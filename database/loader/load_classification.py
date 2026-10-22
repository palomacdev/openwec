"""
OpenWEC — Phase 2, Pass 2: Classification Loader
Reads classification CSVs and populates:
  teams, cars, drivers, results, result_drivers

Strategy:
  - Walks raw/ directories for each series
  - For sessions with hourly snapshots, loads only the final hour
  - Upserts teams and drivers (idempotent)
  - Inserts cars, results and result_drivers

KNOWN DEFECT - cars are NOT upserted idempotently.
  upsert_car issues ON CONFLICT DO NOTHING with no conflict target against a
  table that has no unique constraint, so the insert always succeeds and always
  mints a new car_id. results then gets a fresh row per run, because its
  UNIQUE (session_id, car_id) is keyed on the id that just changed. Re-running
  this loader multiplies the classification: a session loaded three times
  serves every position three times.

  Until the competitive-entry model (migration 006) is wired up, writes are
  blocked. Set OPENWEC_ALLOW_DUPLICATING_LOADER=1 to override and accept the
  duplication. --dry-run is always allowed.

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
import psycopg2
from pathlib import Path
from database.db import DB_CONFIG


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
    "4:00'20.026"   → 14420.026
    "1:35.605"      → 95.605  (lap time format, fallback)
    """
    if not s or s.strip() in ("-", "", "0"):
        return None
    s = s.strip()
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
    """
    if not s or s.strip() in ("-", "", "0"):
        return None
    s = s.strip()
    if "lap" in s.lower():
        return None
    return parse_lap_time(s) or parse_race_time(s)


def normalize_status(s: str) -> str:
    if not s:
        return "Other"
    s = s.strip()
    if s in VALID_STATUSES:
        return s
    low = s.lower()
    if "classify" in low or "classified" in low:
        return "Classified"
    if "dnf" in low or "not finish" in low:
        return "DNF"
    if "dns" in low or "not start" in low:
        return "DNS"
    if "dsq" in low or "disq" in low:
        return "DSQ"
    if "retired" in low or "retir" in low:
        return "Retired"
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
# The lookahead is not \b: the separator after the word is usually "_", itself
# a word character, and \b would reject 03_Classification_Race.CSV.
CLASSIFICATION_FILE = re.compile(r"^03_(classification|results)(?![a-z0-9])", re.IGNORECASE)


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
        # Try numeric prefix in folder: "24_Hour 24" → 24
        m = re.match(r"(\d+)_", folder)
        if m:
            return int(m.group(1))
        return 0

    return max(all_csvs, key=hour_key)


# ── DB helpers ────────────────────────────────────────────────

def get_session_id(cur, series_key: str, season_raw: str, event_raw: str, session_name: str) -> int | None:
    cur.execute("""
        SELECT s.id FROM sessions s
        JOIN events e   ON e.id = s.event_id
        JOIN seasons se ON se.id = e.season_id
        JOIN series sr  ON sr.id = se.series_id
        WHERE sr.key::text = %s
          AND se.raw_id = %s
          AND e.raw_id  = %s
          AND s.name    = %s
        LIMIT 1
    """, (series_key, season_raw, event_raw, session_name))
    row = cur.fetchone()
    return row[0] if row else None


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


def upsert_car(cur, number: str, team_id: int, row: dict, series_key: str) -> int | None:
    attrs = car_identity(number, team_id, row)

    # Two ingestions of the same identity would otherwise both find nothing and
    # both insert. The lock is held to the end of the transaction, which is
    # where the insert is, and is taken on the identity rather than the table,
    # so unrelated rows do not queue behind each other.
    cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (repr(attrs),))

    # IS NOT DISTINCT FROM, not =: most of these columns are nullable, and a
    # NULL never equals itself. This is the NULLS NOT DISTINCT semantics the
    # competitive model uses for the same reason.
    #
    # ORDER BY id is for determinism, not for choosing between candidates: the
    # rows this can match are identical in every column the loader writes, so
    # the choice carries no information. It is not the tie-break among distinct
    # entries that ADR 0001 forbids.
    cur.execute(f"SELECT id FROM cars WHERE {_CAR_MATCH} ORDER BY id LIMIT 1", attrs)
    found = cur.fetchone()
    if found:
        return found[0]

    cur.execute(_CAR_INSERT, attrs)
    inserted = cur.fetchone()
    return inserted[0] if inserted else None


def upsert_driver(cur, d: dict) -> int | None:
    if not d["first_name"] and not d["last_name"]:
        return None

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
    return cur.fetchone()[0]


def insert_result(cur, session_id: int, car_id: int, row: dict) -> int | None:
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
            fl_lap_number, fl_time_raw, fl_time_s, fl_kph
        )
        VALUES (%s, %s, %s, %s::entry_status, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                fl_kph           = EXCLUDED.fl_kph
        RETURNING id
    """, (
        session_id, car_id, position, status, laps,
        total_raw or None, parse_race_time(total_raw),
        gap1_raw or None,  parse_gap(gap1_raw),
        gapp_raw or None,  parse_gap(gapp_raw),
        fl_lap, fl_raw or None, parse_lap_time(fl_raw), fl_kph,
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

    def collect(season_raw: str, event_raw: str, session_dir: Path) -> None:
        class_dir = session_dir / "classification"
        if not class_dir.exists():
            class_dir = session_dir / "other"

        csv = get_final_classification(class_dir)
        if csv:
            sessions.append({
                "season_raw":   season_raw,
                "event_raw":    event_raw,
                "session_name": session_dir.name,
                "csv_path":     csv,
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
                        collect(season_raw, event_raw, session_dir)
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
    season_raw   = sess["season_raw"]
    event_raw    = sess["event_raw"]
    session_name = sess["session_name"]

    rows = read_csv(csv_path)
    if not rows:
        return {"status": "empty"}

    session_id = get_session_id(cur, series_key, season_raw, event_raw, session_name)
    if not session_id:
        return {"status": "no_session_match"}

    if dry_run:
        return {"status": "ok_dry", "rows": len(rows), "session_id": session_id}

    is_imsa = (series_key == "IMSA")
    loaded = 0

    for row in rows:
        number = row.get("NUMBER", "").strip()
        if not number:
            continue

        team_name = row.get("TEAM", "").strip() or "Unknown"
        team_id   = upsert_team(cur, team_name)
        car_id    = upsert_car(cur, number, team_id, row, series_key)
        if not car_id:
            continue

        result_id = insert_result(cur, session_id, car_id, row)
        if not result_id:
            continue

        # Drivers
        drivers = extract_drivers_imsa(row) if is_imsa else extract_drivers_wec(row)
        driver_ids = []
        for d in drivers:
            drv_id = upsert_driver(cur, d)
            if drv_id:
                driver_ids.append((d["slot"], drv_id))

        if driver_ids:
            insert_result_drivers(cur, result_id, driver_ids)

        loaded += 1

    return {"status": "ok", "rows": len(rows), "loaded": loaded, "session_id": session_id}


OVERRIDE_ENV = "OPENWEC_ALLOW_DUPLICATING_LOADER"


def _writes_are_blocked() -> bool:
    """
    Guard for the window between knowing about the duplication and fixing it.

    Blocking is the conservative choice. Repairing idempotency means picking an
    identity rule, and every candidate either merges entries the source proves
    are different or splits entries it only spells differently. That decision
    does not belong in an additive change.
    """
    return os.environ.get(OVERRIDE_ENV, "").strip() not in {"1", "true", "TRUE", "yes"}


def run(series_filter: str | None = None, dry_run: bool = False):
    if not dry_run and _writes_are_blocked():
        print("=" * 60)
        print("BLOCKED - this loader duplicates classification data on every run.")
        print("")
        print("  upsert_car cannot find an existing car, so each run creates new")
        print("  car and result rows. A session loaded N times serves every")
        print("  position N times.")
        print("")
        print("  Use --dry-run to inspect without writing, or set")
        print(f"  {OVERRIDE_ENV}=1 to override and accept the duplication.")
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

    total = {"sessions": 0, "ok": 0, "no_match": 0, "empty": 0, "errors": 0, "rows": 0}

    series_to_run = RAW_DIRS
    if series_filter:
        series_to_run = {k: v for k, v in RAW_DIRS.items() if k == series_filter}

    for series_key, raw_dir in series_to_run.items():
        print(f"\n[{series_key}] Scanning {raw_dir}...")
        sessions = find_classification_sessions(raw_dir, series_key)
        print(f"  Found {len(sessions)} classification sessions")

        series_stats = {"ok": 0, "no_match": 0, "empty": 0, "errors": 0}

        for sess in sessions:
            total["sessions"] += 1
            try:
                result = process_session(cur, series_key, sess, dry_run)
                status = result["status"]

                if status in ("ok", "ok_dry"):
                    series_stats["ok"] += 1
                    total["ok"] += 1
                    total["rows"] += result.get("rows", 0)
                elif status == "no_session_match":
                    series_stats["no_match"] += 1
                    total["no_match"] += 1
                    print(f"  [NO MATCH] {sess['season_raw']} / {sess['event_raw']} / {sess['session_name']}")
                elif status == "empty":
                    series_stats["empty"] += 1
                    total["empty"] += 1

                if not dry_run and status == "ok" and total["ok"] % 50 == 0:
                    conn.commit()
                    print(f"  [{series_key}] {total['ok']} sessions loaded...")

            except Exception as e:
                series_stats["errors"] += 1
                total["errors"] += 1
                conn.rollback()
                print(f"  [ERROR] {sess['season_raw']}/{sess['event_raw']}/{sess['session_name']}: {e}")
                import traceback; traceback.print_exc()

        print(f"  ✓ {series_stats['ok']}  no_match: {series_stats['no_match']}  empty: {series_stats['empty']}  errors: {series_stats['errors']}")

    if not dry_run:
        conn.commit()

    cur.close()
    conn.close()

    print(f"\n{'=' * 60}")
    print("CLASSIFICATION LOAD COMPLETE")
    print(f"  Sessions processed: {total['sessions']}")
    print(f"  ✓ Loaded:           {total['ok']}")
    print(f"  No DB match:        {total['no_match']}")
    print(f"  Empty files:        {total['empty']}")
    print(f"  Errors:             {total['errors']}")
    print(f"  Total rows:         {total['rows']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OpenWEC Classification Loader")
    parser.add_argument("--series",  help="WEC, ELMS, ALMS, LEMANSCUP, IMSA")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(series_filter=args.series, dry_run=args.dry_run)