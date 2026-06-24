"""
OpenWEC — Phase 2, Pass 3: Laps Loader
Reads Analysis CSVs (lap-by-lap) and populates the laps table.

Available for: WEC, ELMS, ALMS, Le Mans Cup
Not available for: IMSA (does not publish analysis files)

CSV fields (WEC = ELMS = ALMS = LEMANSCUP):
    NUMBER; DRIVER_NUMBER; LAP_NUMBER; LAP_TIME; LAP_IMPROVEMENT;
    CROSSING_FINISH_LINE_IN_PIT; S1; S2; S3; KPH; ELAPSED; HOUR;
    S1_LARGE; S2_LARGE; S3_LARGE; TOP_SPEED; DRIVER_NAME; PIT_TIME;
    CLASS; GROUP; TEAM; MANUFACTURER; FLAG_AT_FL;
    S1_SECONDS; S2_SECONDS; S3_SECONDS

Usage:
    python database/loader/load_laps.py
    python database/loader/load_laps.py --series WEC
    python database/loader/load_laps.py --series WEC --dry-run
    python database/loader/load_laps.py --series WEC --session-type Race
"""

import argparse
import csv
import io
import re
import psycopg2
from psycopg2.extras import execute_values
from pathlib import Path
from database.db import DB_CONFIG


# ── Config ───────────────────────────────────────────────────


RAW_DIRS = {
    "WEC":       Path("raw/wec"),
    "ELMS":      Path("raw/elms"),
    "ALMS":      Path("raw/alms"),
    "LEMANSCUP": Path("raw/lemanscup"),
}

VALID_FLAGS = {"GF", "SC", "FCY", "YF", "RF"}


# ── Parsers ──────────────────────────────────────────────────

def parse_lap_time(s: str) -> float | None:
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


def parse_float(s: str) -> float | None:
    if not s or s.strip() in ("-", "", "0"):
        return None
    try:
        return float(s.strip())
    except ValueError:
        return None


def parse_int(s: str) -> int | None:
    if not s or s.strip() in ("-", ""):
        return None
    try:
        return int(s.strip())
    except ValueError:
        return None


def normalize_flag(s: str) -> str | None:
    if not s:
        return None
    s = s.strip().upper()
    if s in VALID_FLAGS:
        return s
    return "Other" if s else None


def parse_driver_name(full_name: str) -> tuple[str, str]:
    """'Patrick PILET' → ('Patrick', 'PILET')"""
    if not full_name or not full_name.strip():
        return ("", "")
    parts = full_name.strip().split()
    if len(parts) == 1:
        return ("", parts[0])
    return (" ".join(parts[:-1]), parts[-1])


# ── CSV reader ────────────────────────────────────────────────

def read_csv(path: Path) -> list[dict]:
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
        clean = {k.strip(): v.strip() if v else "" for k, v in row.items() if k}
        if not any(clean.values()):
            continue
        rows.append(clean)
    return rows


# ── Final analysis finder ─────────────────────────────────────

def get_final_analysis(analysis_dir: Path) -> Path | None:
    """
    Finds the final (highest hour) analysis CSV.
    Same logic as classification — picks max hour subfolder.
    """
    if not analysis_dir.exists():
        return None
    
    # For others
    all_csvs = list(analysis_dir.rglob("*.CSV"))
    if not all_csvs:
        all_csvs = list(analysis_dir.rglob("*.csv"))
    if not all_csvs:
        return None

    if len(all_csvs) == 1:
        return all_csvs[0]

    def hour_key(p: Path) -> int:
        for text in [p.parent.name, p.name]:
            m = re.search(r"[Hh]our\s*(\d+)", text)
            if m:
                return int(m.group(1))
            m = re.match(r"(\d+)_", text)
            if m:
                return int(m.group(1))
        return 0

    return max(all_csvs, key=hour_key)

    # Only analysis files (prefix 23_) ALMS 
    # all_csvs = [
    #     p for p in analysis_dir.rglob("*.CSV")
    #     if p.name.lower().startswith("23_")
    # ]
    # if not all_csvs:
    #     all_csvs = [
    #         p for p in analysis_dir.rglob("*.csv")
    #         if p.name.lower().startswith("23_")
    #     ]
    # if not all_csvs:
    #     return None

    # if len(all_csvs) == 1:
    #     return all_csvs[0]

    # def hour_key(p: Path) -> int:
    #     for text in [p.parent.name, p.name]:
    #         m = re.search(r"[Hh]our\s*(\d+)", text)
    #         if m:
    #             return int(m.group(1))
    #         m = re.match(r"(\d+)_", text)
    #         if m:
    #             return int(m.group(1))
    #     return 0

    # return max(all_csvs, key=hour_key)


# ── DB lookups ────────────────────────────────────────────────

def get_session_id(cur, series_key: str, season_raw: str, event_raw: str, session_name: str) -> int | None:
    print(f"    LOOKUP: series={series_key} season={season_raw} event={event_raw} session={session_name}")

    cur.execute("""
        SELECT s.id FROM sessions s
        JOIN events e   ON e.id = s.event_id
        JOIN seasons se ON se.id = e.season_id
        JOIN series sr  ON sr.id = se.series_id
        WHERE sr.key::text = %s
          AND se.raw_id    = %s
          AND e.raw_id     = %s
          AND s.name       = %s
        LIMIT 1
    """, (series_key, season_raw, event_raw, session_name))
    row = cur.fetchone()
    return row[0] if row else None


def get_car_id(cur, session_id: int, car_number: str) -> int | None:
    """Find car by session + number via results table."""
    cur.execute("""
        SELECT c.id FROM cars c
        JOIN results r ON r.car_id = c.id
        WHERE r.session_id = %s AND c.number = %s
        LIMIT 1
    """, (session_id, car_number))
    row = cur.fetchone()
    return row[0] if row else None


def get_driver_id(cur, first_name: str, last_name: str) -> int | None:
    if not first_name and not last_name:
        return None
    cur.execute("""
        SELECT id FROM drivers
        WHERE first_name = %s AND last_name = %s
        LIMIT 1
    """, (first_name, last_name))
    row = cur.fetchone()
    return row[0] if row else None


# ── Session finder ────────────────────────────────────────────

def find_analysis_sessions(raw_dir: Path) -> list[dict]:
    """
    Walks raw/ and finds all analysis CSVs.
    Returns list of {season_raw, event_raw, session_name, csv_path}.
    """
    sessions = []
    if not raw_dir.exists():
        return sessions

    for season_dir in sorted(raw_dir.iterdir()):
        if not season_dir.is_dir():
            continue
        season_raw = season_dir.name

        for event_dir in sorted(season_dir.iterdir()):
            if not event_dir.is_dir():
                continue
            event_raw = event_dir.name

            for session_dir in sorted(event_dir.iterdir()):
                if not session_dir.is_dir():
                    continue
                session_name = session_dir.name
                print(f"  DISK: season={season_raw} event={event_raw} session='{session_name}'")

                analysis_dir = session_dir / "analysis"
                if not analysis_dir.exists():
                    analysis_dir = session_dir / "other"
                csv = get_final_analysis(analysis_dir)
                if csv:
                    sessions.append({
                        "season_raw":   season_raw,
                        "event_raw":    event_raw,
                        "session_name": session_name,
                        "csv_path":     csv,
                    })

    return sessions


# ── Row builder ───────────────────────────────────────────────

def build_lap_row(
    session_id: int,
    car_id: int,
    driver_id: int | None,
    row: dict,
) -> tuple | None:
    lap_num = parse_int(row.get("LAP_NUMBER", ""))
    if lap_num is None:
        return None

    # Sectors — prefer _SECONDS (float) over formatted string
    s1 = parse_float(row.get("S1_SECONDS")) or parse_lap_time(row.get("S1_LARGE") or row.get("S1"))
    s2 = parse_float(row.get("S2_SECONDS")) or parse_lap_time(row.get("S2_LARGE") or row.get("S2"))
    s3 = parse_float(row.get("S3_SECONDS")) or parse_lap_time(row.get("S3_LARGE") or row.get("S3"))

    lap_time = parse_lap_time(row.get("LAP_TIME", ""))
    kph       = parse_float(row.get("KPH", ""))
    top_speed = parse_float(row.get("TOP_SPEED", ""))
    pit_time  = parse_lap_time(row.get("PIT_TIME", ""))

    improvement = row.get("LAP_IMPROVEMENT", "").strip() == "1"
    in_pit      = bool(row.get("CROSSING_FINISH_LINE_IN_PIT", "").strip())

    flag_raw = row.get("FLAG_AT_FL", "").strip()
    flag     = normalize_flag(flag_raw)

    elapsed_raw = row.get("ELAPSED", "").strip() or None
    hour_raw    = row.get("HOUR", "").strip() or None

    driver_slot = parse_int(row.get("DRIVER_NUMBER", ""))

    return (
        session_id,
        car_id,
        driver_id,
        driver_slot,
        lap_num,
        lap_time,
        s1, s2, s3,
        kph,
        top_speed,
        elapsed_raw,
        hour_raw,
        improvement,
        in_pit,
        flag,
        pit_time,
        None,  # lap_recorded_at — populated later if needed
    )


# ── Main ─────────────────────────────────────────────────────

INSERT_SQL = """
    INSERT INTO laps (
        session_id, car_id, driver_id, driver_slot,
        lap_number, lap_time_s,
        s1_s, s2_s, s3_s,
        kph, top_speed_kph,
        elapsed_raw, hour_raw,
        lap_improvement, crossing_finish_in_pit,
        flag_at_fl, pit_time_s,
        lap_recorded_at
    ) VALUES %s
    ON CONFLICT DO NOTHING
"""


def process_session(cur, series_key: str, sess: dict, dry_run: bool) -> dict:
    print(f"    PROCESS: {sess['season_raw']}/{sess['event_raw']}/{sess['session_name']} → csv={sess['csv_path'].name}")
    csv_path     = sess["csv_path"]
    season_raw   = sess["season_raw"]
    event_raw    = sess["event_raw"]
    session_name = sess["session_name"]

    rows = read_csv(csv_path)
    if not rows:
        return {"status": "empty"}

    session_id = get_session_id(cur, series_key, season_raw, event_raw, session_name)
    print(f"    SESSION_ID: {session_id}")
    if not session_id:
        return {"status": "no_session_match"}

    if dry_run:
        return {"status": "ok_dry", "rows": len(rows)}

    lap_rows = []
    skipped  = 0

    for row in rows:
        car_number = row.get("NUMBER", "").strip()
        if not car_number:
            skipped += 1
            continue

        car_id = get_car_id(cur, session_id, car_number)
        if not car_id:
            skipped += 1
            continue

        # Resolve driver from name
        driver_id = None
        driver_name = row.get("DRIVER_NAME", "").strip()
        if driver_name:
            first, last = parse_driver_name(driver_name)
            driver_id = get_driver_id(cur, first, last)

        lap_row = build_lap_row(session_id, car_id, driver_id, row)
        if lap_row:
            lap_rows.append(lap_row)

    if lap_rows:
        execute_values(cur, INSERT_SQL, lap_rows)

    return {
        "status":  "ok",
        "rows":    len(rows),
        "loaded":  len(lap_rows),
        "skipped": skipped,
    }


def run(
    series_filter: str | None = None,
    session_type_filter: str | None = None,
    dry_run: bool = False,
):
    print("=" * 60)
    print("OpenWEC — Phase 2, Pass 3: Laps Loader")
    print(f"  Dry run: {dry_run}")
    if series_filter:
        print(f"  Series:  {series_filter}")
    if session_type_filter:
        print(f"  Session: {session_type_filter}")
    print("=" * 60)

    conn = psycopg2.connect(**DB_CONFIG)
    cur  = conn.cursor()

    total = {
        "sessions": 0, "ok": 0, "no_match": 0,
        "empty": 0, "errors": 0, "laps": 0,
    }

    series_to_run = RAW_DIRS
    if series_filter:
        series_to_run = {k: v for k, v in RAW_DIRS.items() if k == series_filter}

    for series_key, raw_dir in series_to_run.items():
        print(f"\n[{series_key}] Scanning {raw_dir}...")
        sessions = find_analysis_sessions(raw_dir)

        # Optional filter by session type name
        if session_type_filter:
            sessions = [
                s for s in sessions
                if session_type_filter.lower() in s["session_name"].lower()
            ]

        print(f"  Found {len(sessions)} analysis sessions")
        series_stats = {"ok": 0, "no_match": 0, "empty": 0, "errors": 0, "laps": 0}

        for sess in sessions:
            total["sessions"] += 1
            try:
                result = process_session(cur, series_key, sess, dry_run)
                status = result["status"]

                if status in ("ok", "ok_dry"):
                    series_stats["ok"] += 1
                    total["ok"] += 1
                    laps = result.get("loaded", result.get("rows", 0))
                    series_stats["laps"] += laps
                    total["laps"] += laps
                elif status == "no_session_match":
                    series_stats["no_match"] += 1
                    total["no_match"] += 1
                    print(f"  [NO MATCH] {season_raw}/{event_raw}/{session_name}")
                elif status == "empty":
                    series_stats["empty"] += 1
                    total["empty"] += 1

                if not dry_run and total["ok"] % 100 == 0 and total["ok"] > 0:
                    conn.commit()
                    print(f"  [{series_key}] {total['ok']} sessions, {total['laps']:,} laps...")

            except Exception as e:
                series_stats["errors"] += 1
                total["errors"] += 1
                conn.rollback()
                print(f"  [ERROR] {sess['season_raw']}/{sess['event_raw']}/{sess['session_name']}: {e}")

        print(f"  ✓ {series_stats['ok']}  laps: {series_stats['laps']:,}  "
              f"no_match: {series_stats['no_match']}  errors: {series_stats['errors']}")

    if not dry_run:
        conn.commit()

    cur.close()
    conn.close()

    print(f"\n{'=' * 60}")
    print("LAPS LOAD COMPLETE")
    print(f"  Sessions processed: {total['sessions']}")
    print(f"  ✓ Loaded:           {total['ok']}")
    print(f"  Total laps:         {total['laps']:,}")
    print(f"  No DB match:        {total['no_match']}")
    print(f"  Empty files:        {total['empty']}")
    print(f"  Errors:             {total['errors']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OpenWEC Laps Loader")
    parser.add_argument("--series",       help="WEC, ELMS, ALMS, LEMANSCUP")
    parser.add_argument("--session-type", help="Filter by session name (e.g. Race)")
    parser.add_argument("--dry-run",      action="store_true")
    args = parser.parse_args()
    run(
        series_filter=args.series,
        session_type_filter=args.session_type,
        dry_run=args.dry_run,
    )