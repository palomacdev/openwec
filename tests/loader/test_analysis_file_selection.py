"""
Which CSV the laps loader accepts as a lap analysis, and which it refuses.

An `analysis/` folder holds nothing but lap analyses, but the search falls
back to `other/`, where four kinds of CSV live together. Measured before the
fix: 82 classifications and 2 weather logs were being fed to a parser that
expects one row per lap.

No database: this is file selection, and it runs anywhere.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "database" / "loader"))

from load_laps import (  # noqa: E402
    find_analysis_sessions,
    get_final_analysis,
    is_analysis_file,
)

# ── Fixtures: one row of each kind, as the corpus writes them ──

# raw/wec/.../Race/analysis/23_Analysis_Race.CSV — one row per LAP
ANALYSIS_CSV = (
    "NUMBER;DRIVER_NUMBER;LAP_NUMBER;LAP_TIME;S1;S2;S3;KPH;ELAPSED;HOUR;"
    "DRIVER_NAME;PIT_TIME;CLASS;GROUP;TEAM\n"
    "7;1;1;3:24.112;1:10.4;1:08.2;1:05.5;240.1;3:24.112;14:02:11;"
    "A. Driver;;HYPERCAR;;Toyota Gazoo Racing\n"
    "7;1;2;3:21.908;1:09.8;1:07.9;1:04.2;242.7;6:46.020;14:05:33;"
    "A. Driver;;HYPERCAR;;Toyota Gazoo Racing\n"
)

# The same rows plus intermediate-point columns the lap parser does not read.
ANALYSIS_SECTIONS_CSV = (
    "NUMBER;DRIVER_NUMBER;LAP_NUMBER;LAP_TIME;S1;S2;S3;KPH;ELAPSED;HOUR;"
    "DRIVER_NAME;PIT_TIME;CLASS;GROUP;TEAM;IM1_time;IM1_elapsed\n"
    "7;1;1;3:24.112;1:10.4;1:08.2;1:05.5;240.1;3:24.112;14:02:11;"
    "A. Driver;;HYPERCAR;;Toyota Gazoo Racing;24.112;24.112\n"
)

CLASSIFICATION_CSV = (
    "POSITION;NUMBER;CLASS;TEAM;VEHICLE;LAPS;TOTAL_TIME;STATUS;GAP_FIRST;"
    "GAP_PREVIOUS;FL_TIME;FL_LAPNUM;FL_KPH\n"
    "1;7;HYPERCAR;Toyota Gazoo Racing;Toyota GR010;31;1:00'01.868;Classified;;;"
    "3:21.908;2;242.7\n"
)

RESULTS_CSV = (
    "POSITION;NUMBER;CLASS;TEAM;VEHICLE;LAPS;TOTAL_TIME;STATUS\n"
    "1;10;DPi;Wayne Taylor Racing;Cadillac DPi-V.R;24;45'02.112;Classified\n"
)

WEATHER_CSV = (
    "TIME_UTC_STR;TIME_UTC_SECONDS;AIR_TEMP;TRACK_TEMP;HUMIDITY;PRESSURE;"
    "WIND_SPEED;WIND_DIRECTION\n"
    "14:02:00;50520;21.4;31.8;48.0;1013.2;3.1;270\n"
)

BY_PREFIX = {
    "23_AnalysisEnduranceWithSections": ANALYSIS_SECTIONS_CSV,
    "23_Analysis":        ANALYSIS_CSV,
    "03_Classification":  CLASSIFICATION_CSV,
    "03_Results":         RESULTS_CSV,
    "26_Weather":         WEATHER_CSV,
}


def conteudo(nome: str) -> str:
    for prefixo, texto in BY_PREFIX.items():
        if nome.lower().startswith(prefixo.lower()):
            return texto
    raise AssertionError(f"nome fora das familias conhecidas: {nome}")


def escrever(pasta: Path, *nomes: str) -> Path:
    pasta.mkdir(parents=True, exist_ok=True)
    for nome in nomes:
        (pasta / nome).write_text(conteudo(nome), encoding="utf-8")
    return pasta


# ── O reconhecedor ────────────────────────────────────────────

# Grafias reais, contadas no corpus: 2.227 arquivos 23_Analysis_* e 30
# 23_AnalysisEnduranceWithSections_*.
@pytest.mark.parametrize("nome", [
    "23_Analysis_Race.CSV",
    "23_Analysis_Race 1.CSV",
    "23_Analysis_Race_Hour 6.CSV",
    "23_Analysis_Free Practice 1.CSV",
    "23_Analysis_Qualifying Practice - LMP2 Pro-Am.CSV",
    "23_Analysis_Hyperpole HYPERCAR.CSV",
    "23_Analysis_Free Practice 1_ELMS.CSV",
    "23_Analysis_Warm-Up.CSV",
    "23_AnalysisEnduranceWithSections_Free Practice 1.CSV",
    "23_AnalysisEnduranceWithSections_Race_Hour 6.CSV",
])
def test_as_grafias_legitimas_sao_aceitas(nome):
    assert is_analysis_file(Path(nome)) is True


@pytest.mark.parametrize("nome", [
    "03_Classification_Race.CSV",
    "03_Classification_Free Practice 1.CSV",
    "03_Results.CSV",
    "03_Results_Race_Official.CSV",
    "26_Weather.CSV",
    "26_Weather_Race.CSV",
    "26_Weather (Not Available).CSV",
])
def test_os_outros_tipos_sao_recusados(nome):
    assert is_analysis_file(Path(nome)) is False


def test_a_variante_com_secoes_nao_exige_o_sublinhado():
    """
    `^23_Analysis_` jogaria fora os 30 arquivos da segunda grafia, que trazem
    o mesmo cabecalho de voltas.
    """
    assert is_analysis_file(Path("23_AnalysisEnduranceWithSections_Race.CSV")) is True


@pytest.mark.parametrize("nome", [
    "23_Analysis_Race.csv",
    "23_analysis_race.CSV",
    "23_ANALYSIS_RACE.CSV",
])
def test_a_caixa_do_nome_e_da_extensao_nao_importa(nome):
    assert is_analysis_file(Path(nome)) is True


def test_outra_extensao_nao_entra():
    assert is_analysis_file(Path("23_Analysis_Race.pdf")) is False
    assert is_analysis_file(Path("23_Analysis_Race.PDF")) is False


def test_o_prefixo_numerico_e_exigido():
    assert is_analysis_file(Path("Analysis_Race.CSV")) is False
    assert is_analysis_file(Path("03_Analysis_Race.CSV")) is False


# ── A escolha dentro de uma pasta ─────────────────────────────

def test_a_analise_e_escolhida_entre_os_quatro_tipos(tmp_path):
    pasta = escrever(tmp_path / "other", "23_Analysis_Race.CSV",
                     "03_Classification_Race.CSV", "03_Results.CSV",
                     "26_Weather_Race.CSV")
    escolhido = get_final_analysis(pasta)
    assert escolhido is not None
    assert escolhido.name == "23_Analysis_Race.CSV"


@pytest.mark.parametrize("nome", [
    "03_Classification_Race.CSV", "03_Results.CSV", "26_Weather_Race.CSV",
])
def test_pasta_sem_analise_valida_nao_tem_fallback(tmp_path, nome):
    """
    O defeito: 82 classificacoes e 2 arquivos de clima chegavam ao parser de
    voltas porque eram o unico CSV da pasta.
    """
    pasta = escrever(tmp_path / "other", nome)
    assert get_final_analysis(pasta) is None


def test_pasta_so_com_classificacao_e_clima_nao_tem_analise(tmp_path):
    pasta = escrever(tmp_path / "other", "03_Classification_Race.CSV",
                     "26_Weather_Race.CSV")
    assert get_final_analysis(pasta) is None


def test_pasta_sem_csv_algum(tmp_path):
    (tmp_path / "analysis").mkdir()
    assert get_final_analysis(tmp_path / "analysis") is None


def test_pasta_inexistente(tmp_path):
    assert get_final_analysis(tmp_path / "nao_existe") is None


def test_a_hora_final_vence(tmp_path):
    base = tmp_path / "analysis"
    escrever(base / "Hour 1", "23_Analysis_Race.CSV")
    escrever(base / "Hour 6", "23_Analysis_Race.CSV")
    escrever(base / "24_Hour 24", "23_Analysis_Race.CSV")
    escolhido = get_final_analysis(base)
    assert escolhido is not None
    assert escolhido.parent.name == "24_Hour 24"


def test_uma_classificacao_numa_hora_maior_nao_vence_a_analise(tmp_path):
    """Sem o filtro, a ordenacao por hora entregaria a classificacao."""
    base = tmp_path / "other"
    escrever(base / "Hour 6", "23_Analysis_Race.CSV")
    escrever(base / "Hour 24", "03_Classification_Race.CSV", "26_Weather_Race.CSV")
    escolhido = get_final_analysis(base)
    assert escolhido is not None
    assert escolhido.name == "23_Analysis_Race.CSV"
    assert escolhido.parent.name == "Hour 6"


def test_extensao_minuscula_tambem_e_encontrada(tmp_path):
    pasta = tmp_path / "analysis"
    pasta.mkdir(parents=True)
    (pasta / "23_Analysis_Race.csv").write_text(ANALYSIS_CSV, encoding="utf-8")
    escolhido = get_final_analysis(pasta)
    assert escolhido is not None
    assert escolhido.name == "23_Analysis_Race.csv"


def test_com_as_duas_grafias_a_escolha_e_a_mesma_em_qualquer_plataforma(tmp_path):
    """
    28 pastas do corpus trazem as duas. Elas guardam as mesmas voltas, entao
    qual vence nao muda o dado — mas ordenar `Path` dobra caixa no Windows e
    nao dobra no Linux, e isso daria arquivos diferentes aos dois. A grafia
    simples vence, por ser a dos outros 2.227 arquivos.
    """
    pasta = escrever(tmp_path / "analysis",
                     "23_Analysis_Free Practice 1.CSV",
                     "23_AnalysisEnduranceWithSections_Free Practice 1.CSV")
    escolhido = get_final_analysis(pasta)
    assert escolhido is not None
    assert escolhido.name == "23_Analysis_Free Practice 1.CSV"


def test_a_variante_sozinha_continua_sendo_escolhida(tmp_path):
    pasta = escrever(tmp_path / "analysis",
                     "23_AnalysisEnduranceWithSections_Race.CSV")
    escolhido = get_final_analysis(pasta)
    assert escolhido is not None
    assert escolhido.name == "23_AnalysisEnduranceWithSections_Race.CSV"


def test_a_escolha_e_estavel(tmp_path):
    pasta = escrever(tmp_path / "other", "23_Analysis_Race.CSV",
                     "23_AnalysisEnduranceWithSections_Race.CSV",
                     "03_Classification_Race.CSV", "26_Weather_Race.CSV")
    primeiro = get_final_analysis(pasta)
    for _ in range(5):
        assert get_final_analysis(pasta) == primeiro


# ── A varredura de raw/ ───────────────────────────────────────

def montar(raw: Path, sessao: str, pasta: str, *nomes: str) -> None:
    escrever(raw / "14_2025" / "04_LE MANS" / sessao / pasta, *nomes)


def test_a_sessao_com_analise_e_devolvida(tmp_path):
    montar(tmp_path, "Free Practice 1", "analysis", "23_Analysis_Free Practice 1.CSV")
    sessoes = find_analysis_sessions(tmp_path)
    assert len(sessoes) == 1
    assert sessoes[0]["season_raw"] == "14_2025"
    assert sessoes[0]["event_raw"] == "04_LE MANS"
    assert sessoes[0]["session_name"] == "Free Practice 1"
    assert sessoes[0]["csv_path"].name == "23_Analysis_Free Practice 1.CSV"


def test_a_sessao_so_com_classificacao_fica_de_fora(tmp_path):
    montar(tmp_path, "Race", "other", "03_Classification_Race.CSV")
    assert find_analysis_sessions(tmp_path) == []


def test_a_sessao_so_com_clima_fica_de_fora(tmp_path):
    montar(tmp_path, "Race", "other", "26_Weather_Race.CSV")
    assert find_analysis_sessions(tmp_path) == []


def test_as_sessoes_boas_sobrevivem_as_ruins(tmp_path):
    montar(tmp_path, "Free Practice 1", "analysis", "23_Analysis_Free Practice 1.CSV")
    montar(tmp_path, "Race", "other", "03_Classification_Race.CSV", "26_Weather_Race.CSV")
    montar(tmp_path, "Qualifying", "analysis", "23_Analysis_Qualifying.CSV")
    nomes = sorted(s["session_name"] for s in find_analysis_sessions(tmp_path))
    assert nomes == ["Free Practice 1", "Qualifying"]


def test_o_recuo_para_other_continua_valendo(tmp_path):
    """
    Sem pasta `analysis`, a busca passa para `other` — e 138 analises do
    corpus estao ali. O filtro nao pode derrubar isso.
    """
    montar(tmp_path, "Race", "other/Hour 6", "23_Analysis_Race.CSV",
           "03_Classification_Race.CSV")
    sessoes = find_analysis_sessions(tmp_path)
    assert len(sessoes) == 1
    assert sessoes[0]["csv_path"].name == "23_Analysis_Race.CSV"
    assert sessoes[0]["csv_path"].parent.name == "Hour 6"


def test_raw_inexistente_nao_quebra(tmp_path):
    assert find_analysis_sessions(tmp_path / "nao_existe") == []
