"""
The executor, against a real PostgreSQL carrying the real schema.

Every test runs in a schema of its own, created empty and dropped whole —
see conftest.py for the two gates. The foreign key from `result_drivers` to
`results` is created for real, because `ON DELETE CASCADE` is the whole
subject: a plan that removes a copy removes its driver links with it.

The data is synthetic and shaped like the corpus: one session whose three
signatures each appear three times, every copy carrying two driver rows and
one of the three with its second row split between two spellings that differ
only in case, and a second session with no duplication at all.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

psycopg2 = pytest.importorskip("psycopg2")

from database.recovery.dedup_execute import (  # noqa: E402
    ExecutionRefused,
    load_plan,
    row_digest,
    run_plan,
    table_columns,
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
                "braun": ("Colin", "Braun"),
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
                # Dois pilotos por carro, como no acervo: 119.661 results
                # carregam mais de um vinculo, e e essa forma que o digest
                # do backup tem de atravessar.
                if numero == "54":
                    # A divergencia: as duas primeiras copias apontam para uma
                    # grafia, a terceira para a outra.
                    segundo = pilotos["ks_min" if copia < 2 else "ks_mai"]
                else:
                    segundo = pilotos["braun"]
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


def atestar_dump(destino: Path, schema, *, idade_horas: float = 0.0,
                 contagens: dict | None = None) -> Path:
    """
    Um dump e a evidencia que `backup_attestation` exige dele.

    A restauracao e verificada noutro cluster, e aqui so existe um, entao o
    bloco `restauracao` e montado com o mesmo formato que `record_restore`
    grava. O que esta sob teste e o portao do executor, nao a atestacao, que
    tem suas proprias suites.
    """
    from database.recovery import backup_attestation as att

    destino.mkdir(parents=True, exist_ok=True)
    dump = destino / "cluster.dump"
    dump.write_bytes(b"PGDMP-conteudo-de-teste")

    agora = datetime.now(timezone.utc) - timedelta(hours=idade_horas)
    conn = connect(schema, autocommit=True)
    try:
        with conn.cursor() as cur:
            prova = att.capture(cur, dump, 1, agora=agora)
    finally:
        conn.close()

    if contagens:
        prova["contagens"].update(contagens)
    prova["restauracao"] = {
        "ok": True,
        "cluster_de_destino": prova["cluster_de_origem"] + "9",
        "verificada_em": agora.isoformat(timespec="seconds"),
        "contagens": dict(prova["contagens"]),
        "divergencias": {},
    }
    att.attestation_path(dump).write_text(
        json.dumps(prova, ensure_ascii=False), encoding="utf-8")
    return dump


def executar(schema, plano, aplicar=False, backup_dir=None, dump=None,
             max_age_hours=6):
    conn = connect(schema)
    try:
        return run_plan(conn, plano, backup_dir, aplicar, dump, max_age_hours)
    finally:
        if not conn.closed:
            conn.rollback()
            conn.close()


@pytest.fixture
def dump_valido(mundo, tmp_path):
    return atestar_dump(tmp_path / "cluster", mundo["schema"])


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
    assert relatorio["escritores_concorrentes"] == []
    assert contagens(mundo["schema"]) == antes


def test_dry_run_nao_exige_backup(mundo):
    assert executar(mundo["schema"], mundo["plano"])["backup"] == {
        "estado": "nao exigido em dry-run"}


# ── 2. Execucao ──────────────────────────────────────────────

def test_execucao_remove_exatamente_o_plano(mundo, dump_valido, tmp_path):
    results_antes, vinculos_antes = contagens(mundo["schema"])
    relatorio = executar(mundo["schema"], mundo["plano"], True, tmp_path, dump_valido)

    assert relatorio["aplicado"] is True
    assert relatorio["results_removidos"] == 4
    assert relatorio["vinculos_removidos"] == 8      # dois vinculos por copia
    results_depois, vinculos_depois = contagens(mundo["schema"])
    assert results_depois == results_antes - 4
    assert vinculos_depois == vinculos_antes - 8

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


def test_a_sessao_intacta_continua_intacta(mundo, dump_valido, tmp_path):
    executar(mundo["schema"], mundo["plano"], True, tmp_path, dump_valido)
    conn = connect(mundo["schema"], autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM results WHERE session_id = %s",
                        (mundo["sessoes"][1],))
            assert cur.fetchone()[0] == 2
    finally:
        conn.close()


# ── 8. Grupos bloqueados ─────────────────────────────────────

def test_o_grupo_bloqueado_sobrevive_inteiro(mundo, dump_valido, tmp_path):
    bloqueados = mundo["triplicada"]["54"]
    executar(mundo["schema"], mundo["plano"], True, tmp_path, dump_valido)

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

def test_plano_obsoleto_e_recusado(mundo, dump_valido, tmp_path):
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
        executar(mundo["schema"], mundo["plano"], True, tmp_path, dump_valido)
    assert any("plan_digest" in m for m in erro.value.motivos)
    assert contagens(mundo["schema"])[0] == 12


# ── 4. Divergencia de vinculos ───────────────────────────────

def test_vinculo_alterado_depois_do_plano_e_recusado(mundo, dump_valido, tmp_path):
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
        executar(mundo["schema"], mundo["plano"], True, tmp_path, dump_valido)
    assert any("vinculos_sha256" in m or "plan_digest" in m
               for m in erro.value.motivos)
    assert contagens(mundo["schema"])[0] == 11


# ── 5. Dependencia nova ──────────────────────────────────────

def test_chave_estrangeira_nova_e_recusada(mundo, dump_valido, tmp_path):
    conn = connect(mundo["schema"], autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE stints (id SERIAL PRIMARY KEY, "
                        "result_id INT REFERENCES results(id))")
            cur.execute("INSERT INTO stints (result_id) VALUES (%s)",
                        (mundo["triplicada"]["4"][1],))
        with pytest.raises(ExecutionRefused) as erro:
            executar(mundo["schema"], mundo["plano"], True, tmp_path, dump_valido)
        assert any("nao modelada" in m or "chaves sobre results mudaram" in m
                   for m in erro.value.motivos)
        assert contagens(mundo["schema"])[0] == 11
    finally:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS stints")
        conn.close()


# ── 6. Falha durante a exclusao ──────────────────────────────

def test_falha_depois_do_delete_desfaz_tudo(mundo, dump_valido, tmp_path, monkeypatch):
    """
    O DELETE acontece, a verificacao falha, e nada sobra dele.

    O rollback e do chamador, como em `main`: o modulo levanta e nao comita.
    """
    import database.recovery.dedup_execute as executor
    monkeypatch.setattr(executor, "check_invariants",
                        lambda *a, **k: ["falha simulada depois do delete"])

    antes = contagens(mundo["schema"])
    with pytest.raises(ExecutionRefused) as erro:
        executar(mundo["schema"], mundo["plano"], True, tmp_path, dump_valido)
    assert "falha simulada depois do delete" in erro.value.motivos
    assert contagens(mundo["schema"]) == antes


def test_excecao_inesperada_tambem_desfaz(mundo, dump_valido, tmp_path, monkeypatch):
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
        executar(mundo["schema"], mundo["plano"], True, tmp_path, dump_valido)
    assert contagens(mundo["schema"]) == antes


# ── 7. Segunda execucao ──────────────────────────────────────

def test_a_segunda_execucao_do_mesmo_plano_e_recusada(mundo, dump_valido, tmp_path):
    executar(mundo["schema"], mundo["plano"], True, tmp_path, dump_valido)
    depois = contagens(mundo["schema"])

    with pytest.raises(ExecutionRefused) as erro:
        executar(mundo["schema"], mundo["plano"], True, tmp_path, dump_valido)
    assert erro.value.motivos
    assert contagens(mundo["schema"]) == depois, "a segunda passagem mexeu"


# ── 9. Backup ────────────────────────────────────────────────

def test_o_backup_e_conferido_antes_da_exclusao(mundo, dump_valido, tmp_path):
    relatorio = executar(mundo["schema"], mundo["plano"], True, tmp_path, dump_valido)
    backup = relatorio["backup"]
    assert backup["estado"] == "restauracao conferida"
    assert backup["results"] == 4
    assert backup["result_drivers"] == 8
    assert Path(backup["caminho"]).exists()


def test_backup_que_nao_reproduz_as_linhas_recusa_antes_de_apagar(
        mundo, dump_valido, tmp_path, monkeypatch):
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
        executar(mundo["schema"], mundo["plano"], True, tmp_path, dump_valido)
    assert any("nao reproduz" in m for m in erro.value.motivos)
    assert contagens(mundo["schema"]) == antes


def test_o_backup_restaura_as_linhas_removidas(mundo, dump_valido, tmp_path):
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

    relatorio = executar(mundo["schema"], mundo["plano"], True, tmp_path, dump_valido)
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


# ── Ordenacao do digest ──────────────────────────────────────

# A ordenacao que esta PR substitui: pela coluna do filtro, que em
# `result_drivers` e `result_id` e repete sempre que um result tem mais de
# um piloto. Fica aqui para que o teste abaixo possa exibir o defeito.
ORDENACAO_ANTIGA = (
    'SELECT coalesce(md5(string_agg((t.*)::text, chr(29) '
    'ORDER BY t."{coluna}")), %s) FROM "{tabela}" t '
    'WHERE t."{coluna}" = ANY(%s)')


def digest_antigo(cur, tabela, ids, coluna="result_id"):
    cur.execute(ORDENACAO_ANTIGA.format(tabela=tabela, coluna=coluna),
                ("", list(ids)))
    return cur.fetchone()[0]


def removiveis_de(plano) -> list[int]:
    return sorted(i for s in plano["sessions"] for i in s["ids_removiveis"])


def restaurar_invertido(cur, ids) -> int:
    """
    Os mesmos vinculos numa temporaria, inseridos na ordem inversa.

    E o que `verify_backup` faz com o arquivo, com a diferenca de que aqui a
    ordem fisica e deliberadamente outra: um backup relido nao tem de chegar
    na mesma ordem em que foi gravado para ser o mesmo backup.
    """
    colunas = table_columns(cur, "result_drivers")
    nomes = ", ".join(f'"{c}"' for c in colunas)
    cur.execute('CREATE TEMP TABLE restaurado (LIKE result_drivers '
                'INCLUDING DEFAULTS) ON COMMIT DROP')
    cur.execute(f'SELECT {nomes} FROM result_drivers '
                f'WHERE result_id = ANY(%s) ORDER BY id DESC', (ids,))
    linhas = cur.fetchall()
    marcas = ", ".join(["%s"] * len(colunas))
    for linha in linhas:
        cur.execute(f'INSERT INTO restaurado ({nomes}) VALUES ({marcas})',
                    linha)
    return len(linhas)


def test_a_coluna_do_filtro_nao_ordena_os_vinculos(mundo):
    """
    A premissa do defeito, medida: `result_id` repete, a linha inteira nao.
    """
    ids = removiveis_de(mundo["plano"])
    conn = connect(mundo["schema"], autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM (SELECT result_id FROM "
                        "result_drivers WHERE result_id = ANY(%s) "
                        "GROUP BY result_id HAVING count(*) > 1) t", (ids,))
            assert cur.fetchone()[0] == len(ids), (
                "todo result removivel precisa de mais de um vinculo, "
                "senao o defeito nao tem como aparecer")
            cur.execute("SELECT count(*), count(DISTINCT (t.*)::text) "
                        "FROM result_drivers t WHERE t.result_id = ANY(%s)",
                        (ids,))
            total, distintas = cur.fetchone()
            assert total == distintas, "a linha inteira e uma ordem total"
    finally:
        conn.close()


def test_o_digest_atravessa_vinculos_restaurados_em_outra_ordem(mundo):
    """
    O defeito que recusou a execucao de 27/10, reduzido ao minimo.

    As mesmas linhas, de um lado as vivas e do outro as mesmas repostas em
    ordem inversa. Nada mudou, entao o digest nao pode mudar.
    """
    ids = removiveis_de(mundo["plano"])
    conn = connect(mundo["schema"])
    try:
        with conn.cursor() as cur:
            assert restaurar_invertido(cur, ids) == 8
            assert (row_digest(cur, "result_drivers", ids, "result_id")
                    == row_digest(cur, "restaurado", ids, "result_id")), (
                "linhas identicas em ordem diferente tem de dar o mesmo "
                "digest")
    finally:
        conn.rollback()
        conn.close()


def test_a_ordenacao_antiga_nao_reproduzia_os_mesmos_vinculos(mundo):
    """
    O mesmo par de tabelas sob a ordenacao anterior, que desempatava pelo
    `result_id` e por isso deixava a ordem dentro do empate por conta do
    executor da consulta.
    """
    ids = removiveis_de(mundo["plano"])
    conn = connect(mundo["schema"])
    try:
        with conn.cursor() as cur:
            restaurar_invertido(cur, ids)
            vivo = digest_antigo(cur, "result_drivers", ids)
            restaurado = digest_antigo(cur, "restaurado", ids)
            if vivo == restaurado:
                pytest.skip(
                    "este PostgreSQL devolveu o empate na mesma ordem nas "
                    "duas tabelas; a ordenacao antiga continua sem garantir "
                    "isso, e quem prova a correcao e o teste acima")
            assert vivo != restaurado
    finally:
        conn.rollback()
        conn.close()


def test_backup_relido_em_outra_ordem_e_aceito(mundo, dump_valido, tmp_path,
                                               monkeypatch):
    """
    O mesmo defeito pelo caminho inteiro: `verify_backup` com um arquivo
    cujas linhas chegam em ordem inversa. Um backup nao e uma ordem, e a
    execucao tem de seguir.
    """
    import database.recovery.dedup_execute as executor
    original = executor.backup_payload

    def inverte(cur, ids):
        payload = original(cur, ids)
        for tabela in ("results", "result_drivers"):
            payload[tabela]["linhas"].reverse()
        return payload

    monkeypatch.setattr(executor, "backup_payload", inverte)
    relatorio = executar(mundo["schema"], mundo["plano"], True, tmp_path,
                         dump_valido)
    assert relatorio["backup"]["estado"] == "restauracao conferida"
    assert relatorio["results_removidos"] == 4


@pytest.mark.parametrize("coluna, valor", [("driver_id", 999999), ("slot", 9)])
def test_vinculo_adulterado_no_backup_e_recusado(
        mundo, dump_valido, tmp_path, monkeypatch, coluna, valor):
    """
    A correcao nao pode comprar a igualdade: uma troca real de piloto ou de
    slot continua sendo divergencia, e nada e apagado.
    """
    import database.recovery.dedup_execute as executor
    original = executor.backup_payload

    def adultera(cur, ids):
        payload = original(cur, ids)
        onde = payload["result_drivers"]["colunas"].index(coluna)
        payload["result_drivers"]["linhas"][0][onde] = valor
        return payload

    monkeypatch.setattr(executor, "backup_payload", adultera)
    antes = contagens(mundo["schema"])
    with pytest.raises(ExecutionRefused) as erro:
        executar(mundo["schema"], mundo["plano"], True, tmp_path, dump_valido)
    assert any("result_drivers nao reproduz" in m for m in erro.value.motivos)
    assert contagens(mundo["schema"]) == antes


# ── Sobrescrita de backup ────────────────────────────────────

def test_backup_existente_nao_e_sobrescrito(mundo, dump_valido, tmp_path):
    """
    O nome carrega o digest do plano, entao um arquivo ja ali pertence a uma
    tentativa anterior deste mesmo plano — e pode ser a unica copia do que
    ela levou.
    """
    anterior = tmp_path / f"dedup-{mundo['plano']['plan_digest'][:16]}.json"
    anterior.write_text("backup de uma tentativa anterior", encoding="utf-8")
    antes = contagens(mundo["schema"])

    with pytest.raises(ExecutionRefused) as erro:
        executar(mundo["schema"], mundo["plano"], True, tmp_path, dump_valido)
    assert any("nao sobrescreve backup" in m for m in erro.value.motivos)
    assert contagens(mundo["schema"]) == antes
    assert anterior.read_text(encoding="utf-8") ==         "backup de uma tentativa anterior"


def test_o_backup_gravado_confere_consigo_mesmo(mundo, dump_valido, tmp_path):
    relatorio = executar(mundo["schema"], mundo["plano"], True, tmp_path,
                         dump_valido)
    backup = relatorio["backup"]
    bruto = Path(backup["caminho"]).read_bytes()
    assert hashlib.sha256(bruto).hexdigest() == backup["sha256"]
    assert backup["bytes"] == len(bruto)
    assert backup["plan_digest"] == mundo["plano"]["plan_digest"]
    assert json.loads(bruto)["plan_digest"] == mundo["plano"]["plan_digest"]
    assert not list(tmp_path.glob("*.parcial")), "sobrou arquivo parcial"


# ── Escrita concorrente ──────────────────────────────────────

def test_escritor_concorrente_impede_a_execucao(mundo, dump_valido, tmp_path):
    """
    O advisory lock nao alcanca quem nao o usa. Aqui uma transacao comum
    segura ROW EXCLUSIVE em `results`, e e isso que tem de barrar.
    """
    escritor = connect(mundo["schema"])          # autocommit=False
    try:
        with escritor.cursor() as cur:
            cur.execute("INSERT INTO cars (number, team_id, car_class) "
                        "SELECT '77', team_id, 'PC' FROM cars LIMIT 1 "
                        "RETURNING id")
            carro = cur.fetchone()[0]
            cur.execute("INSERT INTO results (session_id, car_id, status) "
                        "VALUES (%s, %s, 'Classified')",
                        (mundo["sessoes"][0], carro))
        # transacao deliberadamente aberta

        antes = contagens(mundo["schema"])
        with pytest.raises(ExecutionRefused) as erro:
            executar(mundo["schema"], mundo["plano"], True, tmp_path,
                     dump_valido)
        assert any("escritor ativo" in m for m in erro.value.motivos)
        assert contagens(mundo["schema"]) == antes
        assert not list(tmp_path.glob("dedup-*.json")),             "recusou mas gravou backup"
    finally:
        escritor.rollback()
        escritor.close()


def test_o_dry_run_aponta_o_escritor_sem_barrar_ninguem(mundo):
    escritor = connect(mundo["schema"])
    try:
        with escritor.cursor() as cur:
            cur.execute("UPDATE results SET status = 'Classified' "
                        "WHERE id = %s", (mundo["triplicada"]["4"][0],))

        relatorio = executar(mundo["schema"], mundo["plano"])
        assert relatorio["aplicado"] is False
        pids = {e["pid"] for e in relatorio["escritores_concorrentes"]}
        assert pids, "o dry-run devia ver o escritor"
        assert "results" in {e["tabela"]
                             for e in relatorio["escritores_concorrentes"]}
    finally:
        escritor.rollback()
        escritor.close()


# ── O dump do cluster ────────────────────────────────────────

def test_sem_dump_a_execucao_e_recusada(mundo, tmp_path):
    with pytest.raises(ExecutionRefused) as erro:
        executar(mundo["schema"], mundo["plano"], True, tmp_path, None)
    assert any("--pg-dump" in m for m in erro.value.motivos)
    assert contagens(mundo["schema"])[0] == 11


def test_dump_sem_atestacao_e_recusado(mundo, tmp_path):
    solto = tmp_path / "solto.dump"
    solto.write_bytes(b"um arquivo qualquer")
    with pytest.raises(ExecutionRefused) as erro:
        executar(mundo["schema"], mundo["plano"], True, tmp_path, solto)
    assert any("evidencia" in m for m in erro.value.motivos)


def test_dump_velho_e_recusado(mundo, tmp_path):
    velho = atestar_dump(tmp_path / "velho", mundo["schema"], idade_horas=48)
    with pytest.raises(ExecutionRefused) as erro:
        executar(mundo["schema"], mundo["plano"], True, tmp_path, velho,
                 max_age_hours=6)
    assert any("acima do limite" in m for m in erro.value.motivos)
    assert contagens(mundo["schema"])[0] == 11


def test_dump_que_nao_e_mais_pre_imagem_e_recusado(mundo, tmp_path):
    """
    Mesmo cluster, mesma idade, restauracao verificada — mas tirado quando
    `results` tinha outra contagem. Nao e um caminho de volta.
    """
    desatualizado = atestar_dump(tmp_path / "antigo", mundo["schema"],
                                 contagens={"results": 999})
    with pytest.raises(ExecutionRefused) as erro:
        executar(mundo["schema"], mundo["plano"], True, tmp_path,
                 desatualizado)
    assert any("pre-imagem" in m for m in erro.value.motivos)
    assert contagens(mundo["schema"])[0] == 11


def test_o_relatorio_registra_o_dump_usado(mundo, dump_valido, tmp_path):
    relatorio = executar(mundo["schema"], mundo["plano"], True, tmp_path,
                         dump_valido)
    dump = relatorio["dump"]
    assert dump["caminho"].endswith("cluster.dump")
    assert dump["restauracao_verificada"] is True
    assert dump["idade_horas"] < 1
    assert dump["contagens"]["results"] == 11


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
