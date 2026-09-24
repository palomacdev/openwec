"""
OpenWEC — Schema bootstrap tests

Proves that a database created by the official flow (database/schema.sql alone)
is actually usable by the application.

Run:
    pytest tests/schema/ -v

Requirements:
    pip install pytest psycopg2-binary
    A database with database/schema.sql applied.

Connection comes from the same environment variables the API uses
(DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD).

Every test runs inside a transaction that is rolled back, so nothing is left
behind and no test depends on another having run.
"""
import os

import pytest

psycopg2 = pytest.importorskip("psycopg2", reason="psycopg2 not installed")
import psycopg2.errors  # noqa: E402


DB = {
    "host":     os.environ.get("DB_HOST", "127.0.0.1"),
    "port":     int(os.environ.get("DB_PORT", "5433")),
    "dbname":   os.environ.get("DB_NAME", "openwec"),
    "user":     os.environ.get("DB_USER", "openwec"),
    "password": os.environ.get("DB_PASSWORD", "openwec"),
}


@pytest.fixture
def conn():
    """A connection whose work is always rolled back."""
    try:
        c = psycopg2.connect(connect_timeout=5, **DB)
    except psycopg2.OperationalError as exc:
        pytest.skip(f"No database reachable at {DB['host']}:{DB['port']} — {exc}")
    c.autocommit = False
    try:
        yield c
    finally:
        c.rollback()
        c.close()


@pytest.fixture
def cur(conn):
    with conn.cursor() as c:
        yield c


# Explicit ids in a range nothing else uses.
#
# These tests intentionally use explicit ids so their setup remains independent
# of fixture sequence state and of sequence values consumed by other tests.
_ID = 900_001


def _synthetic_session_and_car(cur):
    """Creates the parent rows a lap needs. Rolled back with the transaction."""
    cur.execute("SELECT id FROM series WHERE key = 'WEC'")
    series_id = cur.fetchone()[0]

    cur.execute(
        "INSERT INTO seasons (id, series_id, raw_id, year, label) "
        "VALUES (%s, %s, 'schema-test', 1900, 'schema test')",
        (_ID, series_id),
    )
    cur.execute(
        "INSERT INTO events (id, season_id, raw_id, name, round) "
        "VALUES (%s, %s, 'schema-test', 'Schema Test Event', 1)",
        (_ID, _ID),
    )
    cur.execute(
        "INSERT INTO sessions (id, event_id, raw_id, name, session_type) "
        "VALUES (%s, %s, 'schema-test', 'Race', 'Race')",
        (_ID, _ID),
    )
    cur.execute(
        "INSERT INTO teams (id, name) VALUES (%s, 'Schema Test Team')",
        (_ID,),
    )
    cur.execute(
        "INSERT INTO cars (id, number, team_id, car_class) "
        "VALUES (%s, '99', %s, 'HYPERCAR')",
        (_ID, _ID),
    )
    return _ID, _ID


# ── laps structure ────────────────────────────────────────────

def test_laps_primary_key_is_id_only(cur):
    """
    The primary key must not include lap_recorded_at.

    A composite PK (id, lap_recorded_at) forces the column NOT NULL, and the
    loader inserts NULL there on every lap — see database/loader/load_laps.py.
    """
    cur.execute("""
        SELECT pg_get_constraintdef(oid)
        FROM pg_constraint
        WHERE conrelid = 'laps'::regclass AND contype = 'p'
    """)
    rows = cur.fetchall()
    assert len(rows) == 1, "laps must have exactly one primary key"
    assert rows[0][0] == "PRIMARY KEY (id)", f"unexpected primary key: {rows[0][0]}"


def test_lap_recorded_at_is_nullable(cur):
    cur.execute("""
        SELECT is_nullable
        FROM information_schema.columns
        WHERE table_name = 'laps' AND column_name = 'lap_recorded_at'
    """)
    row = cur.fetchone()
    assert row is not None, "laps.lap_recorded_at does not exist"
    assert row[0] == "YES", "laps.lap_recorded_at must be nullable"


def test_laps_dedupe_constraint_exists_and_is_named(cur):
    """The loader relies on this constraint for ON CONFLICT DO NOTHING."""
    cur.execute("""
        SELECT conname, pg_get_constraintdef(oid)
        FROM pg_constraint
        WHERE conrelid = 'laps'::regclass AND contype = 'u'
    """)
    rows = cur.fetchall()
    names = {name for name, _ in rows}
    defs = {definition for _, definition in rows}
    assert "laps_session_car_lap_unique" in names, f"constraint missing; found {names}"
    assert "UNIQUE (session_id, car_id, lap_number)" in defs


# ── laps behaviour ────────────────────────────────────────────

def test_lap_with_null_timestamp_can_be_inserted(cur):
    """
    The regression this suite exists for.

    load_laps.py inserts None for lap_recorded_at on every row. With the old
    composite primary key this failed with a NOT NULL violation, which meant a
    database created from schema.sql could not ingest a single lap.
    """
    session_id, car_id = _synthetic_session_and_car(cur)

    cur.execute(
        "INSERT INTO laps (session_id, car_id, lap_number, lap_time_s, lap_recorded_at) "
        "VALUES (%s, %s, 1, 95.123, NULL) RETURNING id",
        (session_id, car_id),
    )
    assert cur.fetchone()[0] is not None

    cur.execute(
        "SELECT lap_recorded_at FROM laps WHERE session_id = %s AND car_id = %s",
        (session_id, car_id),
    )
    assert cur.fetchone()[0] is None


def test_duplicate_lap_is_rejected(cur):
    """Same session, car and lap number must not coexist."""
    session_id, car_id = _synthetic_session_and_car(cur)

    insert = (
        "INSERT INTO laps (session_id, car_id, lap_number, lap_recorded_at) "
        "VALUES (%s, %s, 7, NULL)"
    )
    cur.execute(insert, (session_id, car_id))

    with pytest.raises(psycopg2.errors.UniqueViolation):
        cur.execute(insert, (session_id, car_id))


def test_loader_on_conflict_do_nothing_is_idempotent(cur):
    """
    The loader's ON CONFLICT DO NOTHING has no explicit target, so it depends on
    a unique constraint existing. Without one, re-ingesting duplicates laps.
    """
    session_id, car_id = _synthetic_session_and_car(cur)

    insert = (
        "INSERT INTO laps (session_id, car_id, lap_number, lap_recorded_at) "
        "VALUES (%s, %s, 11, NULL) ON CONFLICT DO NOTHING"
    )
    cur.execute(insert, (session_id, car_id))
    cur.execute(insert, (session_id, car_id))

    cur.execute(
        "SELECT count(*) FROM laps WHERE session_id = %s AND car_id = %s AND lap_number = 11",
        (session_id, car_id),
    )
    assert cur.fetchone()[0] == 1, "ON CONFLICT DO NOTHING did not deduplicate"


# ── api_key_requests ──────────────────────────────────────────

def test_api_key_requests_has_quota_columns(cur):
    """api/deps.py:_lookup_dynamic_key selects these on every dynamic key."""
    cur.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'api_key_requests'
          AND column_name IN ('daily_limit', 'monthly_limit')
    """)
    found = {row[0] for row in cur.fetchall()}
    assert found == {"daily_limit", "monthly_limit"}, f"missing quota columns; found {found}"


def test_dynamic_key_lookup_query_runs(cur):
    """The exact query the API runs for a dynamic key must not fail on schema."""
    cur.execute(
        "SELECT status, requests_per_minute, daily_limit, monthly_limit "
        "FROM api_key_requests WHERE api_key = %s",
        ("schema-test-key-that-does-not-exist",),
    )
    assert cur.fetchone() is None
