"""
OpenWEC — Phase 2, Pass 2: Classification Loader
Reads classification CSVs and populates:
  teams, cars, drivers, results, result_drivers

Strategy:
  - Walks raw/ directories for each series
  - For sessions with hourly snapshots, loads only the final hour
  - Upserts teams, cars, drivers (idempotent)
  - Inserts results and result_drivers

Usage:
    python database/loader/load_classification.py
    python database/loader/load_classification.py --series WEC
    python database/loader/load_classification.py --series WEC --dry-run
"""

import argparse
import csv
import io
import re
import psycopg2
from pathlib import Path


# ── Config ───────────────────────────────────────────────────
DB_CONFIG = {
    "host":     "127.0.0.1",
    "port":     5433,
    "dbname":   "openwec",
    "user":     "openwec",
    "password": "openwec",
}

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

def get_final_classification(classification_dir: Path) -> Path | None:
    """
    Finds the final classification CSV in a classification/ folder.
    Handles:
      - Direct CSV in folder (no sub-folders)
      - Hourly sub-folders: "Hour 6/", "24_Hour 24/"
    Returns path to the final (highest hour) CSV, or None.
    """
    if not classification_dir.exists():
        return None

    # Direct CSVs (no sub-folders)
    direct = sorted(classification_dir.glob("*.CSV"))
    if not direct:
        direct = sorted(classification_dir.glob("*.csv"))

    # Sub-folder CSVs (hourly snapshots)
    sub_csvs = list(classification_dir.rglob("*.CSV"))
    if not sub_csvs:
        sub_csvs = list(classification_dir.rglob("*.csv"))

    if not sub_csvs and not direct:
        return None

    all_csvs = sub_csvs or direct

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


def upsert_car(cur, number: str, team_id: int, row: dict, series_key: str) -> int:
    vehicle  = row.get("VEHICLE", "").strip()
    tires    = (row.get("TYRES") or row.get("TIRES") or "").strip()
    class_   = row.get("CLASS", "").strip()
    group_   = row.get("GROUP", "").strip()

    try:
        imsa_car_id  = int(row.get("IMSA_CarId", "") or 0) or None
        imsa_team_id = int(row.get("IMSA_TeamId", "") or 0) or None
    except ValueError:
        imsa_car_id = imsa_team_id = None

    cur.execute("""
        INSERT INTO cars (number, team_id, vehicle, car_class, car_group, tires, imsa_car_id, imsa_team_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
        RETURNING id
    """, (number, team_id, vehicle or None, class_ or None,
          group_ or None, tires or None, imsa_car_id, imsa_team_id))

    row_result = cur.fetchone()
    if row_result:
        return row_result[0]

    # Already exists — fetch it
    cur.execute("""
        SELECT id FROM cars
        WHERE number = %s AND team_id = %s AND car_class = %s
        LIMIT 1
    """, (number, team_id, class_ or None))
    result = cur.fetchone()
    return result[0] if result else None


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
    try:
        position = int(row.get("POSITION", "") or 0) or None
    except ValueError:
        position = None

    try:
        laps = int(row.get("LAPS", "") or 0) or None
    except ValueError:
        laps = None

    try:
        fl_lap = int(row.get("FL_LAPNUM", "") or 0) or None
    except ValueError:
        fl_lap = None

    try:
        fl_kph = float(row.get("FL_KPH", "") or 0) or None
    except ValueError:
        fl_kph = None

    total_raw = row.get("TOTAL_TIME", "").strip()
    gap1_raw  = row.get("GAP_FIRST", "").strip()
    gapp_raw  = row.get("GAP_PREVIOUS", "").strip()
    fl_raw    = row.get("FL_TIME", "").strip()

    status = normalize_status(row.get("STATUS", ""))

    cur.execute("""
        INSERT INTO results (
            session_id, car_id, position, status, laps_completed,
            total_time_raw, total_time_s,
            gap_to_first_raw, gap_to_first_s,
            gap_to_prev_raw,  gap_to_prev_s,
            fl_lap_number, fl_time_raw, fl_time_s, fl_kph
        )
        VALUES (%s, %s, %s, %s::entry_status, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (session_id, car_id) DO UPDATE
            SET position       = EXCLUDED.position,
                status         = EXCLUDED.status,
                laps_completed = EXCLUDED.laps_completed,
                total_time_s   = EXCLUDED.total_time_s,
                fl_time_s      = EXCLUDED.fl_time_s
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
    """
    sessions = []

    if not raw_dir.exists():
        return sessions

    # IMSA has extra championship level:
    # raw/imsa/{season}/{event}/{championship}/{session}/classification/
    is_imsa = (series_key == "IMSA")

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
                        session_name = session_dir.name
                        # class_dir = session_dir / "classification"
                        # csv = get_final_classification(class_dir)
                        class_dir = session_dir / "classification"
                        if not class_dir.exists():
                            class_dir = session_dir / "other"
                        csv = get_final_classification(class_dir)
                        if csv:
                            sessions.append({
                                "season_raw":   season_raw,
                                "event_raw":    event_raw,
                                "session_name": session_name,
                                "csv_path":     csv,
                            })
            else:
                for session_dir in sorted(event_dir.iterdir()):
                    if not session_dir.is_dir():
                        continue
                    session_name = session_dir.name
                    # class_dir = session_dir / "classification"
                    # csv = get_final_classification(class_dir)
                    class_dir = session_dir / "classification"
                    if not class_dir.exists():
                        class_dir = session_dir / "other"
                    csv = get_final_classification(class_dir)
                    if csv:
                        sessions.append({
                            "season_raw":   season_raw,
                            "event_raw":    event_raw,
                            "session_name": session_name,
                            "csv_path":     csv,
                        })

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


def run(series_filter: str | None = None, dry_run: bool = False):
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