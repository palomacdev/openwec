"""
The tuple `cars` is written with, and what it does and does not decide.

These tests need no database. They cover the part of the fix that is pure:
which values the loader takes from a classification row, that the two header
dialects of PR #38 produce the same tuple for the same car, and that a row the
source distinguishes in any written column produces a different tuple.

What a shared tuple then means for the rows in `cars` is covered by
test_car_upsert_idempotency.py, which needs a disposable database.
"""
from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "database" / "loader"))

from load_classification import CAR_COLUMNS, car_identity  # noqa: E402

# raw/alms/01_2022/01_Dubai Autodrome/Race 1/other/Hour 1/03_Classification_Race 1.CSV
DIALETO_A = (
    "POSITION;NUMBER;CLASS;TEAM;VEHICLE;LAPS;TOTAL_TIME;STATUS;GAP_FIRST;"
    "GAP_PREVIOUS;FL_TIME;FL_LAPNUM;FL_KPH;GROUP;TYRES\n"
    "1;4;LMP2;Nielsen Racing;Oreca 07-Gibson;31;1:00'01.868;Classified;;;"
    "1'48.714;25;178.5;;M\n"
)

# raw/alms/01_2022/01_Dubai Autodrome/Free Practice 1/classification/
#     03_Classification_Free Practice 1.CSV
DIALETO_B = (
    "POS;NUMBER;CLASS;TEAM;VEHICLE;LAPS;LAP;TIME;GAP_FIRST;GAP_PREVIOUS;"
    "KPH;GROUP;TIRES\n"
    "1;4;LMP2;Nielsen Racing;Oreca 07-Gibson;36;2;1:46.925;-;-;181.5;;M\n"
)


def linha(texto: str) -> dict:
    leitor = csv.DictReader(io.StringIO(texto), delimiter=";")
    r = next(iter(leitor))
    return {k.strip(): (v.strip() if v else "") for k, v in r.items() if k}


A = linha(DIALETO_A)
B = linha(DIALETO_B)


def test_a_tupla_tem_uma_posicao_por_coluna_escrita():
    assert len(car_identity("4", 7, A)) == len(CAR_COLUMNS) == 8


def test_os_valores_vem_das_colunas_certas():
    t = dict(zip(CAR_COLUMNS, car_identity("4", 7, A)))
    assert t["number"] == "4"
    assert t["team_id"] == 7
    assert t["vehicle"] == "Oreca 07-Gibson"
    assert t["car_class"] == "LMP2"
    assert t["car_group"] is None        # GROUP vazio nesta linha
    assert t["tires"] == "M"             # veio de TYRES
    assert t["imsa_car_id"] is None
    assert t["imsa_team_id"] is None


def test_os_dois_dialetos_descrevem_o_mesmo_carro():
    """
    A mesma inscrição num arquivo de corrida e num de treino. As colunas que
    mudaram de nome na PR #38 não são as que identificam o carro, então a
    tupla tem de ser a mesma — senão uma sessão de treino criaria um carro
    novo para um carro que já existe.
    """
    assert car_identity("4", 7, A) == car_identity("4", 7, B)


def test_tires_e_tyres_sao_a_mesma_coluna():
    assert "TYRES" in A and "TIRES" not in A
    assert "TIRES" in B and "TYRES" not in B
    assert car_identity("4", 7, A)[5] == car_identity("4", 7, B)[5] == "M"


def test_vazio_vira_none_e_nao_string_vazia():
    """NULL e '' não podem virar duas identidades para o mesmo carro."""
    sem_classe = dict(A, CLASS="", VEHICLE="   ")
    t = dict(zip(CAR_COLUMNS, car_identity("4", 7, sem_classe)))
    assert t["car_class"] is None
    assert t["vehicle"] is None


def test_numero_literal_e_preservado():
    """`#03` não é `#3`: ADR 0001, P1."""
    assert car_identity("03", 7, A)[0] == "03"
    assert car_identity("03", 7, A) != car_identity("3", 7, A)


def test_equipe_diferente_e_identidade_diferente():
    assert car_identity("4", 7, A) != car_identity("4", 8, A)


def test_classe_diferente_e_identidade_diferente():
    assert car_identity("4", 7, A) != car_identity("4", 7, dict(A, CLASS="LMP3"))


def test_veiculo_diferente_e_identidade_diferente():
    assert car_identity("4", 7, A) != car_identity("4", 7, dict(A, VEHICLE="Ligier JS P320"))


def test_qualquer_coluna_escrita_separa():
    """Nenhuma das oito é ignorada ao comparar."""
    base = car_identity("4", 7, A)
    variantes = [
        dict(A, NUMBER="x"), dict(A, VEHICLE="x"), dict(A, CLASS="x"),
        dict(A, GROUP="x"), dict(A, TYRES="x"),
        dict(A, IMSA_CarId="99"), dict(A, IMSA_TeamId="99"),
    ]
    numeros = ["4"] * len(variantes)
    numeros[0] = "x"
    for n, v in zip(numeros, variantes):
        assert car_identity(n, 7, v) != base
    assert car_identity("4", 8, A) != base      # team_id


def test_imsa_ids_ilegiveis_nao_quebram_a_leitura():
    t = car_identity("4", 7, dict(A, IMSA_CarId="n/d", IMSA_TeamId="n/d"))
    assert t[6] is None and t[7] is None


def test_imsa_zero_e_tratado_como_ausente():
    """A fonte escreve 0 onde não tem id; 0 não é um id."""
    t = car_identity("4", 7, dict(A, IMSA_CarId="0", IMSA_TeamId="0"))
    assert t[6] is None and t[7] is None
