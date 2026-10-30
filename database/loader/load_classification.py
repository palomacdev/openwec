"""
OpenWEC — Phase 2, Pass 2: Classification Loader
Reads classification CSVs and populates:
  teams, cars, drivers, results, result_drivers, ingest_log

Strategy:
  - Walks raw/ directories for each series
  - For sessions with hourly snapshots, loads only the final hour
  - Upserts teams and drivers (idempotent)
  - Inserts cars, results and result_drivers

Cars are upserted by identity since #39 (65bdd0e). What follows describes
the current behaviour; `upsert_car` holds the detail.

  The defect this replaced: the insert carried ON CONFLICT DO NOTHING with no
  conflict target against a table with no unique constraint, so no conflict
  was ever possible, every run minted a new car_id, and `results` got a fresh
  row with it, because its UNIQUE (session_id, car_id) is keyed on the id that
  had just changed. A session loaded three times served every position three
  times. 668.273 rows in `cars` over 4.411 distinct identities are what that
  left behind.

  What runs now: the identity is looked up before inserting, over the eight
  columns of CAR_COLUMNS, with NULL treated as a value rather than as unknown,
  and under a transaction-scoped advisory lock keyed on the identity itself.
  Re-loading the same file resolves to the same car and updates its result in
  place instead of adding one.

  Writes stay blocked all the same. The block is not about `upsert_car` any
  more: it is about what `cars` cannot answer. `cars` has no temporal scope,
  so one row is "number + team + class" for all time, and the eight attributes
  cannot tell one season's entry from another's. ADR 0001 and
  docs/competitive-entry-model.md record the measurements that rule out every
  candidate key over this table; `entries` (migration 006) is where the
  identity belongs, and nothing points at it yet. Set
  OPENWEC_ALLOW_DUPLICATING_LOADER=1 to write anyway. --dry-run is always
  allowed.

Usage:
    python database/loader/load_classification.py
    python database/loader/load_classification.py --series WEC
    python database/loader/load_classification.py --series WEC --dry-run
"""

import argparse
import csv
import io
import os
import re
import sys
import psycopg2
from pathlib import Path
from database.db import DB_CONFIG

sys.path.insert(0, str(Path(__file__).resolve().parent))
from row_identity import (  # noqa: E402
    DriverCandidate, DriverOutcome, SessionOutcome,
    forget_driver_index, remember_driver, resolve_driver_id, resolve_session_id,
)


# ── Config ───────────────────────────────────────────────────


RAW_DIRS = {
    "WEC":       Path("raw/wec"),
    "ELMS":      Path("raw/elms"),
    "ALMS":      Path("raw/alms"),
    "LEMANSCUP": Path("raw/lemanscup"),
    "IMSA":      Path("raw/imsa"),
}

VALID_STATUSES = {
    "Classified", "Not Classified", "DNF",
    "DNS", "DSQ", "Retired", "Other"
}


# ── Header dialects ──────────────────────────────────────────
#
# The source publishes classification in two header dialects, and the split
# follows the kind of session rather than the series. Measured over the 3,436
# classification files of the raw tree:
#
#   dialect  files   sessions         columns it uses
#   -------  -----   --------------   -----------------------------------
#   A         1172   almost all race  POSITION  LAPS  TOTAL_TIME  STATUS
#                                     FL_TIME  FL_LAPNUM  FL_KPH
#   B         2352   almost none      POS       LAPS  TIME        LAP  KPH
#
# The pairing is not mechanical, and one case matters more than the others:
# in dialect B, TIME is the best lap of the session, next to the LAP it was
# set on and the KPH it was set at. It is NOT an elapsed total. Mapping it to
# total_time_s would record a lap time as a race duration, so TIME maps to
# fl_time_s, LAP to fl_lap_number and KPH to fl_kph.
#
# TOTAL_TIME and STATUS have no counterpart in dialect B. They are absent at
# the source, not lost in transit, and they stay empty here.
HEADER_ALIASES = {
    "POSITION":  ("POSITION", "POS"),
    "FL_TIME":   ("FL_TIME", "TIME"),
    "FL_LAPNUM": ("FL_LAPNUM", "LAP"),
    "FL_KPH":    ("FL_KPH", "KPH"),
}


def field(row: dict, name: str) -> str:
    """
    The value of `name` in whichever spelling this file uses.

    A spelling that is present in the file wins even when its value is empty,
    so an empty TOTAL_TIME in a dialect-A file never falls through to a TIME
    column that means something else. Only a column that is absent altogether
    lets the next spelling be tried.
    """
    for key in HEADER_ALIASES.get(name, (name,)):
        if key in row:
            value = row.get(key)
            return value.strip() if value else ""
    return ""


def as_int(value: str) -> int | None:
    """
    None only when the source gave nothing, or gave something unreadable.

    Zero is a value the source does mean: a car classified 35th with LAPS=0
    completed no laps, which is not the same as not knowing how many it
    completed. 3,014 classification rows say LAPS=0 and 4,170 say FL_LAPNUM=0.
    """
    value = (value or "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def as_float(value: str) -> float | None:
    """Float counterpart of as_int, with the same treatment of zero."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


# ── Time parsers ─────────────────────────────────────────────

def parse_race_time(s: str) -> float | None:
    """
    Parses total race time to seconds.
    "24:01'55.856"  → 86515.856
    "2:01:25.448"   → 7285.448
    "4:00'20.026"   → 14420.026
    "1:35.605"      → 95.605  (lap time format, fallback)

    The hour is written two ways, and the second one has to be recognised
    before the minute:second branch can see it. `2:01:25.448` read as M:SS
    stops at the second colon and yields 121.0 s — a two-hour race recorded
    as two minutes, with no error raised. `MEASURED` over the 3,539
    classification files of the local tree: 11,123 rows carry TOTAL_TIME in
    H:MM:SS form (8,177 as `H:MM:SS.mmm`, 2,946 as `HH:MM:SS.mmm`).
    """
    if not s or s.strip() in ("-", "", "0"):
        return None
    s = s.strip()
    # Format: H:MM:SS.mmm  or  HH:MM:SS.mmm — tried first, because the
    # M:SS branch below matches its prefix and would truncate it silently.
    m = re.match(r"(\d+):(\d+):([\d.]+)$", s)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    # Format: H:MM'SS.mmm  or  HH:MM'SS.mmm
    m = re.match(r"(\d+):(\d+)'([\d.]+)", s)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    # Format: M'SS.mmm
    m = re.match(r"(\d+)'([\d.]+)", s)
    if m:
        return int(m.group(1)) * 60 + float(m.group(2))
    # Format: M:SS.mmm
    m = re.match(r"(\d+):([\d.]+)", s)
    if m:
        return int(m.group(1)) * 60 + float(m.group(2))
    try:
        return float(s)
    except ValueError:
        return None


def parse_lap_time(s: str) -> float | None:
    """
    Parses lap/sector time to seconds.
    "3'29.208" → 209.208
    "1:35.605" → 95.605
    """
    if not s or s.strip() in ("-", "", "0"):
        return None
    s = s.strip()
    m = re.match(r"(\d+)'([\d.]+)", s)
    if m:
        return int(m.group(1)) * 60 + float(m.group(2))
    m = re.match(r"(\d+):([\d.]+)", s)
    if m:
        return int(m.group(1)) * 60 + float(m.group(2))
    try:
        return float(s)
    except ValueError:
        return None


def parse_gap(s: str) -> float | None:
    """
    Parses gap field.
    "-" or "" → None (first place)
    "1 Lap"   → None (lapped)
    "12.345"  → 12.345
    "+2.167"  → 2.167
    "+1:16.850" → 76.85

    The leading "+" is dropped before parsing. Both time parsers anchor on a
    digit, so a signed gap of more than a minute reached neither of them and
    came back None while the raw string was stored intact. `MEASURED` over
    the 3,539 classification files: 726 rows carry GAP_FIRST in signed
    minute form (`+M:SS.mmm` or `+MM:SS.mmm`). A gap under a minute parsed
    only because `float("+2.167")` accepts the sign.
    """
    if not s or s.strip() in ("-", "", "0"):
        return None
    s = s.strip()
    if "lap" in s.lower():
        return None
    if s.startswith("+"):
        s = s[1:].strip()
        if not s:
            return None
    return parse_lap_time(s) or parse_race_time(s)


CANONICAL_STATUS = {status.lower(): status for status in VALID_STATUSES}


def normalize_status(s: str) -> str:
    """
    The negations are tested before the word they negate.

    "Not classified" contains "classified", so a substring test that asks
    about the positive first answers `Classified` for a car that was not —
    the one reading where a wrong answer is worse than `Other`, because it
    puts a car in the result order that the source excluded from it.
    `MEASURED` over the 3,539 classification files of the local tree: 855
    rows spell it "Not classified", which `VALID_STATUSES` holds only as
    "Not Classified". Matching is therefore case-folded as well.
    """
    if not s:
        return "Other"
    s = s.strip()
    canonical = CANONICAL_STATUS.get(s.lower())
    if canonical:
        return canonical
    low = s.lower()
    if "dns" in low or "not start" in low:
        return "DNS"
    if "not classif" in low or "non classif" in low:
        return "Not Classified"
    if "dnf" in low or "not finish" in low:
        return "DNF"
    if "dsq" in low or "disq" in low:
        return "DSQ"
    if "retired" in low or "retir" in low:
        return "Retired"
    if "classify" in low or "classified" in low:
        return "Classified"
    return "Other"


# ── Driver parsers ────────────────────────────────────────────

def split_wec_driver_name(full_name: str) -> tuple[str, str]:
    """
    WEC format: "Antonio FUOCO" or "Nicklas NIELSEN"
    Returns (first_name, last_name).
    Assumes last word that is all-uppercase = last name.
    """
    if not full_name or not full_name.strip():
        return ("", "")
    parts = full_name.strip().split()
    if len(parts) == 1:
        return ("", parts[0])
    # Last part is typically UPPERCASE last name
    last = parts[-1]
    first = " ".join(parts[:-1])
    return (first.strip(), last.strip())


def extract_drivers_wec(row: dict) -> list[dict]:
    """Extracts up to 5 drivers from WEC/ELMS/ALMS/LMC classification row."""
    drivers = []
    for i in range(1, 6):
        key = f"DRIVER_{i}"
        name = row.get(key, "").strip()
        if not name:
            continue
        first, last = split_wec_driver_name(name)
        drivers.append({
            "slot":        i,
            "first_name":  first,
            "last_name":   last,
            "short_name":  "",
            "country":     "",
            "license":     "",
            "hometown":    "",
            "imsa_driver_id":      None,
            "imsa_driver_plug_id": None,
            "imsa_driver_rating":  None,
        })
    return drivers


def extract_drivers_imsa(row: dict) -> list[dict]:
    """Extracts up to 6 drivers from IMSA classification row."""
    drivers = []
    for i in range(1, 7):
        first = row.get(f"DRIVER{i}_FIRSTNAME", "").strip()
        last  = row.get(f"DRIVER{i}_SECONDNAME", "").strip()
        if not first and not last:
            continue

        rating_raw = row.get(f"DRIVER{i}_IMSA_DriverRatingLong", "").strip()
        rating = rating_raw if rating_raw in ("Platinum", "Gold", "Silver", "Bronze") else None

        try:
            drv_id = int(row.get(f"DRIVER{i}_IMSA_DriverId", "") or 0) or None
        except ValueError:
            drv_id = None

        try:
            plug_id = int(row.get(f"DRIVER{i}_IMSA_DriverPlugId", "") or 0) or None
        except ValueError:
            plug_id = None

        drivers.append({
            "slot":        i,
            "first_name":  first,
            "last_name":   last,
            "short_name":  row.get(f"DRIVER{i}_SHORTNAME", "").strip(),
            "country":     row.get(f"DRIVER{i}_COUNTRY", "").strip(),
            "license":     row.get(f"DRIVER{i}_LICENSE", "").strip(),
            "hometown":    row.get(f"DRIVER{i}_HOMETOWN", "").strip(),
            "imsa_driver_id":      drv_id,
            "imsa_driver_plug_id": plug_id,
            "imsa_driver_rating":  rating,
        })
    return drivers


# The two shapes are told apart by the columns the file has, not by the series
# it was published under. `MEASURED`: the Le Mans Cup 2018 classifications of
# Paul Ricard and Monza spell their driver columns `DRIVER1_FIRSTNAME` /
# `DRIVER1_SECONDNAME` and carry no `DRIVER_1` at all, so choosing the
# extractor by series drops every driver in silence — the car and the result
# load, the names do not. The series is kept only as the tie-break for a row
# that shows neither shape, where there is nothing to read either way.
def driver_column_shape(row: dict) -> str | None:
    if any(f"DRIVER_{i}" in row for i in range(1, 6)):
        return "wec"
    if any(f"DRIVER{i}_FIRSTNAME" in row or f"DRIVER{i}_SECONDNAME" in row
           for i in range(1, 7)):
        return "imsa"
    return None


def extract_drivers(row: dict, series_key: str) -> list[dict]:
    """The drivers of this row, read in whichever shape the row is written."""
    shape = driver_column_shape(row) or ("imsa" if series_key == "IMSA" else "wec")
    return extract_drivers_imsa(row) if shape == "imsa" else extract_drivers_wec(row)


# ── CSV reader ────────────────────────────────────────────────

def read_csv(path: Path) -> list[dict]:
    """Reads semicolon-delimited CSV, strips field names, skips empty rows."""
    raw = path.read_bytes()
    content = None
    for enc in ["utf-8-sig", "utf-8", "latin-1", "cp1252"]:
        try:
            content = raw.decode(enc)
            break
        except UnicodeDecodeError:
            pass
    if content is None:
        return []

    reader = csv.DictReader(io.StringIO(content), delimiter=";")
    rows = []
    for row in reader:
        # Strip field names (Analysis CSVs have spaces: " DRIVER_NUMBER")
        clean = {k.strip(): v.strip() if v else "" for k, v in row.items() if k}
        # Skip empty rows
        if not any(clean.values()):
            continue
        rows.append(clean)
    return rows


# ── Final hour finder ─────────────────────────────────────────

# A classification/ or other/ folder holds exactly four kinds of CSV, and only
# two of them have one row per car:
#
#   03_Classification_*  03_Results*   one row per car — classification
#   23_Analysis_*                      one row per LAP
#   26_Weather*                        one row per weather reading
#
# Anything outside the first family is not a classification, however alone it
# is in its folder: a session with no 03_ file has no published classification,
# and reading its lap log instead turns every lap into a car.
#
# `05_Classification_*` is the same kind of file under the name the publisher
# uses for the 51 sessions that have no `03_` at all. PR #49 verified 182 of
# its URLs: same dialect, same 14 fields, same one row per car, and PR #52
# ran the parser over two of them. It is accepted here so a recovery can
# reach it. No file of this family exists in `raw/`, so what the local tree
# selects today does not change.
#
# The lookahead is not \b: the separator after the word is usually "_", itself
# a word character, and \b would reject 03_Classification_Race.CSV.
CLASSIFICATION_FILE = re.compile(
    r"^(03_(classification|results)|05_classification)(?![a-z0-9])",
    re.IGNORECASE,
)

# Within one hour folder the publisher may offer the same classification under
# two names: `05_Classification_Race.CSV` and `05_Classification_Race_Hour
# 2.CSV`. `MEASURED` over Le Mans Cup 2018, Paul Ricard and Monza: the plain
# name carries the dialect the parser reads — 93 header columns over 73-field
# rows, all 14 fields — and the `_Hour N` variant carries the third dialect,
# 22 columns spelled `Pos;Number;Driver 1;…`, in which `field(row, "NUMBER")`
# finds nothing and every row is discarded. Preferring the plain name is
# preferring the file that can be read at all.
HOUR_SUFFIXED = re.compile(r"_hour\s*\d+\.csv$", re.IGNORECASE)

# "6H", "3 Hours", "1 Hour" — the hour written before the word. A folder has
# to be nothing else; in a file name the mark is the last thing before the
# extension. Both are anchored, because a bare number beside an unrelated
# word is not an hour mark: "Race 1" is a race, not one hour.
HOUR_BEFORE_WORD = re.compile(r"^(\d+)\s*h(ours?)?$", re.IGNORECASE)
HOUR_BEFORE_WORD_SUFFIX = re.compile(r"_(\d+)\s*h(ours?)?$", re.IGNORECASE)


def is_classification_file(path: Path) -> bool:
    return path.suffix.lower() == ".csv" and bool(CLASSIFICATION_FILE.match(path.name))


def get_final_classification(classification_dir: Path) -> Path | None:
    """
    Finds the final classification CSV in a classification/ folder.
    Handles:
      - Direct CSV in folder (no sub-folders)
      - Hourly sub-folders: "Hour 6/", "24_Hour 24/"
    Returns path to the final (highest hour) CSV, or None.

    A folder that holds no classification file yields None. There is no
    fallback to another kind of CSV.
    """
    if not classification_dir.exists():
        return None

    # rglob covers both shapes: the files sitting directly in the folder and
    # the hourly sub-folders. The suffix is matched case-insensitively so the
    # same files are seen on Windows and on the case-sensitive CI runner.
    all_csvs = sorted(
        p for p in classification_dir.rglob("*")
        if p.is_file() and is_classification_file(p)
    )

    if not all_csvs:
        return None

    # If only one, return it
    if len(all_csvs) == 1:
        return all_csvs[0]

    # Sort by hour number extracted from folder or filename
    def hour_key(p: Path) -> int:
        # Try parent folder name: "24_Hour 24", "Hour 6", "Hour 24"
        folder = p.parent.name
        m = re.search(r"[Hh]our\s*(\d+)", folder)
        if m:
            return int(m.group(1))
        # Try filename
        m = re.search(r"[Hh]our\s*(\d+)", p.name)
        if m:
            return int(m.group(1))
        # The publisher also writes the hour before the word, and sometimes
        # abbreviates it: "6H", "3 Hours", "1 Hour". `MEASURED`: the ELMS
        # catalogue uses these spellings for 152 files, and `raw/` holds six
        # such folders. Read only as a whole name, so that a "Race 1" folder
        # or a "24_Hour 24" prefix is not mistaken for an hour mark.
        m = (HOUR_BEFORE_WORD.match(folder)
             or HOUR_BEFORE_WORD_SUFFIX.search(p.stem))
        if m:
            return int(m.group(1))
        # Try numeric prefix in folder: "24_Hour 24" → 24
        m = re.match(r"(\d+)_", folder)
        if m:
            return int(m.group(1))
        return 0

    # Highest hour first; within the same hour, the name without the `_Hour N`
    # suffix; then the first name alphabetically, folded, so the choice does
    # not depend on whether the filesystem sorts case-sensitively. Taking the
    # minimum of a key whose first term is negated keeps the order the
    # previous `max(..., key=hour_key)` produced, where a tie fell to the
    # first file of the sorted list.
    def selection_key(p: Path) -> tuple[int, int, str]:
        suffixed = 1 if HOUR_SUFFIXED.search(p.name) else 0
        return (-hour_key(p), suffixed, p.name.casefold())

    return min(all_csvs, key=selection_key)


# ── DB helpers ────────────────────────────────────────────────

def get_session_id(
    cur,
    series_key: str,
    season_raw: str,
    event_raw: str,
    session_name: str,
    championship_raw: str | None = None,
) -> tuple[int | None, SessionOutcome]:
    """
    Which session row this folder is. See row_identity for what counts as
    proof; a name that several sessions answer to resolves to nothing.
    """
    return resolve_session_id(cur, series_key, season_raw, event_raw,
                              session_name, championship_raw)




def upsert_team(cur, name: str) -> int:
    cur.execute("""
        INSERT INTO teams (name) VALUES (%s)
        ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
        RETURNING id
    """, (name,))
    return cur.fetchone()[0]


# ── cars: the write that used to duplicate ───────────────────
#
# `cars` carries no unique constraint but its own surrogate key, so
# `ON CONFLICT DO NOTHING` without a target never had a conflict to find: the
# insert always succeeded and always minted a new id. `results` is keyed on
# `(session_id, car_id)`, so a fresh car id produced a fresh result row too,
# and every re-run multiplied both tables.
#
# The fix is to look the row up before inserting it, under a lock, rather than
# to declare a uniqueness rule this table cannot carry. ADR 0001 is explicit
# that `cars` has no temporal scope and therefore cannot hold the identity of
# a competitive entry; that identity lives in `entries`, keyed per competition.
# Nothing here decides identity. It only refuses to store the same tuple twice.
#
# The lookup compares every column the loader writes, so two rows merge only
# when the source gave them the same value in all of them. Measured over the
# 3,436 classification files: within a single file, zero pairs of rows share
# this tuple. A rule that cannot merge two rows of one session cannot merge
# two cars the source shows racing together, which is the case ADR 0001
# forbids deciding.
CAR_COLUMNS = (
    "number", "team_id", "vehicle", "car_class",
    "car_group", "tires", "imsa_car_id", "imsa_team_id",
)


def car_identity(number: str, team_id: int, row: dict) -> tuple:
    """The values the loader writes to `cars`, in CAR_COLUMNS order."""
    try:
        imsa_car_id  = int(row.get("IMSA_CarId", "") or 0) or None
        imsa_team_id = int(row.get("IMSA_TeamId", "") or 0) or None
    except ValueError:
        imsa_car_id = imsa_team_id = None

    return (
        number,
        team_id,
        field(row, "VEHICLE") or None,
        field(row, "CLASS") or None,
        field(row, "GROUP") or None,
        (row.get("TYRES") or row.get("TIRES") or "").strip() or None,
        imsa_car_id,
        imsa_team_id,
    )


_CAR_MATCH = " AND ".join(f"{c} IS NOT DISTINCT FROM %s" for c in CAR_COLUMNS)
_CAR_INSERT = (
    f"INSERT INTO cars ({', '.join(CAR_COLUMNS)}) "
    f"VALUES ({', '.join(['%s'] * len(CAR_COLUMNS))}) RETURNING id"
)

def car_identity_lock_key(session_id: int, attrs: tuple) -> str:
    """
    A chave sob a qual uma identidade de carro e serializada, numa sessao.

    Serializar por (sessao, identidade) e nao pela tabela: duas ingestoes de
    carros diferentes, ou do mesmo carro em sessoes diferentes, nunca esperam
    uma pela outra.

    Esta funcao existe para que **nao haja uma segunda chave**. Quem escreve
    em `cars` de um jeito que muda a identidade — a ingestao, que a cria, e a
    fusao de times, que a altera — tem de disputar a mesma, ou o lock de uma
    nao significa nada para a outra.
    """
    return repr((session_id,) + tuple(attrs))


def lock_car_identity(cur, session_id: int, attrs: tuple) -> None:
    """
    Toma o lock daquela identidade naquela sessao, ate o fim da transacao.

    `pg_advisory_xact_lock` bloqueia ate obter, e solta no COMMIT ou no
    ROLLBACK — nao ha o que liberar a mao, e uma falha no meio nao deixa lock
    pendurado.
    """
    cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (car_identity_lock_key(session_id, attrs),))


def car_session_scope(alias: str = "c", marcador: str = "%(sessao)s") -> str:
    """
    What it means for a car to belong to a session: the SQL, written once.

    A car belongs to a session when the session points at it — by a result or
    by a lap — and by nothing else. Sharing eight attributes with a car of
    another season is a description two entries happen to share, not a claim
    that either belongs here.

    This exists as one function because two places need the predicate and two
    copies would drift: `_CAR_IN_SESSION`, which resolves which car an
    ingested row is about, and `recover_classification.identity_twin`, which
    refuses to create a second car answering to one identity. A guard that
    scoped differently from the lookup it guards would be guarding something
    else.

    `alias` is the table alias the caller gave `cars`; `marcador` is the
    placeholder that carries the session id, named or positional. A
    positional caller passes the id **twice**, because the fragment names it
    twice.
    """
    return (f"(EXISTS (SELECT 1 FROM results r "
            f"WHERE r.session_id = {marcador} AND r.car_id = {alias}.id)"
            f" OR EXISTS (SELECT 1 FROM laps l "
            f"WHERE l.session_id = {marcador} AND l.car_id = {alias}.id))")


# The candidates for one session, and whether each already carries that
# session's laps. Scoped twice over: the row must match on every column of
# CAR_COLUMNS, and it must already belong to this session. A car no statement
# of this session points at is not a candidate here, whatever its attributes
# say.
_CAR_IN_SESSION = f"""
    SELECT c.id,
           EXISTS (SELECT 1 FROM laps l
                    WHERE l.session_id = %(sessao)s AND l.car_id = c.id)
      FROM cars c
     WHERE {" AND ".join(f"c.{col} IS NOT DISTINCT FROM %({col})s"
                         for col in CAR_COLUMNS)}
       AND {car_session_scope("c")}
     ORDER BY c.id
"""


class CarIdentityAmbiguous(Exception):
    """
    Two cars of one session carry this identity and both carry laps.

    Choosing one would put the published result on one car and leave the
    other's laps answering to nothing, which is the association this scope
    exists to prevent. `MEASURED` on the corpus: 687 (session, identity)
    groups are in that state, spread over 343 of the 2.902 sessions that
    have a classification, 7 of them contaminated. They are the residue of
    the loader that minted a car per row; nothing written under this scope
    can add to them.
    """


def upsert_car(cur, number: str, team_id: int, row: dict, series_key: str,
               session_id: int) -> int | None:
    """
    The car this row is about, **within this session**: the one already
    stored for it, or a new one.

    Identity is the eight columns of CAR_COLUMNS and nothing else.
    `manufacturer` and `tyre_supplier` are deliberately outside it: they are
    complementary metadata that a source states or omits, never contradicts,
    so a row that carries one and a row that does not are the same car.

    NULL is a value here, not an unknown. Six of the eight columns are
    nullable and most are empty in the corpus — `car_group` in 645.524 rows of
    668.273 — so comparing with `=` would make every one of those rows fail to
    match itself and mint a duplicate. `IS NOT DISTINCT FROM` is the same
    NULLS NOT DISTINCT semantics the competitive-entry model uses, for the
    same reason.

    Why the session scopes it (R2-B)
    --------------------------------
    The eight attributes describe a car; they do not identify an entry.
    `MEASURED` on the corpus: 4.148 of the 4.458 distinct identities are
    carried by entries of more than one session, one of them by 290 —
    #88 Proton Competition on a Porsche 911 RSR answers for Silverstone 2015
    and for Paul Ricard 2019 alike. A lookup that ignores the session
    therefore does not find *this* entry's car; it finds the oldest row that
    happens to be described the same way.

    What that costs is not a wrong attribute, it is a broken join: `results`
    and `laps` are both keyed on (session, car), and every one of the 58.359
    (session, car) pairs that carries a lap has a result on the same row. An
    ingestion that resolved to another session's car would write the result
    against a car the laps do not know.

    Scoping the lookup is the whole change. Nothing is deduplicated, no
    attribute is decided, and `resolve_car_id` — which already scopes the
    lap side by session — is untouched.

    What it does when several rows answer
    -------------------------------------
    Candidates found this way are identical in every column the loader
    writes, so they are duplicates of one identity rather than competing
    entries — the distinction `row_identity.choose_car` already draws.

      one carries this session's laps   that one, whatever its id. 68 groups
                                        have a lap carrier that is **not**
                                        the lowest id, and taking the lowest
                                        would separate the result from the
                                        laps
      none carries laps                 the lowest id: a canonical
                                        representative among rows proven
                                        identical, stable across runs
      several carry laps                refused — see CarIdentityAmbiguous

    Concurrency
    -----------
    The lookup and the insert are serialised per (session, identity): the
    advisory lock below is keyed on both, so two ingestions of different
    cars, or of the same car in different sessions, never wait on each
    other, and two ingestions of the same car in one session cannot both
    find nothing and both insert. The lock is held to the end of the
    transaction, which is where the insert is, so the second holder reads a
    state the first has committed.

    Returns the id, or None if the insert produced no row.
    """
    attrs = car_identity(number, team_id, row)

    # Keyed on the session as well as the attributes: the uniqueness this
    # protects is per session, so serialising more widely would only make
    # unrelated ingestions queue. `normalize_teams` takes the same lock, by
    # the same function, for the identities a merge would change.
    lock_car_identity(cur, session_id, attrs)

    parametros = dict(zip(CAR_COLUMNS, attrs))
    parametros["sessao"] = session_id
    cur.execute(_CAR_IN_SESSION, parametros)
    candidatos = cur.fetchall()

    portadores = [cid for cid, carrega in candidatos if carrega]
    if len(portadores) > 1:
        raise CarIdentityAmbiguous(
            f"o carro {number} da sessao {session_id} tem {len(portadores)} "
            f"identidades iguais carregando voltas ({', '.join(map(str, portadores))}): "
            f"escolher uma separaria o resultado das voltas da outra")
    if portadores:
        return portadores[0]
    if candidatos:
        return candidatos[0][0]      # ORDER BY c.id: o representante canonico

    cur.execute(_CAR_INSERT, attrs)
    inserted = cur.fetchone()
    return inserted[0] if inserted else None


def upsert_driver(cur, d: dict) -> tuple[int | None, DriverOutcome]:
    """
    The driver row this source name belongs to, creating it if it is new.

    Before creating one, the stored names are consulted in their normalised
    form. `drivers` is unique on the raw (first_name, last_name), so a row
    whose name was rewritten afterwards — `normalize_drivers.py` turned
    "Giedo VAN DER"/"GARDE" into "Giedo"/"Van Der Garde" — no longer matches
    the spelling the source still carries, and the loader used to insert a
    second row for the same person. Measured: 1.079 names held by 2.219
    rows, 820 of those groups created in different ingestion windows.

    Answers for all five outcomes, and creates a row for only one of them:

      EXACT_NAME           the row exists under this very spelling; it keeps
                           the update path it always had, so the country and
                           the identifiers the source carries still enrich it
      BY_NORMALIZED_NAME   one row, and only one, carries the same name in
                           normalised form: it is reused and left exactly as
                           it is — not renamed, nothing overwritten, since
                           nothing here establishes which spelling is right
      MULTIPLE_CANDIDATES  several rows carry it. No row is created and none
                           is chosen: adding one would deepen the very
                           duplication this guards against, and choosing one
                           would be choosing by id. The ambiguity is returned
                           and the caller counts it
      NOT_FOUND            no row carries this name in any spelling, so it is
                           a driver this table has not seen: created
      NO_NAME              the source carried no name: nothing is created

    Measured over the classification corpus: 1.935 names resolve exactly,
    162 reuse a row that would otherwise have become a duplicate, 15 are
    created, and 2 are refused.
    """
    encontrado, desfecho = resolve_driver_id(cur, d["first_name"], d["last_name"])

    if desfecho is DriverOutcome.NO_NAME:
        return None, desfecho
    if desfecho is DriverOutcome.BY_NORMALIZED_NAME:
        return encontrado, desfecho
    if desfecho is DriverOutcome.MULTIPLE_CANDIDATES:
        # Creating one more row for a name that already answers to several
        # would make the duplication worse, and picking one of them would be
        # picking by id. The result keeps its other drivers; this one is left
        # unlinked, and counted.
        return None, desfecho

    # EXACT_NAME keeps the update path it always had; NOT_FOUND creates.
    cur.execute("""
        INSERT INTO drivers (
            first_name, last_name, short_name, country,
            license, hometown, imsa_driver_id, imsa_driver_plug_id, imsa_driver_rating
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::driver_rating)
        ON CONFLICT (first_name, last_name) DO UPDATE
            SET short_name          = COALESCE(NULLIF(EXCLUDED.short_name, ''), drivers.short_name),
                country             = COALESCE(NULLIF(EXCLUDED.country, ''), drivers.country),
                imsa_driver_id      = COALESCE(EXCLUDED.imsa_driver_id, drivers.imsa_driver_id),
                imsa_driver_rating  = COALESCE(EXCLUDED.imsa_driver_rating::driver_rating, drivers.imsa_driver_rating)
        RETURNING id
    """, (
        d["first_name"] or "", d["last_name"] or "",
        d["short_name"] or None, d["country"] or None,
        d["license"] or None, d["hometown"] or None,
        d["imsa_driver_id"], d["imsa_driver_plug_id"],
        d["imsa_driver_rating"],
    ))
    gravado = cur.fetchone()[0]
    if desfecho is DriverOutcome.NOT_FOUND:
        remember_driver(
            cur, DriverCandidate(gravado, d["first_name"] or "", d["last_name"] or ""))
    return gravado, desfecho


# ── Provenance ───────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[2]

# The file the loader opened, as a path inside this repository. Not the
# session's `source_url`: that one is the catalogue page the session was
# discovered on, and it is what made a row's origin unrecoverable — every
# result of an event pointed at the same place.
#
# Nor the remote URL the file was downloaded from. `catalog/*/ingest_log.json`
# does record one, but it answers for 874 of the 2.732 classification files
# this loader selects, and 1.480 of its 2.421 destinations carry more than one
# URL, because the IMSA collector fetched the same file from two mirrors into
# the same path. A column whose meaning changed from row to row would be worse
# than one that always says the same thing.
def provenance_of(csv_path: Path) -> str:
    resolved = Path(csv_path).resolve()
    try:
        return resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        # Run from outside the repository: the absolute path is still the
        # file that was read, and still truer than nothing.
        return resolved.as_posix()


# The vocabulary is the schema's own: success, error, skipped. A session the
# loader declined to load is a skip with the reason attached, not an absence.
INGEST_STATUS = {
    "ok":                   ("success", None),
    "empty":                ("skipped", "arquivo de classificacao sem linhas"),
    "no_session_match":     ("skipped", "nenhuma sessao corresponde a este arquivo"),
    "ambiguous_session":    ("skipped", "mais de uma sessao responde a este nome"),
    "contradicted_session": ("skipped", "a unica sessao deste nome pertence a "
                                        "outro campeonato"),
}

# ingested_at is set here, not left to the column default: the default only
# fires on INSERT, and a reingestion that took the UPDATE branch would keep
# claiming the time of the first run.
INGEST_UPSERT = """
    INSERT INTO ingest_log (series_key, source_url, file_type,
                            status, rows_loaded, error_message, ingested_at)
    VALUES (%s::series_name, %s, 'classification', %s, %s, %s, NOW())
    ON CONFLICT (source_url) DO UPDATE
        SET series_key    = EXCLUDED.series_key,
            file_type     = EXCLUDED.file_type,
            status        = EXCLUDED.status,
            rows_loaded   = EXCLUDED.rows_loaded,
            error_message = EXCLUDED.error_message,
            ingested_at   = EXCLUDED.ingested_at
"""


def record_ingestions(cur, registros) -> None:
    """
    One row per file processed, keyed on the file.

    `ingest_log.source_url` is UNIQUE, so a second pass over the same file
    replaces its record instead of adding one. The table keeps the last
    execution of each file, which is what the constraint already decided.
    """
    for registro in registros:
        cur.execute(INGEST_UPSERT, registro)


def insert_result(cur, session_id: int, car_id: int, row: dict,
                  source_url: str | None = None) -> int | None:
    position = as_int(field(row, "POSITION"))
    laps     = as_int(field(row, "LAPS"))
    fl_lap   = as_int(field(row, "FL_LAPNUM"))
    fl_kph   = as_float(field(row, "FL_KPH"))

    # No alias: dialect B carries neither, so these stay empty rather than
    # borrowing a column that means something else.
    total_raw = field(row, "TOTAL_TIME")
    status    = normalize_status(field(row, "STATUS"))

    gap1_raw = field(row, "GAP_FIRST")
    gapp_raw = field(row, "GAP_PREVIOUS")
    fl_raw   = field(row, "FL_TIME")

    cur.execute("""
        INSERT INTO results (
            session_id, car_id, position, status, laps_completed,
            total_time_raw, total_time_s,
            gap_to_first_raw, gap_to_first_s,
            gap_to_prev_raw,  gap_to_prev_s,
            fl_lap_number, fl_time_raw, fl_time_s, fl_kph,
            source_url
        )
        VALUES (%s, %s, %s, %s::entry_status, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        -- Every parsed column, not a subset: with a stable car id this branch
        -- is now the one a re-ingestion takes, and a row left half-refreshed
        -- would mix two readings of the same session.
        ON CONFLICT (session_id, car_id) DO UPDATE
            SET position         = EXCLUDED.position,
                status           = EXCLUDED.status,
                laps_completed   = EXCLUDED.laps_completed,
                total_time_raw   = EXCLUDED.total_time_raw,
                total_time_s     = EXCLUDED.total_time_s,
                gap_to_first_raw = EXCLUDED.gap_to_first_raw,
                gap_to_first_s   = EXCLUDED.gap_to_first_s,
                gap_to_prev_raw  = EXCLUDED.gap_to_prev_raw,
                gap_to_prev_s    = EXCLUDED.gap_to_prev_s,
                fl_lap_number    = EXCLUDED.fl_lap_number,
                fl_time_raw      = EXCLUDED.fl_time_raw,
                fl_time_s        = EXCLUDED.fl_time_s,
                fl_kph           = EXCLUDED.fl_kph,
                source_url       = EXCLUDED.source_url
        RETURNING id
    """, (
        session_id, car_id, position, status, laps,
        total_raw or None, parse_race_time(total_raw),
        gap1_raw or None,  parse_gap(gap1_raw),
        gapp_raw or None,  parse_gap(gapp_raw),
        fl_lap, fl_raw or None, parse_lap_time(fl_raw), fl_kph,
        source_url,
    ))
    row_result = cur.fetchone()
    return row_result[0] if row_result else None


def insert_result_drivers(cur, result_id: int, driver_ids: list[tuple[int, int]]):
    for slot, driver_id in driver_ids:
        cur.execute("""
            INSERT INTO result_drivers (result_id, driver_id, slot)
            VALUES (%s, %s, %s)
            ON CONFLICT (result_id, slot) DO NOTHING
        """, (result_id, driver_id, slot))


# ── Series walker ─────────────────────────────────────────────

def find_classification_sessions(raw_dir: Path, series_key: str) -> list[dict]:
    """
    Walks raw/ directory and returns list of:
      {season_raw, event_raw, session_name, csv_path}
    Only returns the final hour CSV per session.

    A session whose folder holds CSVs but no classification among them is
    reported and left out, rather than loaded from whatever else is there.
    """
    sessions = []
    skipped = []

    if not raw_dir.exists():
        return sessions

    # IMSA has extra championship level:
    # raw/imsa/{season}/{event}/{championship}/{session}/classification/
    is_imsa = (series_key == "IMSA")

    def collect(season_raw: str, event_raw: str, session_dir: Path,
                championship_raw: str | None = None) -> None:
        class_dir = session_dir / "classification"
        if not class_dir.exists():
            class_dir = session_dir / "other"

        csv = get_final_classification(class_dir)
        if csv:
            sessions.append({
                "season_raw":       season_raw,
                "event_raw":        event_raw,
                "session_name":     session_dir.name,
                # Only IMSA's raw/ tree keeps the championship folder, and it
                # is what separates sessions the catalogue gave the same name.
                "championship_raw": championship_raw,
                "csv_path":         csv,
            })
            return

        # Silent only when there was nothing to read at all. A folder that
        # holds CSVs but none of them a classification is the case worth
        # seeing: that is where the lap logs used to be picked up.
        if class_dir.exists() and any(
            p.is_file() and p.suffix.lower() == ".csv" for p in class_dir.rglob("*")
        ):
            skipped.append((season_raw, event_raw, session_dir.name))
            print(f"  [NO CLASSIFICATION] {season_raw} / {event_raw} / {session_dir.name}")

    for season_dir in sorted(raw_dir.iterdir()):
        if not season_dir.is_dir():
            continue
        season_raw = season_dir.name

        for event_dir in sorted(season_dir.iterdir()):
            if not event_dir.is_dir():
                continue
            event_raw = event_dir.name

            if is_imsa:
                # Extra championship level
                for champ_dir in sorted(event_dir.iterdir()):
                    if not champ_dir.is_dir():
                        continue
                    for session_dir in sorted(champ_dir.iterdir()):
                        if not session_dir.is_dir():
                            continue
                        collect(season_raw, event_raw, session_dir, champ_dir.name)
            else:
                for session_dir in sorted(event_dir.iterdir()):
                    if not session_dir.is_dir():
                        continue
                    collect(season_raw, event_raw, session_dir)

    if skipped:
        print(f"  {len(skipped)} sessions have CSVs but no published classification")

    return sessions


# ── Main ─────────────────────────────────────────────────────

def process_session(cur, series_key: str, sess: dict, dry_run: bool) -> dict:
    csv_path     = sess["csv_path"]
    fonte        = provenance_of(csv_path)
    season_raw   = sess["season_raw"]
    event_raw    = sess["event_raw"]
    session_name = sess["session_name"]

    rows = read_csv(csv_path)
    if not rows:
        return {"status": "empty"}

    session_id, outcome = get_session_id(
        cur, series_key, season_raw, event_raw, session_name,
        sess.get("championship_raw"),
    )
    if not session_id:
        # Three different facts, kept apart: the row is absent, the row is
        # there more than once, or the row that is there belongs to another
        # championship than the folder this file sits in.
        if outcome is SessionOutcome.MULTIPLE_CANDIDATES:
            return {"status": "ambiguous_session"}
        if outcome is SessionOutcome.CONTRADICTED_BY_CHAMPIONSHIP:
            return {"status": "contradicted_session"}
        return {"status": "no_session_match"}

    if dry_run:
        return {"status": "ok_dry", "rows": len(rows), "session_id": session_id}

    loaded = 0
    # Drivers left unlinked because the name answers to several rows.
    pilotos_ambiguos = 0

    for row in rows:
        number = row.get("NUMBER", "").strip()
        if not number:
            continue

        team_name = row.get("TEAM", "").strip() or "Unknown"
        team_id   = upsert_team(cur, team_name)
        car_id    = upsert_car(cur, number, team_id, row, series_key, session_id)
        if not car_id:
            continue

        result_id = insert_result(cur, session_id, car_id, row, fonte)
        if not result_id:
            continue

        # Drivers
        drivers = extract_drivers(row, series_key)
        driver_ids = []
        for d in drivers:
            drv_id, drv_outcome = upsert_driver(cur, d)
            if drv_id:
                driver_ids.append((d["slot"], drv_id))
            elif drv_outcome is DriverOutcome.MULTIPLE_CANDIDATES:
                pilotos_ambiguos += 1

        if driver_ids:
            insert_result_drivers(cur, result_id, driver_ids)

        loaded += 1

    if pilotos_ambiguos:
        print(f"  [AMBIGUOUS DRIVER] {pilotos_ambiguos} drivers left unlinked in "
              f"{season_raw} / {event_raw} / {session_name}: the name is held by "
              f"more than one driver row, and nothing says which")

    return {"status": "ok", "rows": len(rows), "loaded": loaded,
            "session_id": session_id, "ambiguous_drivers": pilotos_ambiguos}


OVERRIDE_ENV = "OPENWEC_ALLOW_DUPLICATING_LOADER"


def _writes_are_blocked() -> bool:
    """
    Guard for the window between knowing about the duplication and fixing it.

    `upsert_car` is idempotent since #39, and the block still stands, because
    idempotency and identity are not the same thing. The loader can now avoid
    minting a second row for what it recognises as the same car; what it still
    cannot do is tell one season's entry from another's, since `cars` carries
    no temporal scope and the eight attributes carry no competition.

    Blocking is the conservative choice. Every candidate key measured over
    this table either merges entries the source proves are different or splits
    entries it only spells differently — see ADR 0001. That decision belongs
    to the competitive-entry model, not to this loader.
    """
    return os.environ.get(OVERRIDE_ENV, "").strip() not in {"1", "true", "TRUE", "yes"}


def run(series_filter: str | None = None, dry_run: bool = False):
    if not dry_run and _writes_are_blocked():
        print("=" * 60)
        print("BLOCKED - classification identity is not settled for this table.")
        print("")
        print("  upsert_car resolves a car by its eight attributes and no longer")
        print("  duplicates on re-ingestion. What `cars` still cannot express is")
        print("  WHICH entry a row is: it has no season and no competition, so the")
        print("  same number and team in two seasons is one identity.")
        print("")
        print("  See ADR 0001 and docs/competitive-entry-model.md.")
        print("")
        print("  Use --dry-run to inspect without writing, or set")
        print(f"  {OVERRIDE_ENV}=1 to write anyway.")
        print("=" * 60)
        raise SystemExit(2)

    print("=" * 60)
    print("OpenWEC — Phase 2, Pass 2: Classification Loader")
    print(f"  Dry run: {dry_run}")
    if series_filter:
        print(f"  Series:  {series_filter}")
    print("=" * 60)

    conn = psycopg2.connect(**DB_CONFIG)
    cur  = conn.cursor()
    # A run reads the table for itself: a connection that is still open from
    # an earlier one must not answer with what it folded then.
    forget_driver_index(cur)

    # `rows` is what the files held, `loaded` is what reached the table. They
    # are not the same number — a row with no NUMBER is dropped — and the run
    # used to report only the first, which reads as a count of what was saved.
    total = {"sessions": 0, "ok": 0, "no_match": 0, "ambiguous": 0,
             "contradicted": 0, "empty": 0, "errors": 0, "rows": 0,
             "loaded": 0, "ambiguous_drivers": 0}

    series_to_run = RAW_DIRS
    if series_filter:
        series_to_run = {k: v for k, v in RAW_DIRS.items() if k == series_filter}

    for series_key, raw_dir in series_to_run.items():
        print(f"\n[{series_key}] Scanning {raw_dir}...")
        sessions = find_classification_sessions(raw_dir, series_key)
        print(f"  Found {len(sessions)} classification sessions")

        series_stats = {"ok": 0, "no_match": 0, "ambiguous": 0,
                        "contradicted": 0, "empty": 0, "errors": 0}

        for sess in sessions:
            total["sessions"] += 1
            fonte = provenance_of(sess["csv_path"])
            try:
                result = process_session(cur, series_key, sess, dry_run)
                status = result["status"]

                if not dry_run:
                    registro = INGEST_STATUS.get(status)
                    if registro:
                        situacao, motivo = registro
                        record_ingestions(cur, [(series_key, fonte, situacao,
                                                 result.get("loaded", 0), motivo)])
                    # The session is the unit of work. Its rows and the record
                    # of them land together or not at all, and a failure in the
                    # next one cannot reach back to either.
                    conn.commit()

            except Exception as e:
                series_stats["errors"] += 1
                total["errors"] += 1
                print(f"  [ERROR] {sess['season_raw']}/{sess['event_raw']}/{sess['session_name']}: {e}")
                import traceback; traceback.print_exc()

                # The rollback is what keeps a half-read file out of the table.
                # The record is written after it, in a transaction of its own,
                # which is the only way the failure survives.
                conn.rollback()
                # Drivers written by the rolled-back session are still in the
                # fold this connection keeps in memory, pointing at ids that no
                # longer exist. The next session reads the table again.
                forget_driver_index(cur)
                if not dry_run:
                    try:
                        record_ingestions(
                            cur, [(series_key, fonte, "error", 0, str(e))])
                        conn.commit()
                    except Exception as falha_do_log:   # noqa: BLE001
                        # Recording a failure must not become one. Whatever
                        # state this left the connection in, the next session
                        # starts from a clean transaction.
                        conn.rollback()
                        print(f"  [ERROR] o registro da falha nao pode ser "
                              f"gravado: {falha_do_log}")
                continue

            # Counted after the commit, so the summary says what is in the
            # table rather than what was attempted.
            if status in ("ok", "ok_dry"):
                series_stats["ok"] += 1
                total["ok"] += 1
                total["rows"] += result.get("rows", 0)
                total["loaded"] += result.get("loaded", 0)
                total["ambiguous_drivers"] += result.get("ambiguous_drivers", 0)
            elif status == "no_session_match":
                series_stats["no_match"] += 1
                total["no_match"] += 1
                print(f"  [NO MATCH] {sess['season_raw']} / {sess['event_raw']} / {sess['session_name']}")
            elif status == "ambiguous_session":
                series_stats["ambiguous"] += 1
                total["ambiguous"] += 1
                print(f"  [AMBIGUOUS SESSION] {sess['season_raw']} / {sess['event_raw']} / {sess['session_name']}")
            elif status == "contradicted_session":
                series_stats["contradicted"] += 1
                total["contradicted"] += 1
                print(f"  [CONTRADICTED SESSION] {sess['season_raw']} / {sess['event_raw']} / {sess['session_name']}"
                      f" — the only session of this name belongs to another championship")
            elif status == "empty":
                series_stats["empty"] += 1
                total["empty"] += 1

            if not dry_run and total["ok"] and total["ok"] % 50 == 0:
                print(f"  [{series_key}] {total['ok']} sessions loaded...")

        print(f"  ✓ {series_stats['ok']}  no_match: {series_stats['no_match']}"
              f"  ambiguous: {series_stats['ambiguous']}"
              f"  contradicted: {series_stats['contradicted']}"
              f"  empty: {series_stats['empty']}  errors: {series_stats['errors']}")

    # No final commit: every session committed its own transaction, and a
    # second one here would only hide a write that escaped the loop.
    cur.close()
    conn.close()

    print(f"\n{'=' * 60}")
    print("CLASSIFICATION LOAD COMPLETE")
    print(f"  Sessions processed: {total['sessions']}")
    print(f"  ✓ Loaded:           {total['ok']}")
    print(f"  No DB match:        {total['no_match']}")
    print(f"  Empty files:        {total['empty']}")
    print(f"  Errors:             {total['errors']}")
    print(f"  Rows in the files:  {total['rows']}")
    print(f"  Rows persisted:     {total['loaded']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OpenWEC Classification Loader")
    parser.add_argument("--series",  help="WEC, ELMS, ALMS, LEMANSCUP, IMSA")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(series_filter=args.series, dry_run=args.dry_run)