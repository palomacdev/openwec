"""
Which CSV the loader accepts as a classification, and which it refuses.

A session folder holds up to four kinds of CSV, and only two of them have one
row per car. Reading a `23_Analysis_*` as a classification turns every lap into
a car: that is how 51 sessions came to hold 324.720 rows in `results` for 1.665
real entries. These tests pin the selection, including the case that caused it
— a folder whose only CSV is a lap log.

No database: this is file selection, and it runs anywhere.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "database" / "loader"))

from load_classification import (  # noqa: E402
    find_classification_sessions,
    get_final_classification,
    is_classification_file,
)

# ── Fixtures: one row of each of the four kinds, as the corpus writes them ──

# raw/alms/01_2022/.../Free Practice 1/classification/
CLASSIFICATION_CSV = (
    "POS;NUMBER;CLASS;TEAM;VEHICLE;LAPS;LAP;TIME;GAP_FIRST;GAP_PREVIOUS;"
    "KPH;GROUP;TIRES\n"
    "1;4;LMP2;Nielsen Racing;Oreca 07-Gibson;36;2;1:46.925;-;-;181.5;;M\n"
)

# raw/imsa/.../Practice 1/classification/03_Results.CSV
RESULTS_CSV = (
    "POSITION;NUMBER;CLASS;TEAM;VEHICLE;LAPS;TOTAL_TIME;STATUS;GAP_FIRST;"
    "GAP_PREVIOUS;FL_TIME;FL_LAPNUM;FL_KPH\n"
    "1;10;DPi;Wayne Taylor Racing;Cadillac DPi-V.R;24;45'02.112;Classified;;;"
    "1'39.233;12;184.2\n"
)

# raw/elms/07_2012/.../Race/other/6H/23_Analysis_Race.CSV — one row per LAP
ANALYSIS_CSV = (
    "NUMBER;DRIVER_NUMBER;LAP_NUMBER;LAP_TIME;S1;S2;S3;KPH;ELAPSED;HOUR;"
    "DRIVER_NAME;PIT_TIME;CROSSING_FINISH_LINE_IN_PIT\n"
    "4;1;1;1:52.004;33.1;41.2;37.7;172.1;1:52.004;14:02:11;A. Driver;;\n"
    "4;1;2;1:48.714;32.4;40.1;36.2;178.5;3:40.718;14:03:59;A. Driver;;\n"
)

# raw/.../Race/other/Hour 12/26_Weather.CSV
WEATHER_CSV = (
    "TIME_UTC_STR;TIME_UTC_SECONDS;AIR_TEMP;TRACK_TEMP;HUMIDITY;PRESSURE;"
    "WIND_SPEED;WIND_DIRECTION\n"
    "14:02:00;50520;21.4;31.8;48.0;1013.2;3.1;270\n"
)

KINDS = {
    "03_Classification_Free Practice 1.CSV": CLASSIFICATION_CSV,
    "03_Results.CSV":                        RESULTS_CSV,
    "23_Analysis_Race.CSV":                  ANALYSIS_CSV,
    "26_Weather.CSV":                        WEATHER_CSV,
}

# O conteudo vem da familia do arquivo, para que um teste possa usar qualquer
# grafia real sem repetir o CSV inteiro.
BY_PREFIX = {
    "03_Classification": CLASSIFICATION_CSV,
    "03_Results":        RESULTS_CSV,
    "05_Classification": RESULTS_CSV,
    "23_Analysis":       ANALYSIS_CSV,
    "26_Weather":        WEATHER_CSV,
}


def conteudo(nome: str) -> str:
    for prefixo, texto in BY_PREFIX.items():
        if nome.lower().startswith(prefixo.lower()):
            return texto
    raise AssertionError(f"nome fora das quatro familias conhecidas: {nome}")


def escrever(pasta: Path, *nomes: str) -> Path:
    pasta.mkdir(parents=True, exist_ok=True)
    for nome in nomes:
        (pasta / nome).write_text(conteudo(nome), encoding="utf-8")
    return pasta


# ── O reconhecedor ────────────────────────────────────────────

# Grafias reais, contadas no corpus: 2.591 arquivos 03_Classification* e
# 948 arquivos 03_Results*, contra 138 23_Analysis* e 435 26_Weather*.
@pytest.mark.parametrize("nome", [
    "03_Classification_Free Practice 1.CSV",
    "03_Classification_Race.CSV",
    "03_Classification_Race_Hour 6.CSV",
    "03_Classification_Qualifying Practice - LMP2 Pro-Am.CSV",
    "03_Classification_Hyperpole HYPERCAR.CSV",
    "03_Results.CSV",
    "03_Results_Practice 1.CSV",
    "03_Results_Race_Official.CSV",
    "03_Results Official.CSV",
    "03_Results Unofficial.CSV",
])
def test_as_grafias_legitimas_sao_aceitas(nome):
    assert is_classification_file(Path(nome)) is True


@pytest.mark.parametrize("nome", [
    "23_Analysis_Race.CSV",
    "23_Analysis_Race 1.CSV",
    "26_Weather.CSV",
    "26_Weather_Race.CSV",
    "26_Weather (Not Available).CSV",
])
def test_analise_e_clima_sao_recusados(nome):
    assert is_classification_file(Path(nome)) is False


def test_o_separador_sublinhado_nao_invalida_o_nome():
    """
    `\\b` nao casa entre `n` e `_`, porque `_` e caractere de palavra. Uma
    fronteira ingenua recusaria 2.591 arquivos legitimos — a regressao que
    este teste existe para impedir.
    """
    assert is_classification_file(Path("03_Classification_Race.CSV")) is True


def test_a_extensao_e_comparada_sem_caixa():
    assert is_classification_file(Path("03_Classification_Race.csv")) is True
    assert is_classification_file(Path("03_classification_race.CSV")) is True


def test_outra_extensao_nao_entra():
    assert is_classification_file(Path("03_Classification_Race.pdf")) is False


def test_o_prefixo_numerico_e_exigido():
    """Nenhum arquivo de classificacao do corpus usa outro prefixo."""
    assert is_classification_file(Path("Classification_Race.CSV")) is False
    assert is_classification_file(Path("23_Classification_Race.CSV")) is False


# ── A escolha dentro de uma pasta ─────────────────────────────

def test_a_classificacao_e_escolhida_entre_os_quatro_tipos(tmp_path):
    pasta = escrever(tmp_path / "classification", *KINDS)
    escolhido = get_final_classification(pasta)
    assert escolhido is not None
    assert escolhido.name == "03_Classification_Free Practice 1.CSV"


def test_pasta_so_com_analise_nao_tem_classificacao(tmp_path):
    """
    O caso que originou o defeito: nao ha arquivo de classificacao nenhum, e
    o loader caia no log de voltas por falta de alternativa.
    """
    pasta = escrever(tmp_path / "other", "23_Analysis_Race.CSV")
    assert get_final_classification(pasta) is None


def test_pasta_so_com_clima_nao_tem_classificacao(tmp_path):
    pasta = escrever(tmp_path / "other", "26_Weather.CSV")
    assert get_final_classification(pasta) is None


def test_pasta_sem_csv_algum(tmp_path):
    (tmp_path / "classification").mkdir()
    assert get_final_classification(tmp_path / "classification") is None


def test_pasta_inexistente(tmp_path):
    assert get_final_classification(tmp_path / "nao_existe") is None


def test_a_hora_final_continua_vencendo(tmp_path):
    base = tmp_path / "other"
    escrever(base / "Hour 1", "03_Classification_Race.CSV")
    escrever(base / "Hour 6", "03_Classification_Race.CSV")
    escrever(base / "24_Hour 24", "03_Classification_Race.CSV")
    escolhido = get_final_classification(base)
    assert escolhido is not None
    assert escolhido.parent.name == "24_Hour 24"


def test_uma_analise_numa_hora_maior_nao_vence_a_classificacao(tmp_path):
    """
    A ordenacao e por hora, entao sem o filtro um log de voltas na ultima hora
    ganharia da classificacao de uma hora anterior.
    """
    base = tmp_path / "other"
    escrever(base / "Hour 6", "03_Classification_Race.CSV")
    escrever(base / "Hour 24", "23_Analysis_Race.CSV", "26_Weather.CSV")
    escolhido = get_final_classification(base)
    assert escolhido is not None
    assert escolhido.name == "03_Classification_Race.CSV"
    assert escolhido.parent.name == "Hour 6"


# ── A varredura de raw/ ───────────────────────────────────────

def montar_sessao(raw: Path, sessao: str, pasta: str, *nomes: str) -> None:
    escrever(raw / "01_2019" / "01_EVENTO" / sessao / pasta, *nomes)


def test_a_sessao_com_classificacao_e_devolvida(tmp_path):
    montar_sessao(tmp_path, "Free Practice 1", "classification",
                  "03_Classification_Free Practice 1.CSV")
    sessoes = find_classification_sessions(tmp_path, "ELMS")
    assert len(sessoes) == 1
    assert sessoes[0]["season_raw"] == "01_2019"
    assert sessoes[0]["event_raw"] == "01_EVENTO"
    assert sessoes[0]["session_name"] == "Free Practice 1"
    assert sessoes[0]["csv_path"].name == "03_Classification_Free Practice 1.CSV"


def test_a_sessao_so_com_analise_fica_de_fora(tmp_path):
    montar_sessao(tmp_path, "Race", "other", "23_Analysis_Race.CSV")
    assert find_classification_sessions(tmp_path, "ELMS") == []


def test_a_ausencia_e_registrada(tmp_path, capsys):
    """Requisito: registrar a ausencia, nao apenas ignorar em silencio."""
    montar_sessao(tmp_path, "Race", "other", "23_Analysis_Race.CSV")
    find_classification_sessions(tmp_path, "ELMS")
    saida = capsys.readouterr().out
    assert "[NO CLASSIFICATION]" in saida
    assert "01_EVENTO" in saida and "Race" in saida


def test_pasta_vazia_nao_gera_aviso(tmp_path, capsys):
    """Sem CSV algum nao ha o que relatar: o aviso e sobre o arquivo errado."""
    (tmp_path / "01_2019" / "01_EVENTO" / "Race" / "classification").mkdir(parents=True)
    find_classification_sessions(tmp_path, "ELMS")
    assert "[NO CLASSIFICATION]" not in capsys.readouterr().out


def test_as_sessoes_boas_sobrevivem_as_ruins(tmp_path):
    montar_sessao(tmp_path, "Free Practice 1", "classification",
                  "03_Classification_Free Practice 1.CSV")
    montar_sessao(tmp_path, "Race", "other", "23_Analysis_Race.CSV", "26_Weather.CSV")
    montar_sessao(tmp_path, "Qualifying", "classification", "03_Results.CSV")
    nomes = sorted(s["session_name"] for s in find_classification_sessions(tmp_path, "ELMS"))
    assert nomes == ["Free Practice 1", "Qualifying"]


def test_o_recuo_para_other_continua_valendo(tmp_path):
    """
    Sem pasta `classification`, a busca passa para `other` — e e dali que vem
    a classificacao de corrida do dialeto A. O filtro nao pode derrubar isso.
    """
    montar_sessao(tmp_path, "Race", "other/Hour 6", "03_Classification_Race.CSV")
    sessoes = find_classification_sessions(tmp_path, "ELMS")
    assert len(sessoes) == 1
    assert sessoes[0]["csv_path"].parent.name == "Hour 6"


def test_o_nivel_extra_da_imsa_continua_sendo_percorrido(tmp_path):
    escrever(tmp_path / "17_2017" / "02_Daytona" / "01_WeatherTech" / "Race"
             / "other" / "Hour 24", "03_Results.CSV", "26_Weather (Not Available).CSV")
    sessoes = find_classification_sessions(tmp_path, "IMSA")
    assert len(sessoes) == 1
    assert sessoes[0]["event_raw"] == "02_Daytona"
    assert sessoes[0]["session_name"] == "Race"
    assert sessoes[0]["csv_path"].name == "03_Results.CSV"


def test_a_imsa_so_com_clima_fica_de_fora(tmp_path):
    escrever(tmp_path / "17_2017" / "02_Daytona" / "01_WeatherTech" / "Race"
             / "other" / "Hour 24", "26_Weather (Not Available).CSV")
    assert find_classification_sessions(tmp_path, "IMSA") == []


def test_raw_inexistente_nao_quebra(tmp_path):
    assert find_classification_sessions(tmp_path / "nao_existe", "ELMS") == []


# ── A familia 05_, aceita para que uma recuperacao a alcance ──

# As 51 sessoes contaminadas nao tem `03_` nenhum: o que o publicador oferece
# para elas chama-se `05_Classification*`. Nenhum arquivo dessa familia existe
# em `raw/`, entao o que a arvore local seleciona hoje nao muda.
@pytest.mark.parametrize("nome", [
    "05_Classification_Race.CSV",
    "05_Classification_Race_Hour 2.CSV",
    "05_Classification_Race_Race.CSV",
    "05_classification_race.csv",
])
def test_a_familia_05_e_aceita(nome):
    assert is_classification_file(Path(nome)) is True


@pytest.mark.parametrize("nome", [
    "05_Results by Hour.CSV",      # existe, mas nao foi verificada aqui
    "05_Analysis.CSV",
    "90_Classification_Qualifying Results.CSV",
    "05Classification_Race.CSV",
])
def test_o_que_nao_e_05_classification_continua_fora(nome):
    assert is_classification_file(Path(nome)) is False


def test_dentro_da_mesma_hora_vence_o_nome_sem_sufixo(tmp_path):
    """
    As duas grafias da mesma hora nao carregam o mesmo dialeto.

    `MEASURED` na Le Mans Cup 2018, Paul Ricard e Monza: o nome simples traz
    93 colunas de cabecalho sobre linhas de 73 campos, com os 14 campos que o
    parser le; o `_Hour N` traz o terceiro dialeto, `Pos;Number;Driver 1;…`,
    em que `field(row, "NUMBER")` nao encontra nada e toda linha e
    descartada. Escolher o nome simples e escolher o arquivo legivel.
    """
    pasta = escrever(tmp_path / "Hour 2",
                     "05_Classification_Race.CSV",
                     "05_Classification_Race_Hour 2.CSV")
    escolhido = get_final_classification(pasta)
    assert escolhido is not None
    assert escolhido.name == "05_Classification_Race.CSV"


def test_a_hora_maior_continua_vencendo_o_sufixo(tmp_path):
    """O desempate e o segundo criterio, nunca o primeiro."""
    pasta = tmp_path / "classification"
    escrever(pasta / "Hour 1", "05_Classification_Race.CSV")
    escrever(pasta / "Hour 2", "05_Classification_Race_Hour 2.CSV")
    escolhido = get_final_classification(pasta)
    assert escolhido is not None
    assert escolhido.parent.name == "Hour 2"


# ── A hora escrita antes da palavra ───────────────────────────

# `MEASURED`: o catalogo da ELMS usa `1H`..`6H` e `1 Hour`/`3 Hours` em 152
# arquivos, e `raw/` tem seis pastas assim. Sem reconhece-las, `hour_key`
# devolve 0 para todas e o desempate alfabetico escolhe a PRIMEIRA hora —
# um retrato do meio da corrida apresentado como classificacao final.
def test_a_hora_abreviada_na_pasta_e_lida(tmp_path):
    pasta = tmp_path / "classification"
    escrever(pasta / "1H", "05_Classification_Race_1H.CSV")
    escrever(pasta / "6H", "05_Classification_Race_6H.CSV")
    escolhido = get_final_classification(pasta)
    assert escolhido is not None
    assert escolhido.parent.name == "6H"


def test_a_hora_por_extenso_antes_da_palavra_e_lida(tmp_path):
    pasta = tmp_path / "classification"
    escrever(pasta / "1 Hour", "05_Classification_Race_1 Hour.CSV")
    escrever(pasta / "2 Hours", "05_Classification_Race_2 Hours.CSV")
    escrever(pasta / "3 Hours", "05_Classification_Race_3 Hours.CSV")
    escolhido = get_final_classification(pasta)
    assert escolhido is not None
    assert escolhido.parent.name == "3 Hours"


def test_a_corrida_numerada_nao_vira_hora(tmp_path):
    """
    `Race 1` e uma corrida, nao uma hora, e `Race 2` tambem nao.

    A grafia so e lida quando a pasta nao e outra coisa, ou quando o numero
    e a ultima coisa do nome antes da extensao.
    """
    pasta = tmp_path / "classification"
    escrever(pasta / "Race 1", "05_Classification_Race 1.CSV")
    escrever(pasta / "Race 2", "05_Classification_Race 2.CSV")
    escolhido = get_final_classification(pasta)
    assert escolhido is not None
    assert escolhido.name == "05_Classification_Race 1.CSV"


def test_hour_n_continua_vencendo_a_grafia_abreviada(tmp_path):
    """As duas grafias convivem, e a comparacao e numerica dos dois lados."""
    pasta = tmp_path / "classification"
    escrever(pasta / "2H", "05_Classification_Race_2H.CSV")
    escrever(pasta / "Hour 4", "05_Classification_Race_Hour 4.CSV")
    escolhido = get_final_classification(pasta)
    assert escolhido is not None
    assert escolhido.parent.name == "Hour 4"
