"""
The eligibility scan: it reports what the recoverer would answer, nothing more.

The verdict must not drift from the rules that actually guard a write. These
tests pin that `verdict_for` takes its answer from `plan_recovery` and
`assert_complete_match` — including the refusals — and that the parts around
it (finding the published file, counting lap divergences, grouping reasons)
measure what they claim to.

No database and no network: a fake cursor answers the two queries the plan
needs, and the catalogue is a dict.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from database.recovery.eligibility import (  # noqa: E402
    BLOCKED,
    ELIGIBLE,
    SOURCE_UNAVAILABLE,
    Eligibility,
    catalog_files,
    choose_source,
    laps_divergences,
    reason_group,
    verdict_for,
)

from tests.recovery.test_recovery_guard import CursorFalso  # noqa: E402


def fonte(*numeros: str) -> list[dict]:
    return [{"NUMBER": n} for n in numeros]


def voltas(**por_numero: list[int]):
    """A cursor whose only answer is the lap cars of the session."""
    return CursorFalso([[(n, ids) for n, ids in por_numero.items()]])


# ── The verdict comes from the recoverer's own rules ─────────

def test_sem_fonte_nao_se_avalia_pareamento():
    veredito, motivo, plano = verdict_for(CursorFalso(), 1, None)
    assert veredito == SOURCE_UNAVAILABLE
    assert plano is None
    assert "sem fonte" in motivo


def test_fonte_vazia_conta_como_sem_fonte():
    assert verdict_for(CursorFalso(), 1, [])[0] == SOURCE_UNAVAILABLE


def test_os_dois_lados_iguais_sao_elegiveis():
    veredito, motivo, plano = verdict_for(
        voltas(**{"3": [10], "79": [11]}), 1, fonte("3", "79"))
    assert veredito == ELIGIBLE
    assert motivo is None
    assert plano.matched == {"3": 10, "79": 11}


def test_o_numero_em_dois_car_id_bloqueia():
    veredito, motivo, plano = verdict_for(
        voltas(**{"3": [10, 11]}), 1, fonte("3"))
    assert veredito == BLOCKED
    assert "mais de um car_id" in motivo
    assert plano is None            # plan_recovery nao chegou a montar um


def test_o_numero_repetido_na_fonte_bloqueia():
    veredito, motivo, _ = verdict_for(voltas(**{"3": [10]}), 1, fonte("3", "3"))
    assert veredito == BLOCKED
    assert "mais de uma vez" in motivo


def test_o_pareamento_incompleto_bloqueia_e_devolve_o_plano():
    """
    The plan survives the refusal, because the numbers are the report.

    A session blocked for an incomplete match is the one a human has to look
    at, and what they need is which numbers did not line up.
    """
    veredito, motivo, plano = verdict_for(
        voltas(**{"3": [10]}), 1, fonte("3", "88"))
    assert veredito == BLOCKED
    assert "pareamento incompleto" in motivo
    assert plano is not None
    assert plano.source_without_laps == ["88"]


def test_contagens_iguais_nao_bastam():
    """
    The case the scope warns about.

    Two sides of 2 cars each, and not one number in common: counting would
    call this a match, and the rules do not.
    """
    veredito, _, _ = verdict_for(voltas(**{"3": [10], "79": [11]}), 1,
                                 fonte("12", "44"))
    assert veredito == BLOCKED


# ── Grouping the refusals ────────────────────────────────────

def linha(**campos) -> Eligibility:
    base = dict(session_id=1, series="ELMS", season="08_2013",
                event="02_IMOLA", session_name="Race")
    base.update(campos)
    return Eligibility(**base)


@pytest.mark.parametrize("campos,grupo", [
    (dict(verdict=ELIGIBLE, cars_with_laps=28), "—"),
    (dict(verdict=BLOCKED, cars_with_laps=0,
          reason="pareamento incompleto — publicados sem voltas"),
     "sem voltas no banco"),
    (dict(verdict=BLOCKED, cars_with_laps=28,
          reason="o numero aparece em mais de um car_id nas voltas: 46"),
     "numero em mais de um car_id"),
    (dict(verdict=BLOCKED, cars_with_laps=28,
          reason="a fonte publica o mesmo numero mais de uma vez: 3"),
     "numero repetido na fonte"),
    (dict(verdict=BLOCKED, cars_with_laps=28,
          reason="pareamento incompleto — publicados sem voltas no banco: 47"),
     "pareamento incompleto"),
    (dict(verdict=SOURCE_UNAVAILABLE, cars_with_laps=28,
          reason="sem fonte utilizavel"), "sem fonte utilizavel"),
])
def test_reason_group(campos, grupo):
    assert reason_group(linha(**campos)) == grupo


def test_sem_voltas_vem_antes_do_pareamento():
    """
    Two different problems answer with the same message, and are counted apart.

    A session with no laps has a classification and nothing to attach it to;
    a session whose numbers disagree has two sides that do not line up. Both
    come back from `assert_complete_match` as an incomplete match.
    """
    sem_voltas = linha(verdict=BLOCKED, cars_with_laps=0,
                       reason="pareamento incompleto — …")
    desalinhada = linha(verdict=BLOCKED, cars_with_laps=28,
                        reason="pareamento incompleto — …")
    assert reason_group(sem_voltas) != reason_group(desalinhada)


# ── Lap counts: the measurement that is not a gate ───────────

def test_a_fonte_que_concorda_com_o_log_nao_diverge():
    rows = [{"NUMBER": "3", "LAPS": "62"}, {"NUMBER": "79", "LAPS": "61"}]
    assert laps_divergences(rows, {"3": 62, "79": 61}) == 0


def test_o_snapshot_de_hora_intermediaria_aparece_como_divergencia():
    """Monza 2018 in miniature: the `Hour 1` file is behind the lap log."""
    rows = [{"NUMBER": "3", "LAPS": "59"}, {"NUMBER": "2", "LAPS": "58"}]
    assert laps_divergences(rows, {"3": 62, "2": 62}) == 2


def test_o_carro_sem_voltas_nao_conta_como_divergencia():
    rows = [{"NUMBER": "3", "LAPS": "62"}, {"NUMBER": "88", "LAPS": "10"}]
    assert laps_divergences(rows, {"3": 62}) == 0


def test_laps_ilegivel_nao_conta_como_divergencia():
    rows = [{"NUMBER": "3", "LAPS": ""}]
    assert laps_divergences(rows, {"3": 62}) == 0


# ── Finding the published file ───────────────────────────────

CATALOGO = {
    "08_2013": {"events": {"03_RED BULL RING": {"championships": {
        "33_European Le Mans Series": {"sessions": {
            "201307201630_Race": {"files": [
                {"filename": "1 Hour/05_Classification_Race_1 Hour.CSV",
                 "url": "https://exemplo/1h.csv"},
                {"filename": "2 Hours/05_Classification_Race_2 Hours.CSV",
                 "url": "https://exemplo/2h.csv"},
                {"filename": "3 Hours/05_Classification_Race_3 Hours.CSV",
                 "url": "https://exemplo/3h.csv"},
                {"filename": "3 Hours/23_Analysis_Race_ELMS.CSV",
                 "url": "https://exemplo/analise.csv"},
            ]},
        }},
    }}}},
}


def test_o_catalogo_devolve_os_arquivos_da_sessao():
    arquivos = catalog_files(CATALOGO, "08_2013", "03_RED BULL RING",
                             "201307201630_Race")
    assert len(arquivos) == 4


@pytest.mark.parametrize("season,event,session", [
    ("09_2014", "03_RED BULL RING", "201307201630_Race"),
    ("08_2013", "04_OUTRO", "201307201630_Race"),
    ("08_2013", "03_RED BULL RING", "201307201630_Qualifying"),
])
def test_o_catalogo_sem_a_sessao_devolve_vazio(season, event, session):
    assert catalog_files(CATALOGO, season, event, session) == []


def test_a_escolha_e_a_hora_final_e_vem_com_a_url(tmp_path):
    """
    The selection is the loader's, not a second rule written here.

    `3 Hours` is the end of this race and `1 Hour` is an hour mark along the
    way; the lap log of a session that ran three hours agrees with the first
    and not the second.
    """
    arquivos = catalog_files(CATALOGO, "08_2013", "03_RED BULL RING",
                             "201307201630_Race")
    nome, url = choose_source(arquivos, tmp_path)
    assert nome == "3 Hours/05_Classification_Race_3 Hours.CSV"
    assert url == "https://exemplo/3h.csv"


def test_a_analise_nao_e_candidata(tmp_path):
    arquivos = [{"filename": "3 Hours/23_Analysis_Race_ELMS.CSV",
                 "url": "https://exemplo/analise.csv"}]
    assert choose_source(arquivos, tmp_path) is None


def test_sem_arquivo_nenhum_nao_ha_escolha(tmp_path):
    assert choose_source([], tmp_path) is None


def test_o_arquivo_sem_url_e_ignorado(tmp_path):
    arquivos = [{"filename": "3 Hours/05_Classification_Race_3 Hours.CSV",
                 "url": ""}]
    assert choose_source(arquivos, tmp_path) is None
