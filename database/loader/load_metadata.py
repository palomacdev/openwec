"""
OpenWEC — Phase 2, Pass 1: Metadata Loader
Reads catalog/*/sessions.json and populates:
  seasons, events, sessions

Does NOT touch: teams, cars, drivers, results, laps
Those are populated in Pass 2 (classification CSVs).

Usage:
    pip install psycopg2-binary
    python database/loader/load_metadata.py

    # dry run (no DB writes)
    python database/loader/load_metadata.py --dry-run
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

import os
os.environ["PGPASSFILE"] = ""
os.environ["PGSYSCONFDIR"] = ""
os.environ["PGSERVICEFILE"] = ""
import argparse
import json
import re
from datetime import datetime
from pathlib import Path

import psycopg


# ── Config ───────────────────────────────────────────────────
DB_CONFIG = {
    "host":     "127.0.0.1",
    "port":     5433,
    "dbname":   "openwec",
    "user":     "openwec",
    "password": "openwec",
    "client_encoding": "utf8",
}

CATALOG_FILES = {
    "WEC":       Path(r"C:\dev\openwec\catalog\wec\sessions.json"),
    "ELMS":      Path(r"C:\dev\openwec\catalog\elms\sessions.json"),
    "ALMS":      Path(r"C:\dev\openwec\catalog\alms\sessions.json"),
    "LEMANSCUP": Path(r"C:\dev\openwec\catalog\lemanscup\sessions.json"),
    "IMSA":      Path(r"C:\dev\openwec\catalog\imsa\sessions.json"),
}

VALID_SESSION_TYPES = {
    "Race", "Qualifying", "Hyperpole", "Practice",
    "WarmUp", "Test", "Prologue", "Other"
}


# ── Parsers ──────────────────────────────────────────────────

def parse_year(season_raw: str, season_name: str) -> int:
    """
    Extracts the main year from a season.
    "13_2024" → 2024
    "08_2018-2019" → 2018
    "2018-2019" → 2018
    """
    for pattern in [
        r"_(\d{4})-\d{4}",  # 08_2018-2019
        r"_(\d{4})$",        # 13_2024
        r"^(\d{4})",         # 2024
    ]:
        m = re.search(pattern, season_raw)
        if m:
            return int(m.group(1))

    m = re.search(r"(\d{4})", season_name)
    if m:
        return int(m.group(1))

    return 0


def parse_round(event_raw: str) -> int | None:
    """'04_LE MANS' → 4"""
    m = re.match(r"(\d+)_", event_raw)
    return int(m.group(1)) if m else None


def parse_session_at(session_raw: str) -> datetime | None:
    """'202406151000_Race' → datetime(2024, 6, 15, 10, 0)"""
    m = re.match(r"(\d{12})_", session_raw)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y%m%d%H%M")
        except ValueError:
            pass
    return None


def parse_snapshot_hour(session_name: str) -> int | None:
    """'Race Hour 24' → 24, 'Race' → None"""
    m = re.search(r"[Hh]our\s+(\d+)", session_name)
    return int(m.group(1)) if m else None


def normalize_session_type(raw_type: str) -> str:
    if raw_type in VALID_SESSION_TYPES:
        return raw_type
    return "Other"


# ── DB helpers ───────────────────────────────────────────────

def get_series_ids(cur) -> dict[str, int]:
    cur.execute("SELECT key::text, id FROM series")
    return {row[0]: row[1] for row in cur.fetchall()}


def upsert_season(cur, series_id: int, raw_id: str, year: int, label: str) -> int:
    cur.execute("""
        INSERT INTO seasons (series_id, raw_id, year, label)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (series_id, raw_id) DO UPDATE
            SET year = EXCLUDED.year, label = EXCLUDED.label
        RETURNING id
    """, (series_id, raw_id, year, label))
    return cur.fetchone()[0]


def upsert_event(cur, season_id: int, raw_id: str, name: str, round_num: int | None) -> int:
    cur.execute("""
        INSERT INTO events (season_id, raw_id, name, round)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (season_id, raw_id) DO UPDATE
            SET name = EXCLUDED.name, round = EXCLUDED.round
        RETURNING id
    """, (season_id, raw_id, name, round_num))
    return cur.fetchone()[0]


def upsert_session(
    cur,
    event_id: int,
    raw_id: str,
    name: str,
    session_type: str,
    session_at: datetime | None,
    imsa_series: str | None,
    source_url: str | None,
    snapshot_hour: int | None,
) -> int:
    cur.execute("""
        INSERT INTO sessions (
            event_id, raw_id, name, session_type,
            session_at, imsa_series, source_url, snapshot_hour
        )
        VALUES (%s, %s, %s, %s::session_type, %s, %s, %s, %s)
        ON CONFLICT (event_id, raw_id) DO UPDATE
            SET name         = EXCLUDED.name,
                session_type = EXCLUDED.session_type,
                session_at   = EXCLUDED.session_at,
                imsa_series  = EXCLUDED.imsa_series,
                source_url   = EXCLUDED.source_url,
                snapshot_hour = EXCLUDED.snapshot_hour
        RETURNING id
    """, (
        event_id, raw_id, name, session_type,
        session_at, imsa_series or None, source_url, snapshot_hour
    ))
    return cur.fetchone()[0]


# ── Main loader ──────────────────────────────────────────────

def load_series(
    series_key: str,
    sessions: list[dict],
    series_ids: dict[str, int],
    cur,
    dry_run: bool,
) -> dict:
    series_id = series_ids.get(series_key)
    if not series_id:
        print(f"  [SKIP] Series '{series_key}' not found in DB")
        return {"seasons": 0, "events": 0, "sessions": 0}

    stats = {"seasons": 0, "events": 0, "sessions": 0}

    # Cache to avoid redundant upserts
    season_cache: dict[str, int] = {}
    event_cache:  dict[str, int] = {}

    for sess in sessions:
        season_raw  = sess["season_raw"]
        season_name = sess.get("season_name", season_raw)
        event_raw   = sess["event_raw"]
        event_name  = sess.get("event_name", event_raw)
        session_raw = sess["session_raw"]
        session_name = sess.get("session_name", session_raw)
        session_type = normalize_session_type(sess.get("session_type", "Other"))
        imsa_series  = sess.get("imsa_series")

        year  = parse_year(season_raw, season_name)
        label = season_name if season_name else season_raw.split("_", 1)[-1]
        round_num     = parse_round(event_raw)
        session_at    = parse_session_at(session_raw)
        snapshot_hour = parse_snapshot_hour(session_name)

        # Source URL — first CSV file if available
        csv_files = sess.get("csv_files", [])
        source_url = csv_files[0] if csv_files else None

        if dry_run:
            stats["seasons"] += 1 if season_raw not in season_cache else 0
            stats["events"]  += 1 if (season_raw, event_raw) not in event_cache else 0
            stats["sessions"] += 1
            season_cache[season_raw] = 0
            event_cache[(season_raw, event_raw)] = 0
            continue

        # Season
        if season_raw not in season_cache:
            season_id = upsert_season(cur, series_id, season_raw, year, label)
            season_cache[season_raw] = season_id
            stats["seasons"] += 1
        season_id = season_cache[season_raw]

        # Event
        event_key = (season_raw, event_raw)
        if event_key not in event_cache:
            event_id = upsert_event(cur, season_id, event_raw, event_name, round_num)
            event_cache[event_key] = event_id
            stats["events"] += 1
        event_id = event_cache[event_key]

        # Session
        upsert_session(
            cur, event_id, session_raw, session_name,
            session_type, session_at, imsa_series,
            source_url, snapshot_hour,
        )
        stats["sessions"] += 1

    return stats


def run(dry_run: bool = False):
    print("=" * 60)
    print("OpenWEC — Phase 2, Pass 1: Metadata Loader")
    print(f"  Dry run: {dry_run}")
    print("=" * 60)

    conn = None if dry_run else psycopg.connect(**{k: v for k, v in DB_CONFIG.items() if k != 'client_encoding'})    
    cur  = None if dry_run else conn.cursor()

    series_ids = {}
    if not dry_run:
        series_ids = get_series_ids(cur)
        print(f"\n  Series in DB: {series_ids}\n")

    total = {"seasons": 0, "events": 0, "sessions": 0}

    for series_key, catalog_path in CATALOG_FILES.items():
        if not catalog_path.exists():
            print(f"  [SKIP] {catalog_path} not found")
            continue

        with open(catalog_path, encoding="utf-8") as f:
            sessions = json.load(f)

        print(f"  [{series_key}] {len(sessions)} sessions in catalog")

        stats = load_series(series_key, sessions, series_ids, cur, dry_run)

        print(f"    seasons:  {stats['seasons']}")
        print(f"    events:   {stats['events']}")
        print(f"    sessions: {stats['sessions']}")

        for k in total:
            total[k] += stats[k]

    if not dry_run:
        conn.commit()
        cur.close()
        conn.close()

    print(f"\n{'=' * 60}")
    print("METADATA LOAD COMPLETE")
    print(f"  Total seasons:  {total['seasons']}")
    print(f"  Total events:   {total['events']}")
    print(f"  Total sessions: {total['sessions']}")
    if dry_run:
        print("  [DRY RUN] No data written to DB")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OpenWEC Metadata Loader")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(dry_run=args.dry_run)