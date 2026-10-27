"""
Where a result came from, and what the run did with each file.

Before this, `results.source_url` was NULL on all 668.273 rows and `ingest_log`
was empty, so nothing in the database said which file produced a row. Phase 8
had to reconstruct that by comparing content against the raw tree, and for 3
of 516 sessions it could not be reconstructed at all.

These suites pin the two writes that make the reconstruction unnecessary next
time, and the one guarantee that is easy to get wrong: a failure must leave no
rows behind, and must still leave a record saying it happened.

Every write lands in the module's own schema — see conftest.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "database" / "loader"))

psycopg2 = pytest.importorskip("psycopg2")

from tests.loader.sandbox import connect_to_sandbox  # noqa: E402
from load_classification import (  # noqa: E402
    INGEST_STATUS,
    ROLLED_BACK,
    discarded_by_rollback,
    insert_result,
    provenance_of,
    record_ingestions,
    upsert_car,
)
from row_identity import SessionCandidate, SessionOutcome, choose_session  # noqa: E402


FONTE = "raw/elms/03_2019/01_PAUL RICARD/Race/classification/23_Results.CSV"
OUTRA_FONTE = "raw/elms/03_2019/01_PAUL RICARD/Race/classification/23_Results_Final.CSV"


def linha_de(**campos) -> dict:
    base = {
        "POSITION": "1", "NUMBER": "4", "CLASS": "LMP2",
        "TEAM": "EQUIPE A", "VEHICLE": "Oreca 07-Gibson",
        "LAPS": "31", "TOTAL_TIME": "1:00'01.868", "STATUS": "Classified",
        "GAP_FIRST": "", "GAP_PREVIOUS": "", "FL_TIME": "1'48.714",
        "FL_LAPNUM": "25", "FL_KPH": "178.5", "GROUP": "", "TYRES": "M",
    }
    base.update(campos)
    return base


# ── A proveniencia e o arquivo, nao a sessao ─────────────────

def test_a_proveniencia_e_o_caminho_do_arquivo_lido():
    raiz = Path(__file__).resolve().parents[2]
    assert provenance_of(raiz / "raw" / "wec" / "x.CSV") == "raw/wec/x.CSV"


def test_a_proveniencia_nao_depende_de_onde_o_loader_foi_chamado():
    """O mesmo arquivo por caminho relativo e por absoluto da o mesmo valor."""
    raiz = Path(__file__).resolve().parents[2]
    assert provenance_of(Path("raw/wec/x.CSV")) == provenance_of(raiz / "raw/wec/x.CSV")


def test_a_proveniencia_nunca_e_uma_url_de_catalogo():
    """
    O que tornava a origem irrecuperavel era justamente uma URL: todos os
    resultados de um evento apontavam para a mesma pagina de catalogo.
    """
    valor = provenance_of(Path("raw/imsa/23_2023/01_Daytona/Race/classification/a.CSV"))
    assert not valor.startswith(("http://", "https://"))
    assert valor.startswith("raw/")


# ── O lote desfeito ──────────────────────────────────────────

def test_sucesso_desfeito_vira_skip_com_o_motivo():
    pendentes = [("ELMS", FONTE, "success", 42, None)]
    assert list(discarded_by_rollback(pendentes)) == [
        ("ELMS", FONTE, "skipped", 0, ROLLED_BACK)
    ]


def test_quem_nao_carregou_nada_atravessa_o_rollback_inalterado():
    """Um arquivo recusado nao tinha linhas a perder: seu registro continua o mesmo."""
    pendentes = [("WEC", FONTE, "skipped", 0, "nenhuma sessao corresponde a este arquivo")]
    assert list(discarded_by_rollback(pendentes)) == pendentes


def test_a_simulacao_nunca_produz_registro():
    assert INGEST_STATUS.get("ok_dry") is None


def test_toda_recusa_do_loader_tem_um_status_declarado():
    """
    Os quatro desfechos que `process_session` sabe devolver sem carregar nada.
    Se um quinto aparecer sem entrada aqui, o arquivo some do `ingest_log`.
    """
    for desfecho in ("empty", "no_session_match", "ambiguous_session",
                     "contradicted_session"):
        situacao, motivo = INGEST_STATUS[desfecho]
        assert situacao == "skipped"
        assert motivo


# ── A resolucao por campeonato da PR #41 ─────────────────────

def test_a_resolucao_por_campeonato_continua_valendo():
    """
    Esta PR escreve ao lado da resolucao, nao dentro dela. O desempate que
    separa quatro 'Practice 2' de um mesmo evento tem de continuar de pe.
    """
    base = "https://x/Results/23_2023/01_Daytona"
    candidatas = [
        SessionCandidate(10, "a", f"{base}/01_IMSA WeatherTech SportsCar Championship/s"),
        SessionCandidate(11, "b", f"{base}/02_Michelin Pilot Challenge/s"),
    ]
    assert choose_session(candidatas, "01_IMSA WeatherTech SportsCar Championsh") == \
        (10, SessionOutcome.BY_CHAMPIONSHIP)
    assert choose_session(candidatas)[1] is SessionOutcome.MULTIPLE_CANDIDATES


# ── Contra o banco ───────────────────────────────────────────

@pytest.fixture
def conn(sandbox):
    c = connect_to_sandbox(sandbox)
    try:
        yield c
    finally:
        c.rollback()
        c.close()


@pytest.fixture
def cur(conn):
    with conn.cursor() as c:
        yield c


@pytest.fixture
def cenario(cur):
    cur.execute("INSERT INTO teams (name) VALUES (%s) RETURNING id", ("EQUIPE A",))
    equipe = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO sessions (event_id, raw_id, name) VALUES (%s, %s, %s) RETURNING id",
        (1, f"zz_prov_{equipe}", "Race"),
    )
    return equipe, cur.fetchone()[0]


def test_a_carga_grava_a_origem_em_cada_resultado(cur, cenario):
    equipe, sessao = cenario
    car_id = upsert_car(cur, "4", equipe, linha_de(), "ELMS")
    insert_result(cur, sessao, car_id, linha_de(), FONTE)

    cur.execute("SELECT source_url FROM results WHERE session_id = %s", (sessao,))
    assert cur.fetchall() == [(FONTE,)]


def test_sem_origem_declarada_a_coluna_fica_nula(cur, cenario):
    """
    `recover_classification` ainda chama sem origem, e continua gravando NULL
    como hoje. Esta PR nao atravessa a proveniencia por aquele caminho.
    """
    equipe, sessao = cenario
    car_id = upsert_car(cur, "9", equipe, linha_de(NUMBER="9"), "ELMS")
    insert_result(cur, sessao, car_id, linha_de(NUMBER="9"))

    cur.execute("SELECT source_url FROM results WHERE car_id = %s", (car_id,))
    assert cur.fetchall() == [(None,)]


def test_reingestao_de_outro_arquivo_corrige_a_origem(cur, cenario):
    """
    O ramo ON CONFLICT refresca todas as colunas lidas, e a origem e uma
    delas: uma linha nao pode ficar apontando para o arquivo anterior.
    """
    equipe, sessao = cenario
    car_id = upsert_car(cur, "4", equipe, linha_de(), "ELMS")
    insert_result(cur, sessao, car_id, linha_de(), FONTE)
    insert_result(cur, sessao, car_id, linha_de(), OUTRA_FONTE)

    cur.execute("SELECT source_url FROM results WHERE session_id = %s", (sessao,))
    assert cur.fetchall() == [(OUTRA_FONTE,)]


def test_rows_loaded_e_o_que_ficou_na_tabela(cur, cenario):
    """
    `rows_loaded` conta linhas persistidas, nao linhas lidas do arquivo.
    """
    equipe, sessao = cenario
    gravadas = 0
    for numero in ("4", "22", "31"):
        row = linha_de(NUMBER=numero)
        car_id = upsert_car(cur, numero, equipe, row, "ELMS")
        if insert_result(cur, sessao, car_id, row, FONTE) is not None:
            gravadas += 1
    record_ingestions(cur, [("ELMS", FONTE, "success", gravadas, None)])

    cur.execute("SELECT rows_loaded FROM ingest_log WHERE source_url = %s", (FONTE,))
    registrado = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM results WHERE session_id = %s", (sessao,))
    assert registrado == cur.fetchone()[0] == gravadas == 3


def test_reprocessar_o_arquivo_atualiza_o_registro_em_vez_de_duplicar(cur, cenario):
    """
    `ingest_log.source_url` e UNIQUE. Duas passagens pelo mesmo arquivo sao
    uma linha, com o estado da ultima.
    """
    equipe, sessao = cenario
    for _ in range(3):
        car_id = upsert_car(cur, "4", equipe, linha_de(), "ELMS")
        insert_result(cur, sessao, car_id, linha_de(), FONTE)
        record_ingestions(cur, [("ELMS", FONTE, "success", 1, None)])

    cur.execute(
        "SELECT status, rows_loaded FROM ingest_log WHERE source_url = %s", (FONTE,))
    assert cur.fetchall() == [("success", 1)]
    cur.execute("SELECT count(*) FROM results WHERE session_id = %s", (sessao,))
    assert cur.fetchone()[0] == 1


def test_o_registro_passa_a_falar_da_ultima_execucao(cur):
    """Um arquivo que falhou e depois carregou nao continua registrado como erro."""
    record_ingestions(cur, [("ELMS", FONTE, "error", 0, "conexao caiu")])
    cur.execute("SELECT ingested_at FROM ingest_log WHERE source_url = %s", (FONTE,))
    antes = cur.fetchone()[0]

    record_ingestions(cur, [("ELMS", FONTE, "success", 17, None)])
    cur.execute(
        "SELECT status, rows_loaded, error_message, ingested_at "
        "FROM ingest_log WHERE source_url = %s", (FONTE,))
    status, linhas, erro, depois = cur.fetchone()
    assert (status, linhas, erro) == ("success", 17, None)
    assert depois >= antes


def test_a_recusa_fica_registrada_com_o_motivo(cur):
    situacao, motivo = INGEST_STATUS["ambiguous_session"]
    record_ingestions(cur, [("IMSA", FONTE, situacao, 0, motivo)])
    cur.execute(
        "SELECT series_key, file_type, status, rows_loaded, error_message "
        "FROM ingest_log WHERE source_url = %s", (FONTE,))
    assert cur.fetchone() == ("IMSA", "classification", "skipped", 0, motivo)


# ── O harness, do qual a garantia depende ────────────────────

def test_o_schema_do_sandbox_sobrevive_a_um_rollback(sandbox):
    """
    `SET search_path` e transacional. Posto dentro de uma transacao, ele
    voltava ao padrao no ROLLBACK, e a suite seguia escrevendo em `public` —
    exatamente onde o teste de falha abaixo precisa nao escrever.
    """
    c = connect_to_sandbox(sandbox)
    try:
        with c.cursor() as k:
            k.execute("SELECT 1")
        c.rollback()
        with c.cursor() as k:
            k.execute("SELECT current_schema()")
            assert k.fetchone()[0] == sandbox["schema"]
    finally:
        c.close()


# ── A garantia que e facil errar ─────────────────────────────

def test_a_falha_nao_deixa_resultados_parciais_e_ainda_assim_e_registrada(sandbox):
    """
    A ordem do loader, contra um PostgreSQL de verdade: carrega, falha,
    desfaz, registra, comita.

    As duas metades da garantia sao medidas juntas de proposito. Registrar a
    falha dentro da transacao da carga a apagaria junto com as linhas;
    registra-la antes do rollback diria que algo ficou. O que sobra tem de ser
    nenhuma linha e um registro.
    """
    preparo = connect_to_sandbox(sandbox)
    try:
        with preparo.cursor() as c:
            c.execute("INSERT INTO teams (name) VALUES (%s) RETURNING id",
                      ("EQUIPE FALHA",))
            equipe = c.fetchone()[0]
            c.execute(
                "INSERT INTO sessions (event_id, raw_id, name) "
                "VALUES (%s, %s, %s) RETURNING id",
                (1, "zz_prov_falha", "Race"),
            )
            sessao = c.fetchone()[0]
        preparo.commit()
    finally:
        preparo.close()

    fonte = "raw/elms/03_2019/02_MONZA/Race/classification/23_Results.CSV"
    carga = connect_to_sandbox(sandbox)
    try:
        with carga.cursor() as c:
            row = linha_de(NUMBER="7", TEAM="EQUIPE FALHA")
            car_id = upsert_car(c, "7", equipe, row, "ELMS")
            insert_result(c, sessao, car_id, row, fonte)
            c.execute("SELECT count(*) FROM results WHERE session_id = %s", (sessao,))
            assert c.fetchone()[0] == 1, "a carga parcial precisa existir antes da falha"

        carga.rollback()
        with carga.cursor() as c:
            record_ingestions(c, [("ELMS", fonte, "error", 0, "arquivo truncado")])
        carga.commit()
    finally:
        carga.close()

    conferencia = connect_to_sandbox(sandbox)
    try:
        with conferencia.cursor() as c:
            c.execute("SELECT count(*) FROM results WHERE session_id = %s", (sessao,))
            assert c.fetchone()[0] == 0, "o rollback deixou resultados parciais"
            c.execute(
                "SELECT status, rows_loaded, error_message "
                "FROM ingest_log WHERE source_url = %s", (fonte,))
            assert c.fetchone() == ("error", 0, "arquivo truncado")
    finally:
        conferencia.close()
