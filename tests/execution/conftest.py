"""
A disposable copy of the real schema, for the suites that delete.

The two gates are the loader sandbox's, imported rather than restated: the
`OPENWEC_TEST_DB_IS_DISPOSABLE` declaration, and the refusal to write next
to a database that carries the historical corpus whatever it is called.

What differs is the table list and the foreign key. The executor's whole
subject is `ON DELETE CASCADE` from `result_drivers` onto `results`, so the
copy carries that key for real — `LIKE` does not bring foreign keys, and a
schema without it would let every cascade test pass for the wrong reason.
"""
from __future__ import annotations

import uuid

import pytest

from tests.loader.sandbox import (  # noqa: F401
    DB,
    DISPOSABLE_ENV,
    declared_disposable,
    historical_counts,
    refusal,
)

# Creation order. series → seasons → events → sessions and cars/drivers come
# before results, which comes before result_drivers and laps.
TABELAS = ("series", "seasons", "events", "sessions", "teams", "drivers",
           "cars", "results", "result_drivers", "laps")

SEQUENCIAS = {t: "id" for t in TABELAS}


@pytest.fixture(scope="module")
def schema():
    """A schema of this module's own, dropped whole when the module ends."""
    psycopg2 = pytest.importorskip("psycopg2")

    if not declared_disposable():
        pytest.skip(
            f"{DISPOSABLE_ENV} nao definido: esta suite apaga linhas e so "
            "roda onde o banco foi declarado descartavel")
    try:
        conn = psycopg2.connect(connect_timeout=5, **DB)
    except psycopg2.OperationalError as exc:
        pytest.skip(f"sem banco em {DB['host']}:{DB['port']} — {exc}")

    conn.autocommit = True
    nome = f"openwec_exec_{uuid.uuid4().hex[:12]}"
    try:
        with conn.cursor() as cur:
            antes = historical_counts(cur)
            motivo = refusal(antes)
            if motivo:
                pytest.skip(f"recusado: {motivo}")

            cur.execute(f'CREATE SCHEMA "{nome}"')
            for tabela in TABELAS:
                cur.execute(f'CREATE SEQUENCE "{nome}".{tabela}_id_seq')
                cur.execute(
                    f'CREATE TABLE "{nome}".{tabela} '
                    f"(LIKE public.{tabela} INCLUDING DEFAULTS "
                    f"INCLUDING CONSTRAINTS INCLUDING INDEXES)")
                cur.execute(
                    f'ALTER TABLE "{nome}".{tabela} '
                    f"ALTER COLUMN id SET DEFAULT nextval('\"{nome}\".{tabela}_id_seq')")
            # The key the executor exists to respect.
            cur.execute(
                f'ALTER TABLE "{nome}".result_drivers '
                f"ADD CONSTRAINT result_drivers_result_id_fkey "
                f'FOREIGN KEY (result_id) REFERENCES "{nome}".results(id) '
                f"ON DELETE CASCADE")

        yield {"schema": nome, "db": dict(DB), "public_antes": antes}

    finally:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{nome}" CASCADE')
            depois = historical_counts(cur)
        conn.close()
        assert depois == antes, (
            f"a suite alterou tabelas de public: antes {antes}, depois {depois}")


def connect(schema: dict, autocommit: bool = False):
    """A connection whose unqualified names resolve inside the schema."""
    import psycopg2

    conn = psycopg2.connect(connect_timeout=5, **schema["db"])
    # Outside a transaction: SET is transactional, and these suites roll back.
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{schema["schema"]}", public')
    conn.autocommit = autocommit
    return conn
