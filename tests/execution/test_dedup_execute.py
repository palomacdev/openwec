"""
The executor, against a real PostgreSQL carrying the real schema.

Every test runs in a schema of its own, created empty and dropped whole —
see conftest.py for the two gates. The foreign key from `result_drivers` to
`results` is created for real, because `ON DELETE CASCADE` is the whole
subject: a plan that removes a copy removes its driver links with it.

The data is synthetic and shaped like the corpus: one session whose three
signatures each appear three times, two of them with identical links and one
with the links split between two driver rows that differ only in case, and a
second session with no duplication at all.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

psycopg2 = pytest.importorskip("psycopg2")

from database.recovery.dedup_execute import (  # noqa: E402
    ExecutionRefused,
    load_plan,
    row_digest,
    run_plan,
    validate_plan,
)
from database.recovery.dedup_plan import build_plan  # noqa: E402
from tests.execution.conftest import connect  # noqa: E402


URL = ("http://imsa.alkamelsystems.com/Results/16_2016/08_Belle%20Isle/"
       "21_IMSA%20WeatherTech%20SportsCar%20Championship/201606030905_X/03.CSV")


def semear(conn) -> dict:
    """
    Duas sessoes: a primeira triplicada, a segunda intacta.

    Na triplicada, o carro 54 tem os vinculos divididos entre dois pilotos
    que diferem so na caixa — o grupo que o planejador bloqueia.
    """
    with conn.cursor() as cur:
        cur.execute("INSERT INTO series (key, name) VALUES ('IMSA', 'IMSA') "
                    "RETURNING id")
        serie = cur.fetchone()[0]
        cur.execute("INSERT INTO seasons (series_id, label, raw_id, year) "
                    "VALUES (%s, '2016', '16_2016', 2016) RETURNING id", (serie,))
        temporada = cur.fetchone()[0]
        cur.execute("INSERT INTO events (season_id, name, raw_id) "
                    "VALUES (%s, 'Belle Isle', '08_Belle') RETURNING id",
                    (temporada,))
        evento = cur.fetchone()[0]

        sessoes = []
        for nome, raw in (("Practice 1", "p1"), ("Qualifying", "q1")):
            cur.execute(
                "INSERT INTO sessions (event_id, raw_id, name, source_url) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (evento, raw, nome, URL))
            sessoes.append(cur.fetchone()[0])

        cur.execute("INSERT INTO teams (name) VALUES ('CORE autosport') "
                    "RETURNING id")
        equipe = cur.fetchone()[0]

        pilotos = {}
        for chave, (primeiro, ultimo) in {
                "alon": ("Robert", "Alon"),
                "ks_min": ("Tom", "Kimber-smith"),
                "ks_mai": ("Tom", "Kimber-Smith")}.items():
            cur.execute("INSERT INTO drivers (first_name, last_name) "
                        "VALUES (%s, %s) RETURNING id", (primeiro, ultimo))
            pilotos[chave] = cur.fetchone()[0]

        # Sessao triplicada: tres numeros, tres copias de cada, e uma linha
        # de `cars` por copia — e assim que a reingestao duplicou.
        triplicada: dict[str, list[int]] = {}
        for copia in range(3):
            for numero in ("4", "22", "54"):
                cur.execute(
                    "INSERT INTO cars (number, team_id, vehicle, car_class) "
                    "VALUES (%s, %s, 'Oreca FLM09', 'PC') RETURNING id",
                    (numero, equipe))
                carro = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO results (session_id, car_id, laps_completed, "
                    "status) VALUES (%s, %s, %s, 'Classified') RETURNING id",
                    (sessoes[0], carro, 30 + int(numero)))
                result = cur.fetchone()[0]
                triplicada.setdefault(numero, []).append(result)

                cur.execute("INSERT INTO result_drivers (result_id, driver_id, "
                            "slot) VALUES (%s, %s, 1)", (result, pilotos["alon"]))
                if numero == "54":
                    # A divergencia: as duas primeiras copias apontam para uma
                    # grafia, a terceira para a outra.
                    segundo = pilotos["ks_min" if copia < 2 else "ks_mai"]
                    cur.execute("INSERT INTO result_drivers (result_id, "
                                "driver_id, slot) VALUES (%s, %s, 2)",
                                (result, segundo))

        # Sessao intacta.
        intacta = []
        for numero in ("4", "22"):
            cur.execute("INSERT INTO cars (number, team_id, car_class) "
                        "VALUES (%s, %s, 'PC') RETURNING id", (numero, equipe))
            carro = cur.fetchone()[0]
            cur.execute("INSERT INTO results (session_id, car_id, status) "
                        "VALUES (%s, %s, 'Classified') RETURNING id",
                        (sessoes[1], carro))
            intacta.append(cur.fetchone()[0])
    conn.commit()
    return {"sessoes": sessoes, "triplicada": triplicada, "intacta": intacta,
            "pilotos": pilotos}


def planejar(schema) -> dict:
    conn = connect(schema)
    try:
        with conn.cursor() as cur:
            return build_plan(cur, "IMSA")
    finally:
        conn.rollback()
        conn.close()


def contagens(schema) -> tuple[int, int]:
    conn = connect(schema, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM results")
            r = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM result_drivers")
            return r, cur.fetchone()[0]
    finally:
        conn.close()


@pytest.fixture
def mundo(schema):
    """Dados semeados, e a tabela limpa de novo ao final do teste."""
    conn = connect(schema, autocommit=True)
    try:
        with conn.cursor() as cur:
            for tabela in ("result_drivers", "laps", "results", "cars",
                           "drivers", "teams", "sessions", "events",
                           "seasons", "series"):
                cur.execute(f"TRUNCATE {tabela} CASCADE")
    finally:
        conn.close()

    semente_conn = connect(schema)
    try:
        dados = semear(semente_conn)
    finally:
        semente_conn.close()
    dados["schema"] = schema
    dados["plano"] = planejar(schema)
    return dados


def executar(schema, plano, aplicar=False, backup_dir=None):
    conn = connect(schema)
    try:
        return run_plan(conn, plano, backup_dir, aplicar)
    finally:
        if not conn.closed:
            conn.rollback()
            conn.close()


# ── O plano que a semente produz ─────────────────────────────

def test_a_semente_produz_o_plano_esperado(mundo):
    plano = mundo["plano"]
    assert plano["contrato"] == "openwec-dedup-plan-v2"
    resumo = plano["resumo"]
    assert resumo["results_atuais"] == 11          # 9 triplicados + 2 intactos
    assert resumo["results_removiveis"] == 4       # dois grupos de 3 → 2 saem
    assert resumo["results_preservados"] == 7
    assert resumo["grupos_bloqueados_por_vinculo"] == 1
    assert resumo["sessoes_sem_duplicacao"] == 1
    validate_plan(plano)


# ── 1. Dry-run ───────────────────────────────────────────────

def test_dry_run_nao_altera_nada(mundo):
    antes = contagens(mundo["schema"])
    relatorio = executar(mundo["schema"], mundo["plano"])
    assert relatorio["modo"] == "dry-run"
    assert relatorio["aplicado"] is False
    assert relatorio["results_removiveis"] == 4
    assert relatorio["results_preservados"] == 7
    assert relatorio["divergencias"] == []
    assert contagens(mundo["schema"]) == antes


def test_dry_run_nao_exige_backup(mundo):
    assert executar(mundo["schema"], mundo["plano"])["backup"] == {
        "estado": "nao exigido em dry-run"}


# ── 2. Execucao ──────────────────────────────────────────────

def test_execucao_remove_exatamente_o_plano(mundo, tmp_path):
    results_antes, vinculos_antes = contagens(mundo["schema"])
    relatorio = executar(mundo["schema"], mundo["plano"], True, tmp_path)

    assert relatorio["aplicado"] is True
    assert relatorio["results_removidos"] == 4
    assert relatorio["vinculos_removidos"] == 4      # um vinculo por copia
    results_depois, vinculos_depois = contagens(mundo["schema"])
    assert results_depois == results_antes - 4
    assert vinculos_depois == vinculos_antes - 4

    conn = connect(mundo["schema"], autocommit=True)
    try:
        with conn.cursor() as cur:
            removiveis = [i for s in mundo["plano"]["sessions"]
                          for i in s["ids_removiveis"]]
            cur.execute("SELECT count(*) FROM results WHERE id = ANY(%s)",
                        (removiveis,))
            assert cur.fetchone()[0] == 0
            preservados = [i for s in mundo["plano"]["sessions"]
                           for i in s["ids_preservados"]]
            cur.execute("SELECT count(*) FROM results WHERE id = ANY(%s)",
                        (preservados,))
            assert cur.fetchone()[0] == len(preservados)
    finally:
        conn.close()


def test_a_sessao_intacta_continua_intacta(mundo, tmp_path):
    executar(mundo["schema"], mundo["plano"], True, tmp_path)
    conn = connect(mundo["schema"], autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM results WHERE session_id = %s",
                        (mundo["sessoes"][1],))
            assert cur.fetchone()[0] == 2
    finally:
        conn.close()


# ── 8. Grupos bloqueados ─────────────────────────────────────

def test_o_grupo_bloqueado_sobrevive_inteiro(mundo, tmp_path):
    bloqueados = mundo["triplicada"]["54"]
    executar(mundo["schema"], mundo["plano"], True, tmp_path)

    conn = connect(mundo["schema"], autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM results WHERE id = ANY(%s)",
                        (bloqueados,))
            assert cur.fetchone()[0] == 3, "o grupo bloqueado perdeu linhas"
            cur.execute("SELECT count(*) FROM result_drivers "
                        "WHERE result_id = ANY(%s)", (bloqueados,))
            assert cur.fetchone()[0] == 6
            # E o piloto que so aparecia na terceira copia continua ligado.
            cur.execute("SELECT count(*) FROM result_drivers WHERE driver_id = %s",
                        (mundo["pilotos"]["ks_mai"],))
            assert cur.fetchone()[0] == 1
    finally:
        conn.close()


# ── 3. Plano obsoleto ────────────────────────────────────────

def test_plano_obsoleto_e_recusado(mundo, tmp_path):
    """Uma linha a mais na sessao e o plano ja nao descreve este banco."""
    conn = connect(mundo["schema"], autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO cars (number, team_id, car_class) "
                        "SELECT '99', team_id, 'PC' FROM cars LIMIT 1 RETURNING id")
            carro = cur.fetchone()[0]
            cur.execute("INSERT INTO results (session_id, car_id, status) "
                        "VALUES (%s, %s, 'Classified')",
                        (mundo["sessoes"][0], carro))
    finally:
        conn.close()

    with pytest.raises(ExecutionRefused) as erro:
        executar(mundo["schema"], mundo["plano"], True, tmp_path)
    assert any("plan_digest" in m for m in erro.value.motivos)
    assert contagens(mundo["schema"])[0] == 12


# ── 4. Divergencia de vinculos ───────────────────────────────

def test_vinculo_alterado_depois_do_plano_e_recusado(mundo, tmp_path):
    """
    O conteudo das linhas nao muda, as contagens nao mudam, e os ids sao os
    mesmos. So o vinculo mudou — e e por isso que ele entra na comparacao.
    """
    alvo = mundo["triplicada"]["4"][0]
    conn = connect(mundo["schema"], autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE result_drivers SET driver_id = %s "
                        "WHERE result_id = %s AND slot = 1",
                        (mundo["pilotos"]["ks_mai"], alvo))
    finally:
        conn.close()

    with pytest.raises(ExecutionRefused) as erro:
        executar(mundo["schema"], mundo["plano"], True, tmp_path)
    assert any("vinculos_sha256" in m or "plan_digest" in m
               for m in erro.value.motivos)
    assert contagens(mundo["schema"])[0] == 11


# ── 5. Dependencia nova ──────────────────────────────────────

def test_chave_estrangeira_nova_e_recusada(mundo, tmp_path):
    conn = connect(mundo["schema"], autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE stints (id SERIAL PRIMARY KEY, "
                        "result_id INT REFERENCES results(id))")
            cur.execute("INSERT INTO stints (result_id) VALUES (%s)",
                        (mundo["triplicada"]["4"][1],))
        with pytest.raises(ExecutionRefused) as erro:
            executar(mundo["schema"], mundo["plano"], True, tmp_path)
        assert any("nao modelada" in m or "chaves sobre results mudaram" in m
                   for m in erro.value.motivos)
        assert contagens(mundo["schema"])[0] == 11
    finally:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS stints")
        conn.close()


# ── 6. Falha durante a exclusao ──────────────────────────────

def test_falha_depois_do_delete_desfaz_tudo(mundo, tmp_path, monkeypatch):
    """
    O DELETE acontece, a verificacao falha, e nada sobra dele.

    O rollback e do chamador, como em `main`: o modulo levanta e nao comita.
    """
    import database.recovery.dedup_execute as executor
    monkeypatch.setattr(executor, "check_invariants",
                        lambda *a, **k: ["falha simulada depois do delete"])

    antes = contagens(mundo["schema"])
    with pytest.raises(ExecutionRefused) as erro:
        executar(mundo["schema"], mundo["plano"], True, tmp_path)
    assert "falha simulada depois do delete" in erro.value.motivos
    assert contagens(mundo["schema"]) == antes


def test_excecao_inesperada_tambem_desfaz(mundo, tmp_path, monkeypatch):
    import database.recovery.dedup_execute as executor
    chamadas = {"n": 0}
    original = executor.snapshot

    def quebra(*a, **k):
        chamadas["n"] += 1
        if chamadas["n"] > 1:                 # a segunda e a de depois do delete
            raise RuntimeError("conexao caiu no meio")
        return original(*a, **k)

    monkeypatch.setattr(executor, "snapshot", quebra)
    antes = contagens(mundo["schema"])
    with pytest.raises(RuntimeError):
        executar(mundo["schema"], mundo["plano"], True, tmp_path)
    assert contagens(mundo["schema"]) == antes


# ── 7. Segunda execucao ──────────────────────────────────────

def test_a_segunda_execucao_do_mesmo_plano_e_recusada(mundo, tmp_path):
    executar(mundo["schema"], mundo["plano"], True, tmp_path)
    depois = contagens(mundo["schema"])

    with pytest.raises(ExecutionRefused) as erro:
        executar(mundo["schema"], mundo["plano"], True, tmp_path)
    assert erro.value.motivos
    assert contagens(mundo["schema"]) == depois, "a segunda passagem mexeu"


# ── 9. Backup ────────────────────────────────────────────────

def test_o_backup_e_conferido_antes_da_exclusao(mundo, tmp_path):
    relatorio = executar(mundo["schema"], mundo["plano"], True, tmp_path)
    backup = relatorio["backup"]
    assert backup["estado"] == "restauracao conferida"
    assert backup["results"] == 4
    assert backup["result_drivers"] == 4
    assert Path(backup["caminho"]).exists()


def test_backup_que_nao_reproduz_as_linhas_recusa_antes_de_apagar(
        mundo, tmp_path, monkeypatch):
    """
    O que torna a conferencia uma conferencia: um arquivo que nao reproduz
    as linhas vivas interrompe a execucao, e nada e apagado.
    """
    import database.recovery.dedup_execute as executor
    original = executor.backup_payload

    def adultera(cur, ids):
        payload = original(cur, ids)
        coluna = payload["results"]["colunas"].index("laps_completed")
        payload["results"]["linhas"][0][coluna] = 999
        return payload

    monkeypatch.setattr(executor, "backup_payload", adultera)
    antes = contagens(mundo["schema"])
    with pytest.raises(ExecutionRefused) as erro:
        executar(mundo["schema"], mundo["plano"], True, tmp_path)
    assert any("nao reproduz" in m for m in erro.value.motivos)
    assert contagens(mundo["schema"]) == antes


def test_o_backup_restaura_as_linhas_removidas(mundo, tmp_path):
    """
    A prova de reversibilidade: apagar, repor do arquivo, e as linhas voltam
    identicas as que existiam antes — conferidas pelo mesmo digest.
    """
    removiveis = sorted(i for s in mundo["plano"]["sessions"]
                        for i in s["ids_removiveis"])
    conn = connect(mundo["schema"], autocommit=True)
    try:
        with conn.cursor() as cur:
            antes_r = row_digest(cur, "results", removiveis)
            antes_v = row_digest(cur, "result_drivers", removiveis, "result_id")
    finally:
        conn.close()

    relatorio = executar(mundo["schema"], mundo["plano"], True, tmp_path)
    payload = load_plan(Path(relatorio["backup"]["caminho"]))

    conn = connect(mundo["schema"], autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM results WHERE id = ANY(%s)",
                        (removiveis,))
            assert cur.fetchone()[0] == 0
            for tabela in ("results", "result_drivers"):
                dados = payload[tabela]
                colunas = ", ".join(f'"{c}"' for c in dados["colunas"])
                marcas = ", ".join(["%s"] * len(dados["colunas"]))
                for linha in dados["linhas"]:
                    cur.execute(
                        f'INSERT INTO {tabela} ({colunas}) VALUES ({marcas})',
                        tuple(linha))
            assert row_digest(cur, "results", removiveis) == antes_r
            assert row_digest(cur, "result_drivers", removiveis,
                              "result_id") == antes_v
    finally:
        conn.close()


# ── O plano como arquivo ─────────────────────────────────────

def test_plano_adulterado_no_arquivo_e_recusado(mundo, tmp_path):
    """O plan_digest do arquivo e recalculado das proprias listas."""
    caminho = tmp_path / "plano.json"
    plano = json.loads(json.dumps(mundo["plano"], default=str))
    plano["sessions"][0]["ids_removiveis"].append(999999)
    caminho.write_text(json.dumps(plano, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ExecutionRefused) as erro:
        validate_plan(load_plan(caminho))
    assert any("plan_digest" in m for m in erro.value.motivos)


def test_contrato_antigo_e_recusado(mundo):
    plano = dict(mundo["plano"], contrato="openwec-dedup-plan-v1")
    with pytest.raises(ExecutionRefused) as erro:
        validate_plan(plano)
    assert "contrato" in erro.value.motivos[0]
