"""
Which of three duplicate rows may be kept, and everything that stops the choice.

The module decides nothing about the database, so what these tests pin is the
reasoning: that a contradiction between the three ingestions is not the same
as one of them being silent; that a metadata value living on a row about to
be discarded is reported with where it came from rather than dropped; that
two rows carrying laps for one entry end the choice instead of resolving it;
and that two spellings of a driver's name produce a candidate, never a merge.

No database: the cursor answers from a queue.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from database.recovery.triplication import (  # noqa: E402
    BLOCKED,
    COMPLEMENTARY_METADATA_ON_DISCARDED,
    DRIVER_IDENTITY_SPLIT,
    ELIGIBLE,
    MATERIAL_DIVERGENCE,
    MULTIPLE_LAP_CARRIERS,
    SOURCE_REVISION_DIVERGES,
    SOURCE_UNVERIFIABLE,
    SPECIAL,
    STATUS_DIVERGES_FROM_SOURCE,
    assess_entry,
    assess_session,
    complementary_metadata,
    driver_identity_candidates,
    material_divergence,
    status_divergences,
    worst,
)

from tests.recovery.test_recovery_guard import CursorFalso  # noqa: E402

# The columns the module reads, in the order `entry_rows` names them.
COLUNAS = ["number", "car_id", "position", "status", "laps_completed",
           "total_time_raw", "total_time_s", "gap_to_first_raw",
           "gap_to_first_s", "gap_to_prev_raw", "gap_to_prev_s",
           "fl_lap_number", "fl_time_raw", "fl_time_s", "fl_kph",
           "team_id", "vehicle", "manufacturer", "car_class", "car_group",
           "tires", "imsa_car_id", "imsa_team_id", "tyre_supplier"]


def linha(car_id: int, number: str = "7", **ajustes) -> dict:
    """One stored row, with the values the class actually holds."""
    base = dict.fromkeys(COLUNAS)
    base.update({
        "number": number, "car_id": car_id, "position": 5,
        "status": "Classified", "laps_completed": 151,
        "total_time_raw": "6:01'23.051", "total_time_s": 21683.051,
        "gap_to_first_raw": "+5.015", "gap_to_first_s": 5.015,
        "gap_to_prev_raw": "+0.112", "gap_to_prev_s": 0.112,
        "fl_lap_number": 30, "fl_time_raw": "2'05.079", "fl_time_s": 125.079,
        "fl_kph": 200.4, "team_id": 362, "vehicle": "Toyota TR010 Hybrid",
        "car_class": "HYPERCAR", "tires": "M", "tyre_supplier": None,
    })
    base.update(ajustes)
    return base


def tres(**ajustes_por_id) -> list[dict]:
    """Three rows for one entry; keyword `id<n>` adjusts the nth."""
    return [linha(10, **ajustes_por_id.get("a", {})),
            linha(20, **ajustes_por_id.get("b", {})),
            linha(30, **ajustes_por_id.get("c", {}))]


# ── The severity ordering ────────────────────────────────────

def test_o_pior_vence():
    assert worst(ELIGIBLE, SPECIAL) == SPECIAL
    assert worst(SPECIAL, BLOCKED) == BLOCKED
    assert worst(ELIGIBLE, ELIGIBLE) == ELIGIBLE
    assert worst(BLOCKED, SPECIAL, ELIGIBLE) == BLOCKED


# ── Contradiction is not silence ─────────────────────────────

def test_tres_linhas_iguais_nao_divergem():
    assert material_divergence(tres()) == {}


def test_um_nulo_nao_e_divergencia():
    """
    The distinction the whole classification rests on.

    A column one ingestion filled and another left empty is not two answers;
    it is one answer and a silence. Counting it as a conflict would block
    1.108 sessions that agree on every value they hold.
    """
    linhas = tres(a={"tyre_supplier": "Michelin"})
    assert material_divergence(linhas) == {}


def test_dois_valores_distintos_divergem():
    linhas = tres(a={"laps_completed": 77})
    assert "laps_completed" in material_divergence(linhas)
    assert material_divergence(linhas)["laps_completed"] == ["151", "77"]


def test_a_divergencia_lista_todas_as_colunas():
    linhas = tres(a={"laps_completed": 77, "gap_to_first_s": 6.225})
    assert set(material_divergence(linhas)) == {"laps_completed", "gap_to_first_s"}


# ── The eligible entry ───────────────────────────────────────

def test_o_portador_das_voltas_e_o_canonico():
    e = assess_entry(855, "7", tres(), portadores=[20])
    assert e.verdict == ELIGIBLE
    assert e.canonical == 20
    assert e.discarded == [10, 30]
    assert e.evidence["criterio"] == "portador das voltas"
    assert e.reasons == []


def test_sem_portador_vence_o_menor_car_id():
    e = assess_entry(855, "7", tres(), portadores=[])
    assert e.verdict == ELIGIBLE
    assert e.canonical == 10
    assert e.discarded == [20, 30]
    assert "menor car_id" in e.evidence["criterio"]


def test_portadores_de_outra_entrada_sao_ignorados():
    """`lap_cars` answers per number; an id from another entry is not ours."""
    e = assess_entry(855, "7", tres(), portadores=[99])
    assert e.canonical == 10
    assert e.evidence["car_ids_com_voltas"] == []


# ── The ambiguous entry ──────────────────────────────────────

def test_dois_portadores_encerram_a_escolha():
    """
    Never resolved automatically, by instruction and by consequence.

    Choosing one of them would give half an entry's laps to a row that does
    not own them.
    """
    e = assess_entry(855, "007", tres(), portadores=[10, 20])
    assert e.verdict == BLOCKED
    assert MULTIPLE_LAP_CARRIERS in e.reasons
    assert e.canonical is None
    assert e.discarded == []
    assert e.evidence["car_ids_com_voltas"] == [10, 20]


def test_tres_portadores_tambem_encerram():
    e = assess_entry(855, "007", tres(), portadores=[10, 20, 30])
    assert e.verdict == BLOCKED
    assert e.canonical is None


# ── The blocked entry ────────────────────────────────────────

def test_a_divergencia_material_bloqueia_e_nao_escolhe():
    e = assess_entry(1670, "10", tres(a={"laps_completed": 77}), portadores=[20])
    assert e.verdict == BLOCKED
    assert MATERIAL_DIVERGENCE in e.reasons
    assert e.canonical is None
    assert e.discarded == []
    assert "laps_completed" in e.evidence["colunas_divergentes"]


def test_bloqueada_nao_reporta_metadado_complementar():
    """Nothing is kept, so nothing is complementary to it."""
    e = assess_entry(1670, "10",
                     tres(a={"laps_completed": 77, "tyre_supplier": "Michelin"}),
                     portadores=[20])
    assert e.verdict == BLOCKED
    assert e.complementary == {}


# ── Complementary metadata, never dropped quietly ────────────

def test_o_metadado_do_descartado_e_reportado_com_proveniencia():
    """
    The 67 entries where the laps and the metadata are on different rows.

    Keeping the laps means not keeping `tyre_supplier`, so the value and the
    row it came from are reported and the entry stops being plain eligible.
    """
    e = assess_entry(855, "7", tres(a={"tyre_supplier": "Michelin"}),
                     portadores=[20])
    assert e.verdict == SPECIAL
    assert COMPLEMENTARY_METADATA_ON_DISCARDED in e.reasons
    assert e.canonical == 20
    assert e.complementary == {
        "tyre_supplier": {"valor": "Michelin", "proveniencia_car_id": 10}}


def test_sem_perda_nao_ha_metadado_complementar():
    e = assess_entry(855, "7", tres(a={"tyre_supplier": "Michelin"}),
                     portadores=[10])
    assert e.verdict == ELIGIBLE
    assert e.complementary == {}


def test_o_canonico_com_o_valor_nao_procura_nos_descartados():
    linhas = tres(a={"tyre_supplier": "Goodyear"}, b={"tyre_supplier": "Michelin"})
    assert complementary_metadata(linhas, canonical=20) == {}


def test_a_proveniencia_e_o_car_id_e_nao_so_o_valor():
    achado = complementary_metadata(tres(c={"tyre_supplier": "Goodyear"}),
                                    canonical=10)
    assert achado["tyre_supplier"]["proveniencia_car_id"] == 30


# ── Session level ────────────────────────────────────────────

def cursor_de_sessao(linhas, portadores, status=None, drv_results=(),
                     drv_laps=()):
    """The queue `assess_session` consumes, in the order it asks."""
    respostas = [
        [tuple(l[c] for c in COLUNAS) for l in linhas],       # entry_rows
        [(n, ids) for n, ids in portadores.items()],          # lap_cars
    ]
    if status is not None:
        respostas.append(status)                              # status_divergences
    respostas.append(list(drv_results))                       # drivers de results
    respostas.append(list(drv_laps))                          # drivers de laps
    return CursorFalso(respostas)


def test_a_sessao_elegivel():
    cur = cursor_de_sessao(tres(), {"7": [20]},
                           status=[("7", "Classified")])
    s = assess_session(cur, 855, source_rows=[{"NUMBER": "7", "STATUS": "Classified"}])
    assert s.verdict == ELIGIBLE
    assert s.counts() == {ELIGIBLE: 1, SPECIAL: 0, BLOCKED: 0}
    assert s.reasons == []


def test_sem_fonte_a_sessao_nao_passa_limpa():
    """Not checking is not the same as checking and finding nothing."""
    cur = cursor_de_sessao(tres(), {"7": [20]})
    s = assess_session(cur, 855, source_rows=None)
    assert s.verdict == SPECIAL
    assert SOURCE_UNVERIFIABLE in s.reasons


def test_o_status_divergente_e_um_impedimento_proprio():
    cur = cursor_de_sessao(tres(), {"7": [20]}, status=[("7", "Classified")])
    s = assess_session(cur, 855,
                       source_rows=[{"NUMBER": "7", "STATUS": "Not classified"}])
    assert s.verdict == SPECIAL
    assert STATUS_DIVERGES_FROM_SOURCE in s.reasons
    assert MATERIAL_DIVERGENCE not in s.reasons
    assert s.evidence["status_divergentes"][0]["banco"] == "Classified"
    assert s.evidence["status_divergentes"][0]["fonte"] == "Not Classified"


def test_a_revisao_da_fonte_e_outro_impedimento():
    """
    The three ingestions disagreeing is not the database disagreeing with a
    file. A session can carry either on its own, so they are counted apart.
    """
    cur = cursor_de_sessao(tres(a={"laps_completed": 77}), {"7": [20]},
                           status=[("7", "Classified")])
    s = assess_session(cur, 1670,
                       source_rows=[{"NUMBER": "7", "STATUS": "Classified"}])
    assert s.verdict == BLOCKED
    assert SOURCE_REVISION_DIVERGES in s.reasons
    assert STATUS_DIVERGES_FROM_SOURCE not in s.reasons
    assert s.evidence["entradas_com_divergencia_material"] == 1


def test_a_sessao_herda_o_pior_veredito_das_entradas():
    linhas = tres() + [linha(40, number="9"), linha(50, number="9"),
                       linha(60, number="9")]
    cur = cursor_de_sessao(linhas, {"7": [20], "9": [40, 50]},
                           status=[("7", "Classified"), ("9", "Classified")])
    s = assess_session(cur, 855, source_rows=[])
    assert s.verdict == BLOCKED
    assert s.counts() == {ELIGIBLE: 1, SPECIAL: 0, BLOCKED: 1}
    assert MULTIPLE_LAP_CARRIERS in s.reasons


# ── Drivers: candidates, never merges ────────────────────────

def test_o_homonimo_vira_candidato_e_nao_fusao():
    cur = cursor_de_sessao(
        tres(), {"7": [20]}, status=[("7", "Classified")],
        drv_results=[(900, "Rui", "ANDRADE")],
        drv_laps=[(788, "Rui", "Andrade")])
    s = assess_session(cur, 855, source_rows=[])
    assert s.verdict == SPECIAL
    assert DRIVER_IDENTITY_SPLIT in s.reasons
    assert s.evidence["pares_de_piloto_candidatos"] == 1
    assert s.evidence["amostra_de_pares"][0] == {
        "id_em_results": 900, "nome_em_results": "Rui ANDRADE",
        "id_em_laps": 788, "nome_em_laps": "Rui Andrade"}
    # Nothing was written and nothing was decided.
    assert all(not sql.upper().startswith(("UPDATE", "INSERT", "DELETE"))
               for sql, _ in cur.executados)


def test_o_piloto_que_ja_tem_voltas_nao_e_candidato():
    cur = CursorFalso([[(788, "Rui", "Andrade")], [(788, "Rui", "Andrade")]])
    assert driver_identity_candidates(cur, 855) == []


def test_sem_um_dos_lados_nao_ha_candidato():
    assert driver_identity_candidates(
        CursorFalso([[(900, "Rui", "ANDRADE")], []]), 855) == []
    assert driver_identity_candidates(
        CursorFalso([[], [(788, "Rui", "Andrade")]]), 855) == []


def test_nomes_diferentes_nao_viram_candidato():
    cur = CursorFalso([[(900, "Rui", "ANDRADE")], [(788, "Raul", "Andrade")]])
    assert driver_identity_candidates(cur, 855) == []


def test_o_recorte_do_nome_nao_separa_a_mesma_pessoa():
    """
    `full_name_key` joins the two fields before folding, so the parser's
    guess about where the surname starts does not turn one driver into two.
    """
    cur = CursorFalso([[(900, "Giedo VAN DER", "GARDE")],
                       [(788, "Giedo", "Van Der Garde")]])
    assert len(driver_identity_candidates(cur, 855)) == 1


# ── The status comparison reads with today's parser ──────────

def test_not_classified_e_comparado_com_o_parser_corrigido():
    cur = CursorFalso([[("7", "Classified")]])
    div = status_divergences(cur, 855, [{"NUMBER": "7", "STATUS": "Not classified"}])
    assert div == [{"number": "7", "bruto": "Not classified",
                    "fonte": "Not Classified", "banco": "Classified"}]


def test_o_numero_ausente_do_banco_nao_conta():
    cur = CursorFalso([[("7", "Classified")]])
    assert status_divergences(cur, 855,
                              [{"NUMBER": "99", "STATUS": "DNF"}]) == []


def test_nada_e_escrito_em_consulta_alguma():
    cur = cursor_de_sessao(tres(), {"7": [20]}, status=[("7", "Classified")])
    assess_session(cur, 855, source_rows=[{"NUMBER": "7", "STATUS": "Classified"}])
    for sql, _ in cur.executados:
        assert sql.upper().lstrip().startswith(("SELECT", "WITH")), sql
