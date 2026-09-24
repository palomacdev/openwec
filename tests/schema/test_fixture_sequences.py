"""
OpenWEC — Fixture sequence tests

tests/fixtures.sql inserts rows with explicit ids. That does not advance the
SERIAL sequences, so without an explicit sync the next insert that omits id
starts at 1 and collides with the fixture rows.

These tests prove the sync block at the end of fixtures.sql actually works.

Run:
    pytest tests/schema/ -v

Connection comes from DB_HOST / DB_PORT / DB_NAME / DB_USER / DB_PASSWORD.
Everything runs inside a transaction that is rolled back.
"""
import os

import pytest

psycopg2 = pytest.importorskip("psycopg2", reason="psycopg2 not installed")


DB = {
    "host":     os.environ.get("DB_HOST", "127.0.0.1"),
    "port":     int(os.environ.get("DB_PORT", "5433")),
    "dbname":   os.environ.get("DB_NAME", "openwec"),
    "user":     os.environ.get("DB_USER", "openwec"),
    "password": os.environ.get("DB_PASSWORD", "openwec"),
}

# Every table tests/fixtures.sql inserts into with an explicit id.
# result_drivers is absent on purpose: its insert omits id, so its sequence
# advances normally and never desynchronises.
TABELAS_COM_ID_EXPLICITO = [
    "series",
    "seasons",
    "events",
    "sessions",
    "teams",
    "drivers",
    "cars",
    "results",
]


@pytest.fixture
def cur():
    try:
        conn = psycopg2.connect(connect_timeout=5, **DB)
    except psycopg2.OperationalError as exc:
        pytest.skip(f"No database reachable at {DB['host']}:{DB['port']} — {exc}")
    conn.autocommit = False
    try:
        with conn.cursor() as c:
            yield c
    finally:
        conn.rollback()
        conn.close()


def _proximo_valor(cur, tabela):
    """The id the next insert without an explicit id would get."""
    cur.execute("SELECT pg_get_serial_sequence(%s, 'id')", (tabela,))
    seq = cur.fetchone()[0]
    assert seq is not None, f"{tabela}.id has no sequence"
    cur.execute(f"SELECT last_value, is_called FROM {seq}")
    last_value, is_called = cur.fetchone()
    return (last_value + 1) if is_called else last_value


def _max_id(cur, tabela):
    cur.execute(f"SELECT COALESCE(MAX(id), 0) FROM {tabela}")
    return cur.fetchone()[0]


@pytest.mark.parametrize("tabela", TABELAS_COM_ID_EXPLICITO)
def test_sequence_is_ahead_of_existing_rows(cur, tabela):
    """
    The invariant that matters: the next generated id must not already be taken.

    This is what fails when fixtures.sql inserts explicit ids without setval.
    """
    proximo = _proximo_valor(cur, tabela)
    maximo = _max_id(cur, tabela)
    assert proximo > maximo, (
        f"{tabela}: next id would be {proximo}, but id {maximo} already exists — "
        "the sequence is behind the data"
    )


@pytest.mark.parametrize("tabela", TABELAS_COM_ID_EXPLICITO)
def test_sequence_was_actually_synced(cur, tabela):
    """
    With fixture rows loaded, the sequence must have been moved past them.
    A sequence still sitting at 1 means the sync block did not run.

    The assertion is >= rather than == on purpose: sequences are not
    transactional, so any nextval that already happened — by another test, or
    by the application — leaves the sequence further ahead and must not be
    read as a failure. What the sync guarantees is the lower bound.
    """
    maximo = _max_id(cur, tabela)
    if maximo == 0:
        pytest.skip(f"{tabela} is empty — fixtures not loaded, nothing to prove")
    proximo = _proximo_valor(cur, tabela)
    assert proximo >= maximo + 1, (
        f"{tabela}: next id would be {proximo} but id {maximo} exists — "
        "the sync block did not run"
    )


def test_insert_without_explicit_id_does_not_collide_teams(cur):
    """Behavioural proof on a table whose insert needs only one column."""
    antes = _max_id(cur, "teams")
    cur.execute(
        "INSERT INTO teams (name) VALUES ('Sequence Test Team') RETURNING id"
    )
    novo = cur.fetchone()[0]
    assert novo > antes, f"new id {novo} is not above the existing maximum {antes}"


def test_insert_without_explicit_id_does_not_collide_drivers(cur):
    antes = _max_id(cur, "drivers")
    cur.execute(
        "INSERT INTO drivers (first_name, last_name, country) "
        "VALUES ('Sequence', 'Test', 'BRA') RETURNING id"
    )
    novo = cur.fetchone()[0]
    assert novo > antes, f"new id {novo} is not above the existing maximum {antes}"
