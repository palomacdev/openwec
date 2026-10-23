"""
The classification parser against files whose header is wider than their rows.

Five of the 182 sources verified in PR #49 publish a 93-column header over
73-field rows. PR #49 read them with an audit routine that dropped every row
shorter than the header, measured an empty intersection and reported "no
overlap"; PR #50 corrected the measurement and registered the shape as a
precondition, because the production parser had never been run against it.

These tests run the production parser — `read_csv`, `field`, `car_identity`
and `insert_result` — over two of those five files, kept here with their real
header and a handful of their real rows. What they pin is the thing the
precondition asked about: whether a row shorter than the header arrives with
its values under the right column names.

Three defects surfaced while measuring, none of them caused by the width:
TOTAL_TIME in `H:MM:SS.mmm` was truncated to minutes, a signed gap over a
minute parsed to None, and "Not classified" normalised to `Classified`. The
regression section below covers the fixes alongside the formats that already
worked.

No database: a fake cursor captures what `insert_result` would write.
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
    car_identity,
    extract_drivers_imsa,
    field,
    insert_result,
    normalize_status,
    parse_gap,
    parse_lap_time,
    parse_race_time,
    read_csv,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "classification"

# Michelin Le Mans Cup 2018, from .../201804141605_Race/Hour 2/ and
# .../201805121600_Race/Hour 2/ — 05_Classification_Race.CSV, header verbatim,
# a subset of the published rows.
PAUL_RICARD = FIXTURES / "lemanscup_2018_paul_ricard_93x73.csv"
MONZA = FIXTURES / "lemanscup_2018_monza_93x73.csv"

LARGURA_CABECALHO = 93
LARGURA_LINHA = 73


def campos_crus(path: Path) -> tuple[list[str], list[list[str]]]:
    """Header and rows as the file splits them, with no dict in between."""
    texto = path.read_bytes().decode("utf-8")
    linhas = [linha for linha in csv.reader(io.StringIO(texto), delimiter=";")
              if any(c.strip() for c in linha)]
    return linhas[0], linhas[1:]


def por_numero(path: Path) -> dict[str, dict]:
    return {r["NUMBER"]: r for r in read_csv(path)}


PR = por_numero(PAUL_RICARD)
MZ = por_numero(MONZA)


# ── The shape itself ─────────────────────────────────────────

@pytest.mark.parametrize("path", [PAUL_RICARD, MONZA])
def test_o_arquivo_tem_mesmo_a_forma_que_motivou_a_pre_condicao(path):
    cabecalho, linhas = campos_crus(path)
    assert len(cabecalho) == LARGURA_CABECALHO
    assert {len(linha) for linha in linhas} == {LARGURA_LINHA}


@pytest.mark.parametrize("path", [PAUL_RICARD, MONZA])
def test_nenhuma_linha_e_descartada_por_ser_mais_curta(path):
    _, linhas = campos_crus(path)
    assert len(read_csv(path)) == len(linhas)


@pytest.mark.parametrize("path", [PAUL_RICARD, MONZA])
def test_cada_valor_chega_sob_o_nome_da_sua_propria_coluna(path):
    """
    The question the precondition actually asked.

    A row 20 fields short could have been read with its tail shifted, which
    no exception would announce and no row count would reveal. This compares
    position by position: field j of the row under name j of the header.
    """
    cabecalho, linhas = campos_crus(path)
    lidas = read_csv(path)
    assert len(lidas) == len(linhas)

    for crua, lida in zip(linhas, lidas):
        for j, valor in enumerate(crua):
            nome = cabecalho[j].strip()
            if not nome:          # the trailing empty header name
                continue
            assert lida[nome] == valor.strip(), f"coluna {j} ({nome})"


@pytest.mark.parametrize("path", [PAUL_RICARD, MONZA])
def test_as_colunas_excedentes_voltam_vazias_e_sao_todas_de_piloto(path):
    """
    What the 20 columns past the row width become, and what they are.

    `csv.DictReader` fills them with `restval`, which `read_csv` turns into
    "". That is only harmless because of what they are: every one of them
    belongs to driver 5 or driver 6, slots these files leave empty in the
    fields they do publish.
    """
    cabecalho, _ = campos_crus(path)
    excedentes = [n.strip() for n in cabecalho[LARGURA_LINHA:] if n.strip()]

    assert len(excedentes) == 19   # the 20th is the trailing empty name
    assert all(n.startswith(("DRIVER5_", "DRIVER6_")) for n in excedentes)

    for lida in read_csv(path):
        for nome in excedentes:
            assert lida[nome] == ""


# ── The fields the loader reads, value by value ──────────────

ESPERADO = {
    # NUMBER: POSITION, STATUS, LAPS, TOTAL_TIME, GAP_FIRST, GAP_PREVIOUS,
    #         FL_LAPNUM, FL_TIME, FL_KPH, TEAM, CLASS, VEHICLE, TIRES
    "3":  (1,    "Classified",     62, "2:01:25.448", "-",         "-",
           30, "1:52.439", 185.4, "DKR Engineering", "LMP3",
           "Norma M30 - Nissan", "M"),
    "79": (2,    "Classified",     62, "2:01:27.615", "+2.167",    "+2.167",
           49, "1:52.722", 184.9, "Ecurie Ecosse / Nielsen", "LMP3",
           "Ligier JS P3 - Nissan", "M"),
    "98": (10,   "Classified",     62, "2:02:42.298", "+1:16.850", "+19.941",
           60, "1:52.657", 185.1, "Motorsport98", "LMP3",
           "Ligier JS P3 - Nissan", "M"),
    "32": (13,   "Classified",     61, "2:02:03.852", "1 Lap",     "1 Lap",
           61, "1:53.667", 183.4, "United Autosports", "LMP3",
           "Ligier JS P3 - Nissan", "M"),
    "8":  (15,   "Classified",     61, "2:02:33.563", "1 Lap",     "+20.850",
           59, "1:55.634", 180.3, "Kessel Racing", "GT3",
           "Ferrari F488", "M"),
    "17": (None, "Not classified", 56, "1:52:59.306", "",          "",
           40, "1:53.230", 184.1, "RGP Motorsport", "LMP3",
           "Ligier JS P3 - Nissan", "M"),
    "25": (None, "Not classified", 24, "47:14.941",   "",          "",
           12, "1:56.249", 179.3, "Lanan Racing", "LMP3",
           "Norma M30 - Nissan", "M"),
}


@pytest.mark.parametrize("numero", sorted(ESPERADO))
def test_os_campos_do_paul_ricard_chegam_com_o_valor_publicado(numero):
    (posicao, status, voltas, total, gap1, gapp,
     fl_lap, fl_time, fl_kph, equipe, classe, veiculo, pneus) = ESPERADO[numero]
    row = PR[numero]

    assert as_int(field(row, "POSITION")) == posicao
    assert field(row, "STATUS") == status
    assert as_int(field(row, "LAPS")) == voltas
    assert field(row, "TOTAL_TIME") == total
    assert field(row, "GAP_FIRST") == gap1
    assert field(row, "GAP_PREVIOUS") == gapp
    assert as_int(field(row, "FL_LAPNUM")) == fl_lap
    assert field(row, "FL_TIME") == fl_time
    assert as_float(field(row, "FL_KPH")) == pytest.approx(fl_kph)
    assert field(row, "TEAM") == equipe
    assert field(row, "CLASS") == classe
    assert field(row, "VEHICLE") == veiculo
    assert row["TIRES"] == pneus
    assert field(row, "GROUP") == ""     # published empty, not missing


def test_o_lider_de_monza_chega_com_o_valor_publicado():
    row = MZ["3"]
    assert as_int(field(row, "POSITION")) == 1
    assert as_int(field(row, "LAPS")) == 62
    assert field(row, "TOTAL_TIME") == "2:00:36.438"
    assert field(row, "FL_TIME") == "1:46.782"
    assert as_int(field(row, "FL_LAPNUM")) == 47
    assert as_float(field(row, "FL_KPH")) == pytest.approx(195.3)
    assert field(row, "TEAM") == "DKR Engineering"
    assert field(row, "VEHICLE") == "Norma M30 - Nissan"


def test_monza_traz_os_mesmos_formatos_de_tempo_e_de_gap():
    assert field(MZ["24"], "GAP_FIRST") == "+1:10.985"
    assert field(MZ["9"], "GAP_FIRST") == "1 Lap"
    assert field(MZ["65"], "STATUS") == "Not classified"
    assert field(MZ["65"], "TOTAL_TIME") == "15:23.489"


def test_o_acento_do_nome_atravessa_intacto():
    """UTF-8, decoded as such: the file is not cp1252 and must not be read as one."""
    assert PR["3"]["DRIVER1_FIRSTNAME"] == "Léonard"
    assert MZ["3"]["DRIVER1_FIRSTNAME"] == "Léonard"


# ── car_identity ─────────────────────────────────────────────

def test_a_identidade_do_carro_sai_completa():
    assert car_identity("3", 7, PR["3"]) == (
        "3", 7, "Norma M30 - Nissan", "LMP3", None, "M", None, None)


def test_a_classe_separa_os_carros_dentro_do_mesmo_arquivo():
    assert car_identity("8", 7, PR["8"])[3] == "GT3"
    assert car_identity("32", 7, PR["32"])[3] == "LMP3"


# ── insert_result: what would reach `results` ────────────────

class CursorFalso:
    """Captures the parameters without a database behind it."""

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


def test_o_lider_do_paul_ricard_vai_inteiro_para_results():
    r = gravado(PR["3"])
    assert r["position"] == 1
    assert r["status"] == "Classified"
    assert r["laps_completed"] == 62
    assert r["total_time_raw"] == "2:01:25.448"
    assert r["total_time_s"] == pytest.approx(7285.448)
    assert r["gap_to_first_raw"] == "-"       # the text the source gave
    assert r["gap_to_first_s"] is None        # "-" is the leader, not a gap
    assert r["fl_lap_number"] == 30
    assert r["fl_time_raw"] == "1:52.439"
    assert r["fl_time_s"] == pytest.approx(112.439)
    assert r["fl_kph"] == pytest.approx(185.4)


def test_duas_horas_nao_viram_dois_minutos():
    """
    The defect this file exposed, and the one with the widest reach.

    `2:01:25.448` matched the M:SS branch, which stops at the second colon:
    the race came out as 121.0 s. `MEASURED` over the 3,539 classification
    files of the local tree: 11,123 rows carry TOTAL_TIME in this form.
    """
    assert gravado(PR["3"])["total_time_s"] == pytest.approx(7285.448)
    assert gravado(MZ["3"])["total_time_s"] == pytest.approx(7236.438)
    assert gravado(PR["17"])["total_time_s"] == pytest.approx(6779.306)


def test_o_carro_nao_classificado_nao_e_promovido_a_classificado():
    """
    "Not classified" contains "classified"; the order of the tests decides.

    A car the source left out of the order would otherwise be stored as
    `Classified` with position NULL. `MEASURED`: 855 rows of the local tree
    spell it this way.
    """
    r = gravado(PR["17"])
    assert r["status"] == "Not Classified"
    assert r["position"] is None
    assert r["laps_completed"] == 56
    assert gravado(MZ["65"])["status"] == "Not Classified"


def test_o_gap_de_mais_de_um_minuto_deixa_de_se_perder():
    r = gravado(PR["98"])
    assert r["gap_to_first_raw"] == "+1:16.850"
    assert r["gap_to_first_s"] == pytest.approx(76.850)
    assert r["gap_to_prev_s"] == pytest.approx(19.941)
    assert gravado(MZ["24"])["gap_to_first_s"] == pytest.approx(70.985)


def test_o_carro_dobrado_guarda_o_texto_e_nao_inventa_segundos():
    r = gravado(PR["32"])
    assert r["gap_to_first_raw"] == "1 Lap"
    assert r["gap_to_first_s"] is None
    assert r["gap_to_prev_raw"] == "1 Lap"
    assert r["gap_to_prev_s"] is None


def test_o_campo_vazio_fica_vazio():
    r = gravado(PR["25"])
    assert r["gap_to_first_raw"] is None
    assert r["gap_to_first_s"] is None
    assert r["position"] is None


# ── Drivers: present in the row, under IMSA-shaped names ─────

def test_os_pilotos_estao_na_linha_sob_nomes_do_formato_imsa():
    """
    Where the names are, and why the WEC extractor cannot see them.

    These Le Mans Cup files spell the driver columns `DRIVER1_FIRSTNAME` /
    `DRIVER1_SECONDNAME`, the shape `extract_drivers_imsa` reads, and carry
    no `DRIVER_1` column at all. The loader picks the extractor by series,
    so for LEMANSCUP it calls the WEC one. This PR measures that and changes
    nothing: driver linking is not among the fields under test here, and
    which extractor runs is a loader decision, not a parser one.
    """
    row = PR["3"]
    assert "DRIVER_1" not in row
    assert [(d["slot"], d["first_name"], d["last_name"], d["country"])
            for d in extract_drivers_imsa(row)] == [
        (1, "Léonard", "HOOGENBOOM", "NLD"),
        (2, "Jens", "PETERSEN", "DEU"),
    ]


# ── Regression: the formats that already worked ──────────────

@pytest.mark.parametrize("texto,segundos", [
    ("24:01'55.856", 86515.856),   # dialect A, hours with an apostrophe
    ("4:00'20.026", 14420.026),
    ("1:00'01.868", 3601.868),
    ("45'02.112", 2702.112),       # minutes with an apostrophe
    ("2:01:25.448", 7285.448),     # hours with colons — the new branch
    ("24:01:55.856", 86515.856),
    ("47:14.941", 2834.941),       # minutes with a colon, unchanged
    ("1:35.605", 95.605),
    ("95.605", 95.605),
    ("-", None),
    ("", None),
])
def test_parse_race_time(texto, segundos):
    resultado = parse_race_time(texto)
    if segundos is None:
        assert resultado is None
    else:
        assert resultado == pytest.approx(segundos)


@pytest.mark.parametrize("texto,segundos", [
    ("3'29.208", 209.208),
    ("1:35.605", 95.605),
    ("-", None),
])
def test_parse_lap_time_nao_mudou(texto, segundos):
    resultado = parse_lap_time(texto)
    if segundos is None:
        assert resultado is None
    else:
        assert resultado == pytest.approx(segundos)


@pytest.mark.parametrize("texto,segundos", [
    ("12.345", 12.345),
    ("+2.167", 2.167),
    ("+1:16.850", 76.850),      # was None before the sign was dropped
    ("+10:16.850", 616.850),
    ("2'10.500", 130.500),
    ("1 Lap", None),
    ("3 Laps", None),
    ("-", None),
    ("", None),
    ("+", None),
])
def test_parse_gap(texto, segundos):
    resultado = parse_gap(texto)
    if segundos is None:
        assert resultado is None
    else:
        assert resultado == pytest.approx(segundos)


@pytest.mark.parametrize("texto,estado", [
    # Every spelling the 3,539 local classification files contain.
    ("Classified", "Classified"),
    ("Not classified", "Not Classified"),
    ("Not started", "DNS"),
    ("Retired", "Retired"),
    ("Disqualified", "DSQ"),
    ("Excluded", "Other"),
    ("", "Other"),
    # And the canonical ones, which must survive unchanged.
    ("Not Classified", "Not Classified"),
    ("DNF", "DNF"),
    ("DNS", "DNS"),
    ("DSQ", "DSQ"),
    ("Other", "Other"),
])
def test_normalize_status(texto, estado):
    assert normalize_status(texto) == estado
