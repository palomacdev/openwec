"""
Fixtures for the loader suites.

The rules themselves live in sandbox.py, which is an ordinary module: pytest
does not let a conftest be imported by name, and the guard has to be testable.
"""
from __future__ import annotations

import uuid

import pytest

from tests.loader.sandbox import (  # noqa: F401
    DB,
    DISPOSABLE_ENV,
    SANDBOX_TABLES,
    connect_to_sandbox,
    declared_disposable,
    historical_counts,
    refusal,
)


@pytest.fixture(scope="module")
def sandbox():
    """
    A schema of this module's own, dropped whole when the module ends.

    Yields a dict with the schema name, the connection settings, and the
    `public` row counts taken on entry, so a test can assert that nothing
    outside the sandbox moved.
    """
    psycopg2 = pytest.importorskip("psycopg2")

    if not declared_disposable():
        pytest.skip(
            f"{DISPOSABLE_ENV} nao definido: esta suite escreve no banco e so "
            "roda onde ele foi declarado descartavel"
        )

    try:
        conn = psycopg2.connect(connect_timeout=5, **DB)
    except psycopg2.OperationalError as exc:
        pytest.skip(f"sem banco em {DB['host']}:{DB['port']} — {exc}")

    conn.autocommit = True
    nome = f"openwec_sandbox_{uuid.uuid4().hex[:12]}"
    try:
        with conn.cursor() as cur:
            antes = historical_counts(cur)
            motivo = refusal(antes)
            if motivo:
                pytest.skip(f"recusado: {motivo}")

            cur.execute(f'CREATE SCHEMA "{nome}"')
            for tabela in SANDBOX_TABLES:
                cur.execute(f'CREATE SEQUENCE "{nome}".{tabela}_id_seq')
                cur.execute(
                    f'CREATE TABLE "{nome}".{tabela} '
                    f"(LIKE public.{tabela} INCLUDING DEFAULTS "
                    f"INCLUDING CONSTRAINTS INCLUDING INDEXES)"
                )
                cur.execute(
                    f'ALTER TABLE "{nome}".{tabela} '
                    f"ALTER COLUMN id SET DEFAULT nextval('\"{nome}\".{tabela}_id_seq')"
                )

        yield {"schema": nome, "db": dict(DB), "public_antes": antes}

    finally:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{nome}" CASCADE')
            depois = historical_counts(cur)
        conn.close()
        assert depois == antes, (
            f"a suite alterou tabelas de public: antes {antes}, depois {depois}"
        )
