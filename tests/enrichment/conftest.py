"""
Um schema descartável para as suítes de enriquecimento que escrevem.

As duas comportas são as de `tests/loader/sandbox.py`, importadas em vez de
repetidas: a declaração `OPENWEC_TEST_DB_IS_DISPOSABLE` e a recusa de rodar ao
lado de um banco que carrega o acervo histórico.

O que muda é a lista de tabelas e as chaves. `LIKE` não copia chave
estrangeira, e a política que está sob teste é justamente qual tabela
`normalize_teams` reaponta e qual ela não reaponta — um schema sem as duas
chaves para `teams` deixaria o teste da falha passar pelo motivo errado.
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

TABELAS = ("teams", "cars", "entries")


@pytest.fixture(scope="module")
def schema_times():
    """Um schema próprio do módulo, derrubado inteiro quando ele termina."""
    psycopg2 = pytest.importorskip("psycopg2")

    if not declared_disposable():
        pytest.skip(
            f"{DISPOSABLE_ENV} nao definido: esta suite escreve no banco e so "
            "roda onde ele foi declarado descartavel")
    try:
        conn = psycopg2.connect(connect_timeout=5, **DB)
    except psycopg2.OperationalError as exc:
        pytest.skip(f"sem banco em {DB['host']}:{DB['port']} — {exc}")

    conn.autocommit = True
    nome = f"openwec_times_{uuid.uuid4().hex[:12]}"
    try:
        with conn.cursor() as cur:
            antes = historical_counts(cur)
            motivo = refusal(antes)
            if motivo:
                pytest.skip(f"recusado: {motivo}")
            cur.execute("SELECT to_regclass('public.entries')")
            if cur.fetchone()[0] is None:
                pytest.skip("public.entries nao existe; aplique as migrations")

            cur.execute(f'CREATE SCHEMA "{nome}"')
            for tabela in TABELAS:
                cur.execute(f'CREATE SEQUENCE "{nome}".{tabela}_id_seq')
                # `entries` vem sem os indices de propósito. As suítes de
                # tests/schema/ conferem os seis regimes de unicidade de 008
                # com `pg_indexes WHERE tablename = 'entries'`, sem filtrar
                # schema — uma cópia com `INCLUDING INDEXES` apareceria lá
                # como índice a mais e quebraria duas delas. Aqui a tabela
                # existe só para dar a chave estrangeira que impede apagar um
                # time, e para isso índice nenhum é necessário.
                indices = "" if tabela == "entries" else " INCLUDING INDEXES"
                cur.execute(
                    f'CREATE TABLE "{nome}".{tabela} '
                    f"(LIKE public.{tabela} INCLUDING DEFAULTS "
                    f"INCLUDING CONSTRAINTS{indices})")
                cur.execute(
                    f'ALTER TABLE "{nome}".{tabela} '
                    f"ALTER COLUMN id SET DEFAULT nextval('\"{nome}\".{tabela}_id_seq')")
            # As duas chaves que decidem o que uma fusão pode apagar.
            for tabela in ("cars", "entries"):
                cur.execute(
                    f'ALTER TABLE "{nome}".{tabela} '
                    f"ADD CONSTRAINT {tabela}_team_id_fkey "
                    f'FOREIGN KEY (team_id) REFERENCES "{nome}".teams(id)')

        yield {"schema": nome, "db": dict(DB), "public_antes": antes}

    finally:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{nome}" CASCADE')
            depois = historical_counts(cur)
        conn.close()
        assert depois == antes, (
            f"a suite alterou tabelas de public: antes {antes}, depois {depois}")
