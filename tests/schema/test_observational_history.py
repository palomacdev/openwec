"""
Observational history of class and number (migration 010).

Schema and constraint behaviour only. The tables are created empty by the
migration and stay empty outside these tests; nothing here populates real data,
consolidates entries or decides identity.

Writes, so it runs only where the database is declared disposable.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

psycopg2 = pytest.importorskip("psycopg2")
import psycopg2.errors  # noqa: E402

DB = {
    "host":     os.environ.get("DB_HOST", "127.0.0.1"),
    "port":     int(os.environ.get("DB_PORT", "5440")),
    "dbname":   os.environ.get("DB_NAME", "p31chk"),
    "user":     os.environ.get("DB_USER", "testuser"),
    "password": os.environ.get("DB_PASSWORD", ""),
}

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
FILE_PROVISIONAL = "imsa/18_2018/10_x/Race/classification/03_Results_Race_Provisional.CSV"
FILE_OFFICIAL = "imsa/18_2018/10_x/Race/classification/03_Results_Race_Official.CSV"

# Ids in a range nothing else uses, for the same reason the other suites do it.
_ID = 920_001


@pytest.fixture
def conn(disposable_db):
    try:
        c = psycopg2.connect(connect_timeout=5, **DB)
    except psycopg2.OperationalError as exc:
        pytest.skip(f"sem banco em {DB['host']}:{DB['port']} — {exc}")
    c.autocommit = False
    c2 = c.cursor()
    c2.execute("SELECT to_regclass('public.entry_class_history')")
    if c2.fetchone()[0] is None:
        c.rollback()
        c.close()
        pytest.skip("migration 010 nao aplicada")
    try:
        yield c
    finally:
        c.rollback()
        c.close()


@pytest.fixture
def cur(conn):
    with conn.cursor() as c:
        yield c


def _entry(cur):
    """An entry and a session to hang observations on. Rolled back."""
    cur.execute("SELECT id FROM series WHERE key = 'WEC'")
    series_id = cur.fetchone()[0]
    cur.execute("INSERT INTO seasons (id, series_id, raw_id, year, label) "
                "VALUES (%s, %s, 'obs-hist-test', 1902, 'obs hist')",
                (_ID, series_id))
    cur.execute("INSERT INTO events (id, season_id, raw_id, name, round) "
                "VALUES (%s, %s, 'obs-hist-test', 'Obs Hist Event', 1)", (_ID, _ID))
    cur.execute("INSERT INTO sessions (id, event_id, raw_id, name) "
                "VALUES (%s, %s, 'obs-hist-session', 'Race')", (_ID, _ID))
    cur.execute("INSERT INTO competitions (id, event_id, label, resolution) "
                "VALUES (%s, %s, 'obs-hist', 'resolved')", (_ID, _ID))
    cur.execute("INSERT INTO entries (id, competition_id, event_id, number, car_class, "
                "identity_status) VALUES (%s, %s, %s, '7', 'LMP2', 'resolved')",
                (_ID, _ID, _ID))
    return _ID, _ID


def observe_class(cur, entry_id, session_id, *, car_class="LMP2",
                  source_file=FILE_PROVISIONAL, digest=DIGEST_A, line=2, **kw):
    cur.execute(
        "INSERT INTO entry_class_history (entry_id, session_id, car_class, "
        "source_file, source_digest, source_line, source_observed_at, "
        "source_time_basis) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (entry_id, session_id, car_class, source_file, digest, line,
         kw.get("source_observed_at"), kw.get("source_time_basis", "none")))
    return cur.fetchone()[0]


def observe_number(cur, entry_id, session_id, *, number="7",
                   source_file=FILE_PROVISIONAL, digest=DIGEST_A, line=2, **kw):
    cur.execute(
        "INSERT INTO entry_number_history (entry_id, session_id, number_as_written, "
        "source_file, source_digest, source_line, source_observed_at, "
        "source_time_basis) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (entry_id, session_id, number, source_file, digest, line,
         kw.get("source_observed_at"), kw.get("source_time_basis", "none")))
    return cur.fetchone()[0]


# ── shape ─────────────────────────────────────────────────────────────────

def test_migration_objects_exist(cur):
    cur.execute("SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name IN "
                "('entry_class_history','entry_number_history')")
    assert {r[0] for r in cur.fetchall()} == {"entry_class_history",
                                             "entry_number_history"}
    cur.execute("SELECT array_agg(e.enumlabel::text ORDER BY e.enumsortorder) "
                "FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid "
                "WHERE t.typname = 'source_time_basis'")
    assert cur.fetchone()[0] == ["none", "file_published", "row_timestamp"]


def test_tabelas_nascem_vazias(cur):
    """
    The migration creates and does not populate. Scoped past this suite's own
    rows, which are rolled back anyway.
    """
    for table in ("entry_class_history", "entry_number_history"):
        cur.execute(f"SELECT count(*) FROM {table}")
        assert cur.fetchone()[0] == 0, f"{table} nao esta vazia"


@pytest.mark.parametrize("table", ["entry_class_history", "entry_number_history"])
def test_indices_sem_redundancia(cur, table):
    """
    No index on (entry_id) alone: the UNIQUE constraint already indexes it as
    a leading column. entry_team_history carries that redundancy today and this
    does not repeat it.
    """
    cur.execute("SELECT indexname, indexdef FROM pg_indexes "
                "WHERE tablename = %s ORDER BY indexname", (table,))
    found = {name: definition for name, definition in cur.fetchall()}
    assert any("entry_id, source_file, source_digest, source_line" in d
               for d in found.values()), found
    redundant = [n for n, d in found.items()
                 if d.rstrip(")").endswith("(entry_id")]
    assert not redundant, f"indice redundante em (entry_id): {redundant}"
    assert any(d.rstrip(")").endswith("(session_id") for d in found.values()), found
    assert any(d.rstrip(")").endswith("(source_digest") for d in found.values()), found


# ── validation of the source reference ────────────────────────────────────

@pytest.mark.parametrize("bad_line", [0, -1])
def test_source_line_deve_ser_positiva(cur, bad_line):
    entry_id, session_id = _entry(cur)
    with pytest.raises(psycopg2.errors.CheckViolation):
        observe_class(cur, entry_id, session_id, line=bad_line)


def test_source_file_nao_pode_ser_vazio(cur):
    entry_id, session_id = _entry(cur)
    with pytest.raises(psycopg2.errors.CheckViolation):
        observe_class(cur, entry_id, session_id, source_file="   ")


@pytest.mark.parametrize("bad_digest", [
    "Z" * 64,        # nao hexadecimal
    "A" * 64,        # hexadecimal em maiuscula
    "a" * 65,        # longo demais
    "",              # vazio
])
def test_digest_deve_ser_sha256_hexadecimal(cur, bad_digest):
    entry_id, session_id = _entry(cur)
    with pytest.raises((psycopg2.errors.CheckViolation,
                        psycopg2.errors.StringDataRightTruncation)):
        observe_class(cur, entry_id, session_id, digest=bad_digest)


def test_digest_curto_e_recusado_apesar_do_padding_de_char(cur):
    """
    CHAR(64) pads a shorter value with spaces. The regex runs on the padded
    value, so a 63-character digest fails rather than being silently accepted.
    """
    entry_id, session_id = _entry(cur)
    with pytest.raises(psycopg2.errors.CheckViolation):
        observe_class(cur, entry_id, session_id, digest="a" * 63)


def test_numero_nao_pode_ser_vazio(cur):
    entry_id, session_id = _entry(cur)
    with pytest.raises(psycopg2.errors.CheckViolation):
        observe_number(cur, entry_id, session_id, number="  ")


# ── temporal semantics ────────────────────────────────────────────────────

def test_padrao_e_ausencia_declarada(cur):
    """No source dates a classification row, so nothing is inferred."""
    entry_id, session_id = _entry(cur)
    cur.execute(
        "INSERT INTO entry_class_history (entry_id, session_id, car_class, "
        "source_file, source_digest, source_line) VALUES (%s,%s,'LMP2',%s,%s,2) "
        "RETURNING source_time_basis::text, source_observed_at",
        (entry_id, session_id, FILE_PROVISIONAL, DIGEST_A))
    basis, observed = cur.fetchone()
    assert basis == "none"
    assert observed is None


def test_timestamp_sem_origem_declarada_e_recusado(cur):
    entry_id, session_id = _entry(cur)
    with pytest.raises(psycopg2.errors.CheckViolation):
        observe_class(cur, entry_id, session_id,
                      source_observed_at="2025-04-18T09:15:43Z",
                      source_time_basis="none")


def test_origem_declarada_sem_timestamp_e_recusada(cur):
    entry_id, session_id = _entry(cur)
    with pytest.raises(psycopg2.errors.CheckViolation):
        observe_class(cur, entry_id, session_id,
                      source_observed_at=None,
                      source_time_basis="file_published")


def test_origem_declarada_com_timestamp_e_aceita(cur):
    """The shape a future source would use; no source uses it today."""
    entry_id, session_id = _entry(cur)
    assert observe_class(cur, entry_id, session_id,
                         source_observed_at="2025-04-18T09:15:43Z",
                         source_time_basis="file_published")


def test_ingested_at_e_preenchido_e_nao_e_tempo_de_fonte(cur):
    entry_id, session_id = _entry(cur)
    observe_class(cur, entry_id, session_id)
    cur.execute("SELECT ingested_at IS NOT NULL, source_observed_at IS NULL "
                "FROM entry_class_history WHERE entry_id = %s", (entry_id,))
    assert cur.fetchone() == (True, True)


# ── the grain: raw observations ───────────────────────────────────────────

def test_provisional_e_official_identicos_viram_duas_observacoes(cur):
    """
    69 sessions hold two byte-identical classification files published under
    different names. Keying on the digest alone would collapse them into one
    row and lose the fact that the source published both.
    """
    entry_id, session_id = _entry(cur)
    observe_class(cur, entry_id, session_id,
                  source_file=FILE_PROVISIONAL, digest=DIGEST_A)
    observe_class(cur, entry_id, session_id,
                  source_file=FILE_OFFICIAL, digest=DIGEST_A)
    cur.execute("SELECT count(*), count(DISTINCT source_file) "
                "FROM entry_class_history WHERE entry_id = %s", (entry_id,))
    assert cur.fetchone() == (2, 2)


def test_revisao_do_arquivo_preserva_a_observacao_anterior(cur):
    entry_id, session_id = _entry(cur)
    observe_class(cur, entry_id, session_id, car_class="LMP2", digest=DIGEST_A)
    observe_class(cur, entry_id, session_id, car_class="LMP2 PRO/AM", digest=DIGEST_B)
    cur.execute("SELECT count(*), count(DISTINCT car_class) "
                "FROM entry_class_history WHERE entry_id = %s", (entry_id,))
    assert cur.fetchone() == (2, 2), "a observacao anterior deve sobreviver"


def test_reingestao_do_mesmo_arquivo_e_idempotente(cur):
    entry_id, session_id = _entry(cur)
    observe_class(cur, entry_id, session_id)
    with pytest.raises(psycopg2.errors.UniqueViolation):
        observe_class(cur, entry_id, session_id)


def test_duas_linhas_da_mesma_inscricao_num_arquivo_coexistem(cur):
    """
    Does not occur in the corpus today — zero cases across 3,436 files — but a
    store of raw observations must be able to hold both rather than refuse the
    second or pick one.
    """
    entry_id, session_id = _entry(cur)
    observe_class(cur, entry_id, session_id, line=2)
    observe_class(cur, entry_id, session_id, line=37)
    cur.execute("SELECT count(*) FROM entry_class_history WHERE entry_id = %s",
                (entry_id,))
    assert cur.fetchone()[0] == 2


def test_divergencia_entre_arquivos_coexiste_sem_eleicao(cur):
    entry_id, session_id = _entry(cur)
    observe_class(cur, entry_id, session_id, car_class="LMP2",
                  source_file=FILE_PROVISIONAL, digest=DIGEST_A)
    observe_class(cur, entry_id, session_id, car_class="LMP2 PRO/AM",
                  source_file=FILE_OFFICIAL, digest=DIGEST_B)
    cur.execute("SELECT array_agg(car_class ORDER BY car_class) "
                "FROM entry_class_history WHERE entry_id = %s", (entry_id,))
    assert cur.fetchone()[0] == ["LMP2", "LMP2 PRO/AM"]


def test_numero_literal_e_preservado(cur):
    """#03 stays #03. Normalising merges cars that race together."""
    entry_id, session_id = _entry(cur)
    observe_number(cur, entry_id, session_id, number="03", digest=DIGEST_A)
    observe_number(cur, entry_id, session_id, number="3", digest=DIGEST_B)
    cur.execute("SELECT array_agg(number_as_written ORDER BY number_as_written) "
                "FROM entry_number_history WHERE entry_id = %s", (entry_id,))
    assert cur.fetchone()[0] == ["03", "3"]


def test_larguras_comportam_o_acervo(cur):
    """Widest values measured in raw/: '1009' and 'INNOVATIVE CAR'."""
    entry_id, session_id = _entry(cur)
    assert observe_number(cur, entry_id, session_id, number="1009")
    assert observe_class(cur, entry_id, session_id, car_class="INNOVATIVE CAR")


def test_classe_pode_ser_nula(cur):
    """Pre-2013 exports carry no CLASS column."""
    entry_id, session_id = _entry(cur)
    assert observe_class(cur, entry_id, session_id, car_class=None)


# ── evidence is not deleted implicitly ────────────────────────────────────

def test_apagar_inscricao_com_historico_e_recusado(cur):
    entry_id, session_id = _entry(cur)
    observe_class(cur, entry_id, session_id)
    with pytest.raises(psycopg2.errors.ForeignKeyViolation):
        cur.execute("DELETE FROM entries WHERE id = %s", (entry_id,))


def test_apagar_sessao_com_historico_e_recusado(cur):
    entry_id, session_id = _entry(cur)
    observe_number(cur, entry_id, session_id)
    with pytest.raises(psycopg2.errors.ForeignKeyViolation):
        cur.execute("DELETE FROM sessions WHERE id = %s", (session_id,))


# ── nothing existing was touched ──────────────────────────────────────────

def test_entries_permanece_intacta(cur):
    """car_class, its column type and the six uniqueness regimes of 008."""
    cur.execute("SELECT data_type, character_maximum_length, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_name='entries' AND column_name='car_class'")
    assert cur.fetchone() == ("character varying", 20, "YES")
    cur.execute("SELECT count(*) FROM pg_indexes WHERE tablename='entries' "
                "AND (indexname LIKE 'entries_ident%' OR indexname LIKE 'entries_undet%')")
    assert cur.fetchone()[0] == 6


def test_tabelas_historicas_nao_foram_tocadas(cur):
    for table in ("results", "laps", "result_drivers", "cars"):
        cur.execute("SELECT count(*) FROM pg_constraint c JOIN pg_class t "
                    "ON t.oid = c.conrelid WHERE t.relname = %s "
                    "AND c.conname LIKE '%%history%%'", (table,))
        assert cur.fetchone()[0] == 0, f"{table} ganhou constraint de historico"
