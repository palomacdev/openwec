"""
Header dialects of the classification exports.

The source publishes classification in more than one header dialect. The rows
below are copied from real files of the raw tree, with the columns that matter
kept and the driver columns dropped; each fixture names the file it came from.

What these tests guard is narrow: that a field present in the source reaches
the row written to `results`, and that a field absent from the source stays
absent instead of being filled in from a column that means something else.
"""
from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "database" / "loader"))

from load_classification import (  # noqa: E402
    as_float,
    as_int,
    field,
    insert_result,
    parse_lap_time,
    parse_race_time,
)

# ── Fixtures, copied from raw/ ───────────────────────────────

# raw/alms/01_2022/01_Dubai Autodrome/Race 1/other/Hour 1/03_Classification_Race 1.CSV
DIALETO_A = (
    "POSITION;NUMBER;CLASS;TEAM;VEHICLE;LAPS;TOTAL_TIME;STATUS;GAP_FIRST;"
    "GAP_PREVIOUS;FL_TIME;FL_LAPNUM;FL_KPH;GROUP;TYRES\n"
    "1;4;LMP2;Nielsen Racing;Oreca 07-Gibson;31;1:00'01.868;Classified;;;"
    "1'48.714;25;178.5;;M\n"
    "2;39;LMP2;Graff Racing;Oreca 07-Gibson;31;1:00'14.892;Classified;13.024;"
    "13.024;1'51.833;5;173.5;AM;M\n"
)

# raw/alms/01_2022/01_Dubai Autodrome/Free Practice 1/classification/
#     03_Classification_Free Practice 1.CSV
DIALETO_B = (
    "POS;NUMBER;CLASS;TEAM;VEHICLE;LAPS;LAP;TIME;GAP_FIRST;GAP_PREVIOUS;"
    "KPH;GROUP;TIRES\n"
    "1;4;LMP2;Nielsen Racing;Oreca 07-Gibson;36;2;1:46.925;-;-;181.5;;M\n"
    "35;3;LMP3;CD Sport;Ligier JS P320-Nissan;0;0;;;;;;M\n"
)


def linhas(texto: str) -> list[dict]:
    """The same shape read_csv produces: semicolon-delimited, values stripped."""
    leitor = csv.DictReader(io.StringIO(texto), delimiter=";")
    return [{k.strip(): (v.strip() if v else "") for k, v in r.items() if k}
            for r in leitor]


A = linhas(DIALETO_A)
B = linhas(DIALETO_B)


# ── field(): which spelling wins ─────────────────────────────

def test_dialeto_a_le_os_nomes_canonicos():
    assert field(A[0], "POSITION") == "1"
    assert field(A[0], "LAPS") == "31"
    assert field(A[0], "TOTAL_TIME") == "1:00'01.868"
    assert field(A[0], "FL_TIME") == "1'48.714"
    assert field(A[0], "FL_LAPNUM") == "25"
    assert field(A[0], "FL_KPH") == "178.5"
    assert field(A[0], "STATUS") == "Classified"


def test_dialeto_b_le_os_nomes_alternativos():
    assert field(B[0], "POSITION") == "1"        # vem de POS
    assert field(B[0], "FL_TIME") == "1:46.925"  # vem de TIME
    assert field(B[0], "FL_LAPNUM") == "2"       # vem de LAP
    assert field(B[0], "FL_KPH") == "181.5"      # vem de KPH


def test_dialeto_b_nao_inventa_o_que_a_fonte_nao_traz():
    """TOTAL_TIME e STATUS não existem no dialeto B. Ficam vazios."""
    assert "TOTAL_TIME" not in B[0]
    assert "STATUS" not in B[0]
    assert field(B[0], "TOTAL_TIME") == ""
    assert field(B[0], "STATUS") == ""


def test_coluna_presente_e_vazia_nao_cai_para_a_alternativa():
    """
    O caso que separa 'ausente na fonte' de 'perdido pelo parser'.

    Um arquivo do dialeto A com FL_TIME vazio não pode passar a ler TIME, que
    nesse dialeto é outra coisa. A coluna existe: o valor é que não veio.
    """
    linha = dict(A[0])
    linha["FL_TIME"] = ""
    linha["TIME"] = "9:99.999"
    assert field(linha, "FL_TIME") == ""


def test_nome_sem_alias_se_comporta_como_antes():
    assert field(A[0], "CLASS") == "LMP2"
    assert field(B[0], "CLASS") == "LMP2"
    assert field(A[0], "NAO_EXISTE") == ""


# ── as_int / as_float: zero é um valor ───────────────────────

def test_zero_e_preservado_e_nao_vira_ausencia():
    """
    Um carro classificado em 35º com LAPS=0 completou zero voltas. Isso não é
    o mesmo que não se saber quantas completou.
    """
    assert field(B[1], "LAPS") == "0"
    assert as_int(field(B[1], "LAPS")) == 0
    assert as_int(field(B[1], "FL_LAPNUM")) == 0


def test_ausente_e_ilegivel_continuam_sendo_none():
    assert as_int("") is None
    assert as_int("   ") is None
    assert as_int("n/d") is None
    assert as_float("") is None
    assert as_float("-") is None
    assert as_int(None) is None
    assert as_float(None) is None


def test_valores_normais_sao_lidos():
    assert as_int("31") == 31
    assert as_int(" 7 ") == 7
    assert as_float("178.5") == pytest.approx(178.5)
    assert as_int("-1") == -1


# ── Formatos de tempo dos dois dialetos ──────────────────────

def test_o_tempo_do_dialeto_b_e_volta_e_nao_duracao():
    """
    `1:46.925` no dialeto B é a melhor volta, não um tempo decorrido. Por isso
    vai para fl_time_s, e parse_lap_time o lê como 1min46,925s.
    """
    assert parse_lap_time("1:46.925") == pytest.approx(106.925)
    assert parse_lap_time("1'48.714") == pytest.approx(108.714)


def test_o_tempo_total_do_dialeto_a_continua_sendo_duracao():
    assert parse_race_time("1:00'01.868") == pytest.approx(3601.868)


# ── O que chega a results ────────────────────────────────────

class CursorFalso:
    """Guarda os parâmetros do INSERT; não fala com banco nenhum."""

    def __init__(self):
        self.parametros = None

    def execute(self, _sql, parametros):
        self.parametros = parametros

    def fetchone(self):
        return (1,)


COLUNAS = [
    "session_id", "car_id", "position", "status", "laps_completed",
    "total_time_raw", "total_time_s",
    "gap_to_first_raw", "gap_to_first_s",
    "gap_to_prev_raw", "gap_to_prev_s",
    "fl_lap_number", "fl_time_raw", "fl_time_s", "fl_kph",
]


def gravado(row: dict) -> dict:
    cur = CursorFalso()
    insert_result(cur, session_id=1, car_id=2, row=row)
    return dict(zip(COLUNAS, cur.parametros))


def test_resultado_do_dialeto_a():
    r = gravado(A[0])
    assert r["position"] == 1
    assert r["laps_completed"] == 31
    assert r["status"] == "Classified"
    assert r["total_time_s"] == pytest.approx(3601.868)
    assert r["fl_time_s"] == pytest.approx(108.714)
    assert r["fl_lap_number"] == 25
    assert r["fl_kph"] == pytest.approx(178.5)


def test_resultado_do_dialeto_b_recupera_o_que_se_perdia():
    r = gravado(B[0])
    assert r["position"] == 1                            # antes: None
    assert r["fl_time_s"] == pytest.approx(106.925)      # antes: None
    assert r["fl_lap_number"] == 2                       # antes: None
    assert r["fl_kph"] == pytest.approx(181.5)           # antes: None
    assert r["laps_completed"] == 36


def test_resultado_do_dialeto_b_deixa_vazio_o_que_a_fonte_nao_tem():
    r = gravado(B[0])
    assert r["total_time_raw"] is None
    assert r["total_time_s"] is None


def test_status_ausente_nao_vira_classified():
    """
    `results.status` é NOT NULL com default 'Classified'. O dialeto B não traz
    status, e escrever 'Classified' afirmaria algo que a fonte não disse. O
    loader escreve 'Other', que é o rótulo de desconhecido do enum.
    """
    assert gravado(B[0])["status"] == "Other"


def test_linha_sem_voltas_grava_zero_e_nao_nulo():
    r = gravado(B[1])
    assert r["position"] == 35
    assert r["laps_completed"] == 0
    assert r["fl_lap_number"] == 0
    assert r["fl_time_s"] is None      # TIME vazio: ausente de verdade
    assert r["fl_kph"] is None


def test_dialeto_a_nao_regride():
    """A correção não pode mudar o que já funcionava."""
    r = gravado(A[1])
    assert r["position"] == 2
    assert r["laps_completed"] == 31
    assert r["gap_to_first_s"] == pytest.approx(13.024)
    assert r["gap_to_prev_s"] == pytest.approx(13.024)
    assert r["fl_lap_number"] == 5
    assert r["fl_kph"] == pytest.approx(173.5)


# ── Contra o acervo real, quando ele estiver presente ────────

RAW = Path(__file__).resolve().parents[2] / "raw"


@pytest.mark.skipif(not RAW.is_dir(), reason="raw/ nao esta presente")
def test_os_dois_dialetos_existem_no_acervo():
    """
    Lê alguns arquivos reais e confirma que as duas grafias ocorrem. Não
    percorre a árvore inteira: a contagem completa é trabalho de relatório,
    não de teste.
    """
    from load_classification import read_csv

    vistos = set()
    lidos = 0
    for p in sorted(RAW.rglob("*.csv")):
        nome = p.name.lower()
        if "classification" not in nome and "result" not in nome:
            continue
        linhas_arquivo = read_csv(p)
        if not linhas_arquivo:
            continue
        lidos += 1
        chaves = set(linhas_arquivo[0].keys())
        if "POSITION" in chaves:
            vistos.add("A")
        elif "POS" in chaves:
            vistos.add("B")
        if vistos >= {"A", "B"} or lidos >= 400:
            break
    assert "A" in vistos, "nenhum arquivo com POSITION entre os lidos"
    assert "B" in vistos, "nenhum arquivo com POS entre os lidos"
