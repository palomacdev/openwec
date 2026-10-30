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
    insert_result,
    provenance_of,
    record_ingestions,
    upsert_car,
)
from row_identity import SessionCandidate, SessionOutcome, choose_session  # noqa: E402

import load_classification as lc  # noqa: E402


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


# ── O vocabulario do registro ────────────────────────────────

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
    car_id = upsert_car(cur, "4", equipe, linha_de(), "ELMS", sessao)
    insert_result(cur, sessao, car_id, linha_de(), FONTE)

    cur.execute("SELECT source_url FROM results WHERE session_id = %s", (sessao,))
    assert cur.fetchall() == [(FONTE,)]


def test_sem_origem_declarada_a_coluna_fica_nula(cur, cenario):
    """
    `recover_classification` ainda chama sem origem, e continua gravando NULL
    como hoje. Esta PR nao atravessa a proveniencia por aquele caminho.
    """
    equipe, sessao = cenario
    car_id = upsert_car(cur, "9", equipe, linha_de(NUMBER="9"), "ELMS", sessao)
    insert_result(cur, sessao, car_id, linha_de(NUMBER="9"))

    cur.execute("SELECT source_url FROM results WHERE car_id = %s", (car_id,))
    assert cur.fetchall() == [(None,)]


def test_reingestao_de_outro_arquivo_corrige_a_origem(cur, cenario):
    """
    O ramo ON CONFLICT refresca todas as colunas lidas, e a origem e uma
    delas: uma linha nao pode ficar apontando para o arquivo anterior.
    """
    equipe, sessao = cenario
    car_id = upsert_car(cur, "4", equipe, linha_de(), "ELMS", sessao)
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
        car_id = upsert_car(cur, numero, equipe, row, "ELMS", sessao)
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
        car_id = upsert_car(cur, "4", equipe, linha_de(), "ELMS", sessao)
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


# ── A sessao como unidade transacional ───────────────────────

def semear(sandbox, equipe_nome: str, raw_ids: tuple[str, ...]):
    """Equipe e sessoes ja comitadas, para que a carga comece de um estado firme."""
    conn = connect_to_sandbox(sandbox)
    try:
        with conn.cursor() as c:
            c.execute("INSERT INTO teams (name) VALUES (%s) RETURNING id", (equipe_nome,))
            equipe = c.fetchone()[0]
            sessoes = []
            for raw_id in raw_ids:
                c.execute(
                    "INSERT INTO sessions (event_id, raw_id, name) "
                    "VALUES (%s, %s, %s) RETURNING id",
                    (1, raw_id, "Race"),
                )
                sessoes.append(c.fetchone()[0])
        conn.commit()
    finally:
        conn.close()
    return equipe, sessoes


def test_a_sessao_e_a_unidade_transacional(sandbox, monkeypatch, capsys):
    """
    O laco de `run` de verdade, contra um PostgreSQL de verdade: a sessao A
    carrega e comita, a sessao B falha no meio.

    So a caminhada pela arvore raw/ e a leitura do CSV sao substituidas — o
    que precisaria de corpus e de um banco povoado. Tudo o que esta sob
    julgamento aqui (comitar por sessao, desfazer so a sessao atual,
    registrar, contar depois do commit) e o codigo da PR.
    """
    equipe, (sessao_a, sessao_b) = semear(
        sandbox, "EQUIPE AB", ("zz_prov_a", "zz_prov_b"))
    fonte_a = Path("raw/elms/03_2019/02_MONZA/Race/classification/23_Results.CSV")
    fonte_b = Path("raw/elms/03_2019/03_BARCELONA/Race/classification/23_Results.CSV")

    arquivos = [
        {"season_raw": "03_2019", "event_raw": "02_MONZA", "session_name": "Race",
         "championship_raw": None, "csv_path": fonte_a,
         "sessao": sessao_a, "falhar": False},
        {"season_raw": "03_2019", "event_raw": "03_BARCELONA", "session_name": "Race",
         "championship_raw": None, "csv_path": fonte_b,
         "sessao": sessao_b, "falhar": True},
    ]

    def carrega(cur, series_key, sess, dry_run):
        """O que `process_session` faz, sem ler arquivo nem resolver sessao."""
        gravadas = 0
        for numero in ("4", "22"):
            row = linha_de(NUMBER=numero, TEAM="EQUIPE AB")
            car_id = upsert_car(cur, numero, equipe, row, series_key, sess["sessao"])
            if insert_result(cur, sess["sessao"], car_id, row,
                             provenance_of(sess["csv_path"])) is not None:
                gravadas += 1
            if sess["falhar"] and gravadas == 1:
                raise RuntimeError("arquivo truncado na segunda linha")
        return {"status": "ok", "rows": 2, "loaded": gravadas,
                "session_id": sess["sessao"]}

    conferencia = connect_to_sandbox(sandbox)      # antes do monkeypatch
    carga = connect_to_sandbox(sandbox)
    try:
        monkeypatch.setattr(lc.psycopg2, "connect", lambda **kw: carga)
        monkeypatch.setattr(lc, "RAW_DIRS", {"ELMS": Path("raw/elms")})
        monkeypatch.setattr(lc, "_writes_are_blocked", lambda: False)
        monkeypatch.setattr(lc, "find_classification_sessions",
                            lambda raw_dir, series_key: arquivos)
        monkeypatch.setattr(lc, "process_session", carrega)

        lc.run()
        saida = capsys.readouterr().out

        with conferencia.cursor() as c:
            # 1 e 3. A sessao confirmada continua inteira, com log de sucesso.
            c.execute("SELECT count(*) FROM results WHERE session_id = %s", (sessao_a,))
            assert c.fetchone()[0] == 2, "a falha seguinte alcancou a sessao confirmada"
            c.execute(
                "SELECT status, rows_loaded FROM ingest_log WHERE source_url = %s",
                (provenance_of(fonte_a),))
            assert c.fetchone() == ("success", 2)

            # 2 e 4. A sessao que falhou nao deixou nada, e deixou o registro.
            c.execute("SELECT count(*) FROM results WHERE session_id = %s", (sessao_b,))
            assert c.fetchone()[0] == 0, "o rollback deixou resultados parciais"
            c.execute(
                "SELECT status, rows_loaded, error_message "
                "FROM ingest_log WHERE source_url = %s", (provenance_of(fonte_b),))
            assert c.fetchone() == ("error", 0, "arquivo truncado na segunda linha")

            # 5. O resumo fala do que ficou, nao do que foi tentado.
            c.execute(
                "SELECT count(*) FROM results WHERE session_id IN (%s, %s)",
                (sessao_a, sessao_b))
            assert c.fetchone()[0] == 2
        assert "Rows persisted:     2" in saida
        assert "Errors:             1" in saida
        assert "Loaded:           1" in saida
    finally:
        conferencia.close()
        carga.close()


def test_a_simulacao_nao_escreve(sandbox, monkeypatch, capsys):
    """`--dry-run` atravessa o mesmo laco e nao deixa linha nem registro."""
    equipe, (sessao,) = semear(sandbox, "EQUIPE SECA", ("zz_prov_seca",))
    fonte = Path("raw/elms/03_2019/05_FUJI/Race/classification/23_Results.CSV")

    conferencia = connect_to_sandbox(sandbox)
    carga = connect_to_sandbox(sandbox)
    try:
        monkeypatch.setattr(lc.psycopg2, "connect", lambda **kw: carga)
        monkeypatch.setattr(lc, "RAW_DIRS", {"ELMS": Path("raw/elms")})
        monkeypatch.setattr(lc, "find_classification_sessions",
                            lambda raw_dir, series_key: [
                                {"season_raw": "03_2019", "event_raw": "05_FUJI",
                                 "session_name": "Race", "championship_raw": None,
                                 "csv_path": fonte}])
        monkeypatch.setattr(
            lc, "process_session",
            lambda cur, series_key, sess, dry_run: {
                "status": "ok_dry", "rows": 2, "session_id": sessao})

        lc.run(dry_run=True)
        capsys.readouterr()

        with conferencia.cursor() as c:
            c.execute("SELECT count(*) FROM results WHERE session_id = %s", (sessao,))
            assert c.fetchone()[0] == 0
            c.execute("SELECT count(*) FROM ingest_log WHERE source_url = %s",
                      (provenance_of(fonte),))
            assert c.fetchone()[0] == 0
    finally:
        conferencia.close()
        carga.close()


def test_falha_ao_gravar_o_log_nao_contamina_a_proxima_sessao(
        sandbox, monkeypatch, capsys):
    """
    Registrar uma falha pode ele mesmo falhar. O laco desfaz e segue: o que
    nao pode acontecer e a sessao seguinte herdar a transacao abortada e
    perder o que carregou.

    Aqui a sessao A falha na carga e o registro do seu erro tambem falha. A
    sessao B, logo depois, na mesma conexao, tem de ficar inteira.
    """
    equipe, (sessao_a, sessao_b) = semear(
        sandbox, "EQUIPE LOG", ("zz_prov_log_a", "zz_prov_log_b"))
    fonte_a = Path("raw/elms/03_2019/04_SPA/Race/classification/23_Results.CSV")
    fonte_b = Path("raw/elms/03_2019/05_FUJI/Race/classification/23_Results.CSV")

    arquivos = [
        {"season_raw": "03_2019", "event_raw": "04_SPA", "session_name": "Race",
         "championship_raw": None, "csv_path": fonte_a,
         "sessao": sessao_a, "falhar": True},
        {"season_raw": "03_2019", "event_raw": "05_FUJI", "session_name": "Race",
         "championship_raw": None, "csv_path": fonte_b,
         "sessao": sessao_b, "falhar": False},
    ]

    def carrega(cur, series_key, sess, dry_run):
        row = linha_de(NUMBER="31", TEAM="EQUIPE LOG")
        car_id = upsert_car(cur, "31", equipe, row, series_key, sess["sessao"])
        insert_result(cur, sess["sessao"], car_id, row,
                      provenance_of(sess["csv_path"]))
        if sess["falhar"]:
            raise RuntimeError("arquivo truncado")
        return {"status": "ok", "rows": 1, "loaded": 1, "session_id": sess["sessao"]}

    registrar = lc.record_ingestions

    def registra_mal(cur, registros):
        """Uma serie fora do enum: o proprio registro aborta a transacao."""
        for serie, fonte, situacao, linhas, motivo in registros:
            serie = "SERIE_INEXISTENTE" if situacao == "error" else serie
            registrar(cur, [(serie, fonte, situacao, linhas, motivo)])

    conferencia = connect_to_sandbox(sandbox)
    carga = connect_to_sandbox(sandbox)
    try:
        monkeypatch.setattr(lc.psycopg2, "connect", lambda **kw: carga)
        monkeypatch.setattr(lc, "RAW_DIRS", {"ELMS": Path("raw/elms")})
        monkeypatch.setattr(lc, "_writes_are_blocked", lambda: False)
        monkeypatch.setattr(lc, "find_classification_sessions",
                            lambda raw_dir, series_key: arquivos)
        monkeypatch.setattr(lc, "process_session", carrega)
        monkeypatch.setattr(lc, "record_ingestions", registra_mal)

        lc.run()
        saida = capsys.readouterr().out
        assert "o registro da falha nao pode ser gravado" in saida

        with conferencia.cursor() as c:
            # A sessao que falhou nao deixou linha nem registro.
            c.execute("SELECT count(*) FROM results WHERE session_id = %s", (sessao_a,))
            assert c.fetchone()[0] == 0
            c.execute("SELECT count(*) FROM ingest_log WHERE source_url = %s",
                      (provenance_of(fonte_a),))
            assert c.fetchone()[0] == 0

            # A seguinte, na mesma conexao, ficou inteira.
            c.execute("SELECT count(*) FROM results WHERE session_id = %s", (sessao_b,))
            assert c.fetchone()[0] == 1, "a transacao abortada contaminou a sessao seguinte"
            c.execute(
                "SELECT status, rows_loaded FROM ingest_log WHERE source_url = %s",
                (provenance_of(fonte_b),))
            assert c.fetchone() == ("success", 1)
        assert "Errors:             1" in saida
        assert "Loaded:           1" in saida
    finally:
        conferencia.close()
        carga.close()
