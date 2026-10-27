"""
Os dois mecanismos que multiplicaram a ELMS, fechados por teste.

Nenhum deles existe no código de hoje. Estes testes existem para que não
voltem, e são escritos contra o caminho real do loader — a caminhada pela
árvore, a seleção do arquivo e a carga —, não contra funções substituídas.

  A  uma sessão que só publicou análise de voltas. `get_final_classification`
     globava qualquer `*.CSV` e `find_classification_sessions` recua de
     `classification/` para `other/`, então o log de voltas era lido como
     classificação e o loader gravava um carro e um resultado por volta
     (9a0bfc2, #40). A sessão 970 da ELMS tem 6.772 `results` vazios para
     3.386 voltas por causa disso.

  B  releitura do mesmo arquivo. `upsert_car` inseria com `ON CONFLICT DO
     NOTHING` sem alvo contra uma tabela cuja única restrição única é a
     própria PK, então nunca havia conflito, todo carro era novo e
     `UNIQUE (session_id, car_id)` dava um `results` novo junto (65bdd0e,
     #39). Três ondas de ingestão deixaram fator 3 em 811 sessões da ELMS.

O terceiro mecanismo — quem pode escrever em `results` — é uma varredura
estática do repositório e não pertence a uma suíte de loader: vive em
`tests/guards/test_results_writers.py`, com o alcance declarado lá.

A árvore `raw/` é sintética e vive em `tmp_path`; o acervo não é lido. Toda
escrita cai no schema do módulo, criado vazio e derrubado inteiro — ver
conftest.py.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "database" / "loader"))

psycopg2 = pytest.importorskip("psycopg2")

from tests.loader.sandbox import connect_to_sandbox  # noqa: E402
import load_classification as lc  # noqa: E402
from load_classification import (  # noqa: E402
    find_classification_sessions,
    get_final_classification,
    is_classification_file,
)


# ── A árvore sintética ───────────────────────────────────────

CLASSIFICACAO = (
    "POS;NUMBER;TEAM;VEHICLE;CLASS;TYRES;LAPS;TIME;GAP_FIRST;GAP_PREVIOUS;KPH\n"
)
ANALISE = (
    "NUMBER; DRIVER_NUMBER; LAP_NUMBER; LAP_TIME; LAP_IMPROVEMENT; "
    "CROSSING_FINISH_LINE_IN_PIT; S1; S2; S3; KPH; ELAPSED; HOUR; "
    "TOP_SPEED; DRIVER_NAME; PIT_TIME\n"
)

SEASON_RAW = "99_2099"
EVENT_RAW = "01_CIRCUITO SINTETICO"
SESSION_NAME = "Race"


def escreve_classificacao(destino: Path, carros) -> Path:
    """Um `03_Classification_*.CSV` com uma linha por carro."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    linhas = [CLASSIFICACAO]
    for pos, (numero, equipe, veiculo, classe, voltas) in enumerate(carros, 1):
        linhas.append(
            f"{pos};{numero};{equipe};{veiculo};{classe};M;{voltas};"
            f"1:00'0{pos}.000;-;-;180.0\n")
    destino.write_text("".join(linhas), encoding="utf-8")
    return destino


def escreve_analise(destino: Path, carros, voltas_por_carro: int) -> Path:
    """Um `23_Analysis_*.CSV`: uma linha por volta, sem equipe nem classe."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    linhas = [ANALISE]
    for numero, piloto in carros:
        for volta in range(1, voltas_por_carro + 1):
            linhas.append(
                f"{numero};1;{volta};1:5{volta % 10}.000;0;;35.0;30.0;48.0;"
                f"180.0;{volta}:00.000;12:0{volta % 10};270.0;{piloto};\n")
    destino.write_text("".join(linhas), encoding="utf-8")
    return destino


def arvore_so_com_analise(tmp_path: Path, voltas_por_carro: int = 4) -> Path:
    """
    A forma exata da sessão 970: `Race/` sem `classification/`, com a análise
    sob `other/`, que é a pasta para onde o loader recua.
    """
    raiz = tmp_path / "elms"
    sessao = raiz / SEASON_RAW / EVENT_RAW / SESSION_NAME
    escreve_analise(sessao / "other" / "6H" / "23_Analysis_Race.CSV",
                    [("4", "Piloto Um"), ("22", "Piloto Dois")],
                    voltas_por_carro)
    return raiz


def semeia_catalogo(sandbox) -> dict:
    """Série, temporada, evento e sessão, para que a resolução encontre a linha."""
    conn = connect_to_sandbox(sandbox)
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO series (key, name) VALUES ('ELMS','ELMS') "
                        "RETURNING id")
            serie = cur.fetchone()[0]
            cur.execute("INSERT INTO seasons (series_id, label, raw_id, year) "
                        "VALUES (%s, '2099', %s, 2099) RETURNING id",
                        (serie, SEASON_RAW))
            temporada = cur.fetchone()[0]
            cur.execute("INSERT INTO events (season_id, name, raw_id) "
                        "VALUES (%s, 'Circuito Sintetico', %s) RETURNING id",
                        (temporada, EVENT_RAW))
            evento = cur.fetchone()[0]
            cur.execute("INSERT INTO sessions (event_id, raw_id, name) "
                        "VALUES (%s, %s, %s) RETURNING id",
                        (evento, f"209901011200_{SESSION_NAME}", SESSION_NAME))
            sessao = cur.fetchone()[0]
        conn.commit()
        return {"serie": serie, "evento": evento, "sessao": sessao}
    finally:
        conn.close()


def contagens(sandbox) -> dict:
    conn = connect_to_sandbox(sandbox)
    try:
        with conn.cursor() as cur:
            saida = {}
            for tabela in ("results", "cars", "teams"):
                cur.execute(f"SELECT count(*) FROM {tabela}")
                saida[tabela] = cur.fetchone()[0]
            return saida
    finally:
        conn.close()


def limpa(sandbox) -> None:
    conn = connect_to_sandbox(sandbox)
    try:
        with conn.cursor() as cur:
            for tabela in ("results", "cars", "sessions", "events", "seasons",
                           "series", "teams", "ingest_log"):
                cur.execute(f"TRUNCATE {tabela} CASCADE")
        conn.commit()
    finally:
        conn.close()


def carrega(sandbox, raiz: Path, monkeypatch, capsys) -> str:
    """
    `lc.run()` de verdade sobre a árvore dada. Devolve a saída.

    `psycopg2.connect` é trocado à mão e devolvido no `finally`, não pelo
    monkeypatch: o alvo é o módulo `psycopg2` inteiro, que `connect_to_sandbox`
    também usa, e um patch que durasse até o fim do teste faria a conferência
    seguinte receber esta mesma conexão — já fechada por `run`.
    """
    monkeypatch.setattr(lc, "RAW_DIRS", {"ELMS": raiz})
    monkeypatch.setattr(lc, "_writes_are_blocked", lambda: False)

    ligacao = connect_to_sandbox(sandbox)
    original = lc.psycopg2.connect
    lc.psycopg2.connect = lambda **kw: ligacao
    try:
        lc.run()
    finally:
        lc.psycopg2.connect = original
        if not ligacao.closed:
            ligacao.close()
    return capsys.readouterr().out


@pytest.fixture
def mundo(sandbox):
    limpa(sandbox)
    dados = semeia_catalogo(sandbox)
    yield dados
    limpa(sandbox)


# ── A. A sessão que só publicou análise de voltas ────────────

def test_a_analise_nao_passa_pelo_filtro_de_classificacao(tmp_path):
    """O filtro, isolado: nem a análise nem o clima são classificação."""
    pasta = tmp_path / "other"
    pasta.mkdir()
    for nome in ("23_Analysis_Race.CSV", "26_Weather_Race.CSV",
                 "23_AnalysisEnduranceWithSections_Race.CSV"):
        (pasta / nome).write_text("x", encoding="utf-8")
    assert not any(is_classification_file(p) for p in pasta.iterdir())
    assert get_final_classification(pasta) is None


def test_a_sessao_so_com_analise_nao_e_descoberta(tmp_path):
    """
    O caminho real de descoberta sobre a árvore sintética.

    `find_classification_sessions` recua de `classification/` para `other/`
    — e é esse recuo que fazia o log de voltas ser lido. O recuo continua;
    o que ele aceita ler é que mudou.
    """
    raiz = arvore_so_com_analise(tmp_path)
    assert (raiz / SEASON_RAW / EVENT_RAW / SESSION_NAME / "other" / "6H"
            / "23_Analysis_Race.CSV").exists(), "a árvore não foi montada"
    assert find_classification_sessions(raiz, "ELMS") == []


def test_a_sessao_so_com_analise_nao_grava_results_nem_cars(
        sandbox, mundo, tmp_path, monkeypatch, capsys):
    """
    A carga inteira sobre essa árvore: nenhuma linha, e nenhum erro.

    Com o comportamento antigo seriam 2 carros × 4 voltas = 8 `results` e 8
    `cars`, todos sem posição, sem classe e com equipe `Unknown`.
    """
    antes = contagens(sandbox)
    assert antes["results"] == 0 and antes["cars"] == 0

    saida = carrega(sandbox, arvore_so_com_analise(tmp_path), monkeypatch,
                    capsys)

    assert contagens(sandbox) == antes, "a análise virou linha no banco"
    assert "[NO CLASSIFICATION]" in saida, "a ausência não foi relatada"
    assert "Traceback" not in saida


def test_a_mesma_arvore_com_classificacao_grava(sandbox, mundo, tmp_path,
                                                monkeypatch, capsys):
    """
    O controle, sem o qual o teste acima passaria pelo motivo errado.

    Mesma árvore, mesma sessão, mesma carga — só que agora com uma
    classificação de verdade ao lado da análise.
    """
    raiz = arvore_so_com_analise(tmp_path)
    escreve_classificacao(
        raiz / SEASON_RAW / EVENT_RAW / SESSION_NAME / "classification"
        / "03_Classification_Race.CSV",
        [("4", "EQUIPE UM", "Oreca 07-Gibson", "LMP2", 31)])

    carrega(sandbox, raiz, monkeypatch, capsys)

    depois = contagens(sandbox)
    assert depois["results"] == 1, "a classificação não foi carregada"
    assert depois["cars"] == 1


def test_a_classificacao_vence_a_analise_na_mesma_pasta(sandbox, mundo,
                                                        tmp_path, monkeypatch,
                                                        capsys):
    """
    Os dois tipos dentro de `other/`, que é como o acervo os guarda.

    O que era escolhido ali era o que o glob devolvesse primeiro.
    """
    raiz = tmp_path / "elms"
    outra = raiz / SEASON_RAW / EVENT_RAW / SESSION_NAME / "other"
    escreve_analise(outra / "23_Analysis_Race.CSV", [("4", "Piloto Um")], 5)
    escreve_classificacao(outra / "03_Classification_Race.CSV",
                          [("4", "EQUIPE UM", "Oreca 07-Gibson", "LMP2", 31)])

    descobertas = find_classification_sessions(raiz, "ELMS")
    assert len(descobertas) == 1
    assert descobertas[0]["csv_path"].name == "03_Classification_Race.CSV"

    carrega(sandbox, raiz, monkeypatch, capsys)
    assert contagens(sandbox)["results"] == 1, "a análise entrou junto"


# ── B. Reingestão do mesmo arquivo ───────────────────────────

def estado_das_linhas(sandbox) -> list[tuple]:
    conn = connect_to_sandbox(sandbox)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT r.session_id, r.car_id, c.number, c.vehicle, "
                "       c.car_class, r.position, r.laps_completed "
                "  FROM results r JOIN cars c ON c.id = r.car_id "
                " ORDER BY c.number")
            return cur.fetchall()
    finally:
        conn.close()


@pytest.fixture
def arvore_com_dois_carros(tmp_path):
    raiz = tmp_path / "elms"
    caminho = (raiz / SEASON_RAW / EVENT_RAW / SESSION_NAME / "classification"
               / "03_Classification_Race.CSV")
    escreve_classificacao(caminho, [
        ("4", "EQUIPE UM", "Oreca 07-Gibson", "LMP2", 31),
        ("22", "EQUIPE DOIS", "Ligier JS P217", "LMP2", 30),
    ])
    return raiz, caminho


def test_reingestao_reutiliza_o_carro_e_nao_multiplica(
        sandbox, mundo, arvore_com_dois_carros, monkeypatch, capsys):
    """
    Duas cargas do mesmo arquivo, no mesmo banco.

    Com o comportamento antigo a segunda passagem mintava dois carros novos
    e, por tabela, dois `results` novos: quatro linhas onde há duas.
    """
    raiz, _ = arvore_com_dois_carros

    carrega(sandbox, raiz, monkeypatch, capsys)
    primeira = estado_das_linhas(sandbox)
    assert len(primeira) == 2, "a primeira carga não gravou as duas linhas"

    carrega(sandbox, raiz, monkeypatch, capsys)
    segunda = estado_das_linhas(sandbox)

    assert len(segunda) == 2, "a reingestão multiplicou as linhas"
    assert [l[1] for l in segunda] == [l[1] for l in primeira], \
        "os car_id mudaram: a segunda carga mintou carros novos"
    assert [(l[0], l[1]) for l in segunda] == [(l[0], l[1]) for l in primeira], \
        "o par (session_id, car_id) não se manteve"
    assert contagens(sandbox)["cars"] == 2


def test_a_segunda_carga_atualiza_no_lugar(
        sandbox, mundo, arvore_com_dois_carros, monkeypatch, capsys):
    """
    O arquivo é revisto e recarregado: a linha é corrigida, não duplicada.

    Posição e voltas são campos do resultado, não da identidade do carro —
    então o carro é o mesmo e o `ON CONFLICT (session_id, car_id)` atualiza.
    """
    raiz, caminho = arvore_com_dois_carros
    carrega(sandbox, raiz, monkeypatch, capsys)
    antes = {l[2]: (l[1], l[5], l[6]) for l in estado_das_linhas(sandbox)}

    escreve_classificacao(caminho, [
        ("22", "EQUIPE DOIS", "Ligier JS P217", "LMP2", 44),
        ("4", "EQUIPE UM", "Oreca 07-Gibson", "LMP2", 43),
    ])
    carrega(sandbox, raiz, monkeypatch, capsys)
    depois = {l[2]: (l[1], l[5], l[6]) for l in estado_das_linhas(sandbox)}

    assert set(depois) == set(antes)
    for numero in antes:
        assert depois[numero][0] == antes[numero][0], \
            f"o carro #{numero} trocou de id"
    assert depois["22"][2] == 44 and depois["4"][2] == 43, \
        "as voltas não foram atualizadas"
    assert depois["22"][1] == 1 and depois["4"][1] == 2, \
        "a posição não acompanhou a revisão"
    assert contagens(sandbox)["results"] == 2


def test_identidades_distintas_continuam_distintas(
        sandbox, mundo, arvore_com_dois_carros, monkeypatch, capsys):
    """
    O risco oposto ao da duplicação: fundir o que a fonte mostra separado.

    Dois carros com o mesmo número e equipes diferentes são duas inscrições,
    e nenhuma releitura pode transformá-las numa só.
    """
    raiz, caminho = arvore_com_dois_carros
    escreve_classificacao(caminho, [
        ("4", "EQUIPE UM", "Oreca 07-Gibson", "LMP2", 31),
        ("4", "EQUIPE DOIS", "Ligier JS P217", "LMP2", 30),
    ])

    carrega(sandbox, raiz, monkeypatch, capsys)
    carrega(sandbox, raiz, monkeypatch, capsys)

    linhas = estado_das_linhas(sandbox)
    assert len(linhas) == 2, "as duas inscrições viraram uma, ou viraram quatro"
    assert {l[3] for l in linhas} == {"Oreca 07-Gibson", "Ligier JS P217"}
    assert len({l[1] for l in linhas}) == 2, "os dois carros compartilham id"
