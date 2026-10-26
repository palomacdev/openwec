"""
Two carriers, one entry: are they the same laps, and what else differs?

What these tests pin is the separation the module exists to keep. Sporting
disagreement is not identity disagreement. Equal laps are not interchangeable
`cars` rows. And an answer about equivalence is not permission to write
anything — `candidate_car_ids` always holds both carriers, and no test here
expects it to hold one.

No database: the cursor answers from a queue and records every statement.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from database.recovery.carriers import (  # noqa: E402
    BLOCKED,
    CANDIDATE,
    CAR_ATTRIBUTES,
    DRIVER_ID_DIVERGENCE,
    DRIVER_ID_MISSING,
    EXACT_DUPLICATE,
    EXACT_EQUIVALENCE,
    EXCLUDED_COLUMNS,
    IMPEDIMENT_ANALYTICS_ASYMMETRIC,
    IMPEDIMENT_ANALYTICS_WITHOUT_FK,
    IMPEDIMENT_DRIVER_IDENTITY,
    IMPEDIMENT_EXCLUSIVE_METADATA,
    IMPEDIMENT_RESULT_DRIVERS_DIFFER,
    IMPEDIMENT_RESULT_EXCLUSIVE_INFORMATION,
    IMPEDIMENT_RESULT_MATERIAL_DIFFERENCE,
    NON_EQUIVALENT,
    OUTSIDE_ASSESSMENT_CLASS,
    REQUIRES_SPECIAL_HANDLING,
    SPORTING_CONFLICT,
    SPORTING_EQUIVALENCE_WITH_IDENTITY_AMBIGUITY,
    UNDETERMINED,
    UNDETERMINED_EQUIVALENCE,
    UNMATCHED_LAP,
    RESULT_EXCLUDED_COLUMNS,
    CarrierAssessmentError,
    LapColumns,
    assess_entry,
    compare_carriers,
    compare_lap,
    equivalence_of,
    exclusive_metadata,
    compare_results,
    index_by_lap_number,
    lap_columns,
    result_columns,
    population,
    verdict_of,
    write_report,
)
from database.recovery.carriers import CarrierProfile  # noqa: E402

from tests.recovery.test_recovery_guard import CursorFalso  # noqa: E402

# The sporting set as the live catalogue yields it.
ESPORTIVAS = ("driver_slot", "lap_time_s", "s1_s", "s2_s", "s3_s", "kph",
              "top_speed_kph", "elapsed_raw", "hour_raw", "lap_improvement",
              "crossing_finish_in_pit", "flag_at_fl", "pit_time_s",
              "lap_recorded_at")
COLUNAS = LapColumns(sporting=ESPORTIVAS)


def volta(n: int, driver_id: int | None = 567, **ajustes) -> dict:
    base = {"lap_number": n, "driver_id": driver_id,
            "driver_slot": 1, "lap_time_s": 125.079, "s1_s": 30.1,
            "s2_s": 45.2, "s3_s": 49.779, "kph": 200.4, "top_speed_kph": 290.1,
            "elapsed_raw": "2:05.079", "hour_raw": "14:03:02.538",
            "lap_improvement": False, "crossing_finish_in_pit": False,
            "flag_at_fl": "GF", "pit_time_s": None, "lap_recorded_at": None}
    base.update(ajustes)
    return base


# ── Columns: discovered, not written by hand ─────────────────

def test_as_colunas_vem_do_catalogo():
    cur = CursorFalso([[("id",), ("session_id",), ("car_id",), ("driver_id",),
                        ("lap_number",), ("lap_time_s",), ("created_at",)]])
    c = lap_columns(cur)
    assert c.sporting == ("lap_time_s",)
    assert c.driver == "driver_id"
    assert set(c.excluded) == set(EXCLUDED_COLUMNS)


def test_uma_coluna_nova_entra_na_comparacao_esportiva():
    """
    The default has to be inclusion.

    A hand-written list goes stale: a column added to `laps` would be skipped
    and two laps differing in it would be called identical.
    """
    cur = CursorFalso([[("id",), ("session_id",), ("car_id",), ("driver_id",),
                        ("lap_number",), ("coluna_nova",)]])
    assert "coluna_nova" in lap_columns(cur).sporting


def test_sem_a_tabela_a_leitura_falha_em_vez_de_concluir():
    with pytest.raises(CarrierAssessmentError):
        lap_columns(CursorFalso([[]]))


def test_toda_coluna_excluida_tem_motivo():
    assert all(isinstance(v, str) and v for v in EXCLUDED_COLUMNS.values())


# ── Pair categories ──────────────────────────────────────────

def test_1_voltas_integralmente_identicas():
    p = compare_lap(volta(5), volta(5), COLUNAS)
    assert p.category == EXACT_DUPLICATE
    assert p.evidence == {}


def test_2_driver_id_diferente_nao_e_conflito_esportivo():
    p = compare_lap(volta(5, driver_id=43), volta(5, driver_id=330979), COLUNAS)
    assert p.category == DRIVER_ID_DIVERGENCE
    assert p.evidence["driver_id"] == {"a": 43, "b": 330979}


def test_3_driver_id_nulo_de_um_lado():
    p = compare_lap(volta(5, driver_id=None), volta(5, driver_id=43), COLUNAS)
    assert p.category == DRIVER_ID_MISSING
    assert p.evidence["driver_id"] == {"a": None, "b": 43}


def test_dois_nulos_nao_sao_divergencia():
    assert compare_lap(volta(5, driver_id=None), volta(5, driver_id=None),
                       COLUNAS).category == EXACT_DUPLICATE


@pytest.mark.parametrize("coluna,valor", [
    ("lap_time_s", 126.0), ("s2_s", 44.0), ("kph", 199.0),
    ("elapsed_raw", "2:06.000"), ("hour_raw", "14:04:00.000"),
    ("flag_at_fl", "SC"), ("crossing_finish_in_pit", True),
    ("top_speed_kph", 280.0), ("driver_slot", 2),
])
def test_4_divergencia_material_em_qualquer_coluna_esportiva(coluna, valor):
    p = compare_lap(volta(5), volta(5, **{coluna: valor}), COLUNAS)
    assert p.category == SPORTING_CONFLICT
    assert coluna in p.evidence["colunas"]


def test_14_coluna_inesperada_que_diverge_nao_vira_equivalencia():
    """The safe default, demonstrated: an unknown column still blocks EXACT."""
    colunas = LapColumns(sporting=(*ESPORTIVAS, "coluna_nova"))
    a = volta(5); a["coluna_nova"] = "x"
    b = volta(5); b["coluna_nova"] = "y"
    assert compare_lap(a, b, colunas).category == SPORTING_CONFLICT


def test_atributo_esportivo_ausente_de_um_lado_e_indeterminado():
    """
    A gap is not a conflict and not an identity question.

    One carrier holding a sporting value the other does not is something the
    module reports rather than resolves.
    """
    p = compare_lap(volta(5), volta(5, kph=None), COLUNAS)
    assert p.category == UNDETERMINED
    assert "kph" in p.evidence["atributo_esportivo_ausente_de_um_lado"]


def test_a_evidencia_guarda_coluna_e_valores():
    p = compare_lap(volta(5), volta(5, lap_time_s=126.0), COLUNAS)
    assert p.evidence["colunas"]["lap_time_s"] == {"a": "125.079", "b": "126.0"}


# ── Coverage ─────────────────────────────────────────────────

def test_5_volta_existente_em_apenas_um_portador():
    pares, cob = compare_carriers([volta(1), volta(2)], [volta(1)], COLUNAS)
    categorias = [p.category for p in pares]
    assert categorias == [EXACT_DUPLICATE, UNMATCHED_LAP]
    assert cob.only_a == [2] and cob.only_b == []
    assert cob.shared == 1


def test_6_numero_repetido_no_mesmo_portador_nao_se_resolve():
    """
    No row is picked to stand for the others.

    Choosing one would be the module inventing the answer it was asked to
    find, so the whole entry goes undetermined instead.
    """
    pares, cob = compare_carriers([volta(1), volta(1)], [volta(1)], COLUNAS)
    assert len(pares) == 1 and pares[0].category == UNDETERMINED
    assert cob.repeated_a == [1]
    assert pares[0].evidence[
        "numero_de_volta_repetido_no_mesmo_portador"]["a"] == [1]


def test_a_cobertura_reconcilia_com_o_que_foi_comparado():
    a = [volta(n) for n in (1, 2, 3)]
    b = [volta(n) for n in (2, 3, 4)]
    pares, cob = compare_carriers(a, b, COLUNAS)
    assert cob.rows_a == 3 and cob.rows_b == 3
    assert cob.distinct_a == 3 and cob.distinct_b == 3
    assert cob.shared == 2 and cob.only_a == [1] and cob.only_b == [4]
    assert len(pares) == cob.shared + len(cob.only_a) + len(cob.only_b)


def test_lacunas_e_intervalos_sao_relatados():
    _, cob = compare_carriers([volta(1), volta(3)], [volta(1), volta(3)], COLUNAS)
    assert cob.range_a == [1, 3] and cob.gaps_a == 1


def test_o_indice_encontra_repeticao():
    indice, repetidos = index_by_lap_number([volta(1), volta(1), volta(2)])
    assert repetidos == [1] and len(indice[1]) == 2


# ── Population ───────────────────────────────────────────────

def pop_cursor(carriers: dict[int, int]) -> CursorFalso:
    return CursorFalso([[(c, n) for c, n in carriers.items()]])


def test_7_entrada_fora_da_classe_de_triplicacao():
    p = population(pop_cursor({10: 5, 20: 5}), 855, "7")
    assert p.valid is False
    assert "2 linhas de resultado" in p.reason


def test_8_um_unico_portador():
    p = population(pop_cursor({10: 5, 20: 0, 30: 0}), 855, "7")
    assert p.valid is False
    assert "1 portadores" in p.reason


def test_9_tres_portadores():
    p = population(pop_cursor({10: 5, 20: 5, 30: 5}), 855, "7")
    assert p.valid is False
    assert "3 portadores" in p.reason


def test_a_populacao_valida_tem_tres_linhas_e_dois_portadores():
    p = population(pop_cursor({10: 5, 20: 5, 30: 0}), 855, "7")
    assert p.valid is True
    assert p.carriers == [10, 20]
    assert p.car_ids == [10, 20, 30]


def test_fora_da_populacao_o_veredito_e_bloqueado_sem_criterio_aplicado():
    cur = pop_cursor({10: 5, 20: 5, 30: 5})
    e = assess_entry(cur, 855, "7", COLUNAS, RESULT_COLS)
    assert e.verdict == BLOCKED
    assert e.impediments == [OUTSIDE_ASSESSMENT_CLASS]
    assert e.equivalence == UNDETERMINED_EQUIVALENCE
    assert e.pair_counts == {} and e.candidate_car_ids == []


# ── Equivalence and verdict, kept apart ──────────────────────

@pytest.mark.parametrize("contagens,esperado", [
    ({EXACT_DUPLICATE: 10}, EXACT_EQUIVALENCE),
    ({EXACT_DUPLICATE: 5, DRIVER_ID_DIVERGENCE: 5},
     SPORTING_EQUIVALENCE_WITH_IDENTITY_AMBIGUITY),
    ({EXACT_DUPLICATE: 5, DRIVER_ID_MISSING: 5},
     SPORTING_EQUIVALENCE_WITH_IDENTITY_AMBIGUITY),
    ({EXACT_DUPLICATE: 9, SPORTING_CONFLICT: 1}, NON_EQUIVALENT),
    ({EXACT_DUPLICATE: 9, UNMATCHED_LAP: 1}, UNDETERMINED_EQUIVALENCE),
    ({EXACT_DUPLICATE: 9, UNDETERMINED: 1}, UNDETERMINED_EQUIVALENCE),
    ({}, UNDETERMINED_EQUIVALENCE),
])
def test_a_equivalencia_so_fala_das_voltas(contagens, esperado):
    assert equivalence_of(contagens) == esperado


def test_o_indeterminado_vence_o_conflito():
    """An entry that cannot be read is not an entry that was read and refused."""
    assert equivalence_of({SPORTING_CONFLICT: 1, UNDETERMINED: 1}) \
        == UNDETERMINED_EQUIVALENCE


@pytest.mark.parametrize("equiv,imped,esperado", [
    (EXACT_EQUIVALENCE, [], CANDIDATE),
    (EXACT_EQUIVALENCE, [IMPEDIMENT_EXCLUSIVE_METADATA], REQUIRES_SPECIAL_HANDLING),
    (SPORTING_EQUIVALENCE_WITH_IDENTITY_AMBIGUITY, [], REQUIRES_SPECIAL_HANDLING),
    (NON_EQUIVALENT, [], BLOCKED),
    (UNDETERMINED_EQUIVALENCE, [], BLOCKED),
    (NON_EQUIVALENT, [IMPEDIMENT_DRIVER_IDENTITY], BLOCKED),
])
def test_o_veredito_combina_equivalencia_e_impedimentos(equiv, imped, esperado):
    assert verdict_of(equiv, imped) == esperado


# ── The carriers as whole records ────────────────────────────

def perfil(car_id: int, **car) -> CarrierProfile:
    atributos = dict.fromkeys(CAR_ATTRIBUTES)
    atributos.update({"number": "007", "team_id": 1, "vehicle": "Valkyrie",
                      "car_class": "HYPERCAR", "tires": "M"})
    atributos.update(car)
    return CarrierProfile(car_id=car_id, car=atributos)


def test_10_metadado_exclusivo_de_um_portador():
    achados = exclusive_metadata(perfil(10, tyre_supplier="Michelin"), perfil(20))
    assert achados == {"tyre_supplier": {"valor": "Michelin",
                                         "somente_no_car_id": 10}}


def test_metadado_igual_nao_e_exclusivo():
    assert exclusive_metadata(perfil(10, tyre_supplier="Michelin"),
                              perfil(20, tyre_supplier="Michelin")) == {}


# ── The whole entry, through the cursor ──────────────────────

RESULT_COLS = ("position", "status", "laps_completed", "total_time_s",
               "source_url")
RESULTADO = {"position": 4, "status": "Classified", "laps_completed": 151,
             "total_time_s": "21683.051", "source_url": "https://exemplo/h6"}


def entrada_cursor(carriers, laps_a, laps_b, *,
                   car_a=None, car_b=None, rd_a=(), rd_b=(),
                   counts_a=(0, 0, 0), counts_b=(0, 0, 0),
                   result_a=None, result_b=None):
    """The queue `assess_entry` consumes, in the order it asks."""
    def car_row(car, resultado):
        atributos = dict.fromkeys(CAR_ATTRIBUTES)
        atributos.update({"number": "007", "team_id": 1})
        atributos.update(car or {})
        r = dict(RESULTADO)
        r.update(resultado or {})
        return [tuple(atributos[c] for c in CAR_ATTRIBUTES)
                + ("2026-05-22T10:47:26+00:00", 900)
                + tuple(r[c] for c in RESULT_COLS)]

    def linhas(ls):
        return [tuple(l[c] for c in COLUNAS.all_read) for l in ls]

    return CursorFalso([
        [(c, n) for c, n in carriers.items()],      # lap_carriers
        linhas(laps_a),                             # carrier_lap_rows a
        linhas(laps_b),                             # carrier_lap_rows b
        car_row(car_a, result_a), list(rd_a), [counts_a],   # carrier_profile a
        car_row(car_b, result_b), list(rd_b), [counts_b],   # carrier_profile b
    ])


def test_entrada_integralmente_duplicada_e_candidata():
    cur = entrada_cursor({10: 2, 20: 2, 30: 0},
                         [volta(1), volta(2)], [volta(1), volta(2)])
    e = assess_entry(cur, 855, "007", COLUNAS, RESULT_COLS)
    assert e.equivalence == EXACT_EQUIVALENCE
    assert e.verdict == CANDIDATE
    assert e.impediments == []
    assert e.pair_counts[EXACT_DUPLICATE] == 2


def test_os_dois_portadores_voltam_sempre_em_candidate_car_ids():
    """
    The module never prefers one.

    A lower id, an earlier `created_at` or more references are not evidence
    of correctness, and returning a single id would read as a decision.
    """
    cur = entrada_cursor({10: 1, 20: 1, 30: 0}, [volta(1)], [volta(1)])
    e = assess_entry(cur, 855, "007", COLUNAS, RESULT_COLS)
    assert e.candidate_car_ids == [10, 20]
    assert not hasattr(e, "canonical_car_id")


def test_11_result_drivers_divergentes_viram_impedimento():
    cur = entrada_cursor({10: 1, 20: 1, 30: 0}, [volta(1)], [volta(1)],
                         rd_a=[(1, 43, "Marco", "Sørensen")],
                         rd_b=[(1, 330979, "Marco", "SØRENSEN")])
    e = assess_entry(cur, 855, "009", COLUNAS, RESULT_COLS)
    assert IMPEDIMENT_RESULT_DRIVERS_DIFFER in e.impediments
    assert e.verdict == REQUIRES_SPECIAL_HANDLING
    assert e.equivalence == EXACT_EQUIVALENCE   # as voltas seguem equivalentes


def test_12_analiticas_nos_dois_portadores():
    cur = entrada_cursor({10: 1, 20: 1, 30: 0}, [volta(1)], [volta(1)],
                         counts_a=(1, 1, 7), counts_b=(1, 1, 7))
    e = assess_entry(cur, 855, "007", COLUNAS, RESULT_COLS)
    assert IMPEDIMENT_ANALYTICS_WITHOUT_FK in e.impediments
    assert IMPEDIMENT_ANALYTICS_ASYMMETRIC not in e.impediments
    assert e.verdict == REQUIRES_SPECIAL_HANDLING


def test_13_analiticas_em_apenas_um_portador():
    cur = entrada_cursor({10: 1, 20: 1, 30: 0}, [volta(1)], [volta(1)],
                         counts_a=(1, 1, 7), counts_b=(1, 0, 0))
    e = assess_entry(cur, 855, "007", COLUNAS, RESULT_COLS)
    assert IMPEDIMENT_ANALYTICS_ASYMMETRIC in e.impediments


def test_metadado_exclusivo_chega_ao_relatorio_da_entrada():
    cur = entrada_cursor({10: 1, 20: 1, 30: 0}, [volta(1)], [volta(1)],
                         car_a={"tyre_supplier": "Michelin"})
    e = assess_entry(cur, 855, "007", COLUNAS, RESULT_COLS)
    assert IMPEDIMENT_EXCLUSIVE_METADATA in e.impediments
    assert e.notes["metadado_exclusivo"]["tyre_supplier"][
        "somente_no_car_id"] == 10


def test_o_conflito_esportivo_bloqueia_a_entrada():
    cur = entrada_cursor({10: 1, 20: 1, 30: 0}, [volta(1)],
                         [volta(1, lap_time_s=126.0)])
    e = assess_entry(cur, 855, "007", COLUNAS, RESULT_COLS)
    assert e.equivalence == NON_EQUIVALENT
    assert e.verdict == BLOCKED


def test_15_uma_leitura_que_falha_nao_produz_elegibilidade():
    """
    An error is an error, not a pass.

    A cursor that raises propagates; nothing is classified, and in
    particular nothing is classified CANDIDATE.
    """
    class CursorQueQuebra(CursorFalso):
        def execute(self, sql, params=None):
            raise CarrierAssessmentError("falha de leitura simulada")

    with pytest.raises(CarrierAssessmentError):
        assess_entry(CursorQueQuebra(), 855, "007", COLUNAS, RESULT_COLS)


def test_nenhuma_consulta_do_modulo_escreve():
    cur = entrada_cursor({10: 1, 20: 1, 30: 0}, [volta(1)], [volta(1)])
    assess_entry(cur, 855, "007", COLUNAS, RESULT_COLS)
    for sql, _ in cur.executados:
        assert sql.upper().lstrip().startswith(("SELECT", "WITH")), sql


# ── The report, the one write ────────────────────────────────

def test_o_relatorio_vai_para_onde_se_pede(tmp_path):
    destino = tmp_path / "855.json"
    assert write_report({"session_id": 855}, destino) == 0
    assert '"session_id": 855' in destino.read_text(encoding="utf-8")


def test_sem_destino_nada_e_escrito(tmp_path):
    assert write_report({"session_id": 855}, None) == 0
    assert list(tmp_path.iterdir()) == []


def test_um_destino_impossivel_nao_e_classificado_como_sucesso(tmp_path, capsys):
    assert write_report({"x": 1}, tmp_path / "nao" / "existe.json") == 1
    saida = capsys.readouterr().out
    assert "nao pode ser escrito" in saida and "banco nao foi tocado" in saida


# ── Os `results` dos portadores, comparados a parte ───────────

def test_as_colunas_de_results_vem_do_catalogo():
    cur = CursorFalso([[("id",), ("session_id",), ("car_id",), ("position",),
                        ("status",), ("source_url",), ("created_at",)]])
    assert result_columns(cur) == ("position", "status", "source_url")


def test_uma_coluna_nova_em_results_entra_na_comparacao():
    cur = CursorFalso([[("id",), ("session_id",), ("car_id",), ("coluna_nova",)]])
    assert "coluna_nova" in result_columns(cur)


def test_sem_a_tabela_results_a_leitura_falha():
    with pytest.raises(CarrierAssessmentError):
        result_columns(CursorFalso([[]]))


def test_toda_coluna_de_results_excluida_tem_motivo():
    assert set(RESULT_EXCLUDED_COLUMNS) == {"id", "session_id", "car_id",
                                            "created_at"}
    assert all(isinstance(v, str) and v for v in RESULT_EXCLUDED_COLUMNS.values())


def test_results_identicos_nao_produzem_achado():
    c = compare_results(dict(RESULTADO), dict(RESULTADO), RESULT_COLS)
    assert c["materiais"] == {} and c["exclusivos"] == {}


def test_results_contraditorios_sao_diferenca_material():
    """Os dois afirmam, e afirmam coisas diferentes: so um descreve a inscricao."""
    b = dict(RESULTADO, position=5)
    c = compare_results(dict(RESULTADO), b, RESULT_COLS)
    assert c["materiais"] == {"position": {"a": "4", "b": "5"}}
    assert c["exclusivos"] == {}


def test_results_complementares_sao_informacao_exclusiva():
    """Nada se contradiz, mas descartar aquele portador perderia o valor."""
    b = dict(RESULTADO, source_url=None)
    c = compare_results(dict(RESULTADO), b, RESULT_COLS)
    assert c["materiais"] == {}
    assert c["exclusivos"]["source_url"] == {"valor": "https://exemplo/h6",
                                             "somente_em": "a"}


def test_os_dois_nulos_nao_sao_exclusividade():
    a = dict(RESULTADO, source_url=None)
    b = dict(RESULTADO, source_url=None)
    assert compare_results(a, b, RESULT_COLS)["exclusivos"] == {}


def test_a_comparacao_de_results_registra_o_que_olhou():
    c = compare_results(dict(RESULTADO), dict(RESULTADO), RESULT_COLS)
    assert c["colunas_comparadas"] == list(RESULT_COLS)
    assert set(c["colunas_excluidas"]) == set(RESULT_EXCLUDED_COLUMNS)


def test_results_contraditorios_impedem_candidate():
    cur = entrada_cursor({10: 1, 20: 1, 30: 0}, [volta(1)], [volta(1)],
                         result_b={"position": 5})
    e = assess_entry(cur, 855, "007", COLUNAS, RESULT_COLS)
    assert IMPEDIMENT_RESULT_MATERIAL_DIFFERENCE in e.impediments
    assert e.verdict == REQUIRES_SPECIAL_HANDLING
    # As voltas seguem equivalentes: as duas coisas nao se misturam.
    assert e.equivalence == EXACT_EQUIVALENCE
    assert e.pair_counts[EXACT_DUPLICATE] == 1


def test_informacao_exclusiva_em_results_impede_candidate():
    cur = entrada_cursor({10: 1, 20: 1, 30: 0}, [volta(1)], [volta(1)],
                         result_b={"source_url": None})
    e = assess_entry(cur, 855, "007", COLUNAS, RESULT_COLS)
    assert IMPEDIMENT_RESULT_EXCLUSIVE_INFORMATION in e.impediments
    assert e.verdict == REQUIRES_SPECIAL_HANDLING
    assert e.equivalence == EXACT_EQUIVALENCE


def test_results_iguais_nao_impedem_nada():
    cur = entrada_cursor({10: 1, 20: 1, 30: 0}, [volta(1)], [volta(1)])
    e = assess_entry(cur, 855, "007", COLUNAS, RESULT_COLS)
    assert IMPEDIMENT_RESULT_MATERIAL_DIFFERENCE not in e.impediments
    assert IMPEDIMENT_RESULT_EXCLUSIVE_INFORMATION not in e.impediments
    assert e.verdict == CANDIDATE


def test_a_comparacao_de_results_entra_nas_notas():
    cur = entrada_cursor({10: 1, 20: 1, 30: 0}, [volta(1)], [volta(1)],
                         result_b={"laps_completed": 150})
    e = assess_entry(cur, 855, "007", COLUNAS, RESULT_COLS)
    assert e.notes["comparacao_de_results"]["materiais"]["laps_completed"] == {
        "a": "151", "b": "150"}


def test_as_colunas_comparadas_sao_relatadas_das_duas_tabelas():
    cur = entrada_cursor({10: 1, 20: 1, 30: 0}, [volta(1)], [volta(1)])
    e = assess_entry(cur, 855, "007", COLUNAS, RESULT_COLS)
    assert e.columns_compared["laps"]["identidade"] == "driver_id"
    assert e.columns_compared["results"]["comparadas"] == list(RESULT_COLS)
    assert set(e.columns_compared["results"]["excluidas"]) == set(
        RESULT_EXCLUDED_COLUMNS)


# ── A evidencia e completa, e o limite so exibe ───────────────

def muitas_divergencias(n: int = 25):
    a = [volta(i, driver_id=43) for i in range(1, n + 1)]
    b = [volta(i, driver_id=330979) for i in range(1, n + 1)]
    return a, b


def test_mais_de_vinte_divergencias_sao_contadas_todas():
    a, b = muitas_divergencias(25)
    cur = entrada_cursor({10: 25, 20: 25, 30: 0}, a, b)
    e = assess_entry(cur, 855, "007", COLUNAS, RESULT_COLS)
    assert e.pair_counts[DRIVER_ID_DIVERGENCE] == 25
    assert e.pair_evidence_meta["divergencias"] == 25
    assert e.pair_evidence_meta["exibidas"] == 20
    assert e.pair_evidence_meta["truncado"] is True
    assert len(e.pair_evidence) == 20


def test_o_limite_zero_lista_todas():
    a, b = muitas_divergencias(25)
    cur = entrada_cursor({10: 25, 20: 25, 30: 0}, a, b)
    e = assess_entry(cur, 855, "007", COLUNAS, RESULT_COLS, evidence_limit=0)
    assert e.pair_evidence_meta["exibidas"] == 25
    assert e.pair_evidence_meta["truncado"] is False
    assert len(e.pair_evidence) == 25


@pytest.mark.parametrize("limite", [0, 1, 5, 20, 100])
def test_nem_a_contagem_nem_a_classificacao_dependem_do_limite(limite):
    """
    O limite e de exibicao, e nada mais.

    Uma evidencia cortada nao pode mudar quantos pares divergiram nem o que
    a entrada e.
    """
    a, b = muitas_divergencias(25)
    cur = entrada_cursor({10: 25, 20: 25, 30: 0}, a, b)
    e = assess_entry(cur, 855, "007", COLUNAS, RESULT_COLS, evidence_limit=limite)
    assert e.pair_counts[DRIVER_ID_DIVERGENCE] == 25
    assert e.pair_counts["total"] == 25
    assert e.equivalence == SPORTING_EQUIVALENCE_WITH_IDENTITY_AMBIGUITY
    assert e.verdict == REQUIRES_SPECIAL_HANDLING
    assert e.pair_evidence_meta["divergencias"] == 25


def test_o_relatorio_diz_como_ver_o_resto():
    a, b = muitas_divergencias(25)
    cur = entrada_cursor({10: 25, 20: 25, 30: 0}, a, b)
    e = assess_entry(cur, 855, "007", COLUNAS, RESULT_COLS)
    assert "--evidence-limit 0" in e.pair_evidence_meta["como_ver_o_resto"]


def test_sem_divergencia_a_evidencia_fica_vazia_e_nao_truncada():
    cur = entrada_cursor({10: 2, 20: 2, 30: 0},
                         [volta(1), volta(2)], [volta(1), volta(2)])
    e = assess_entry(cur, 855, "007", COLUNAS, RESULT_COLS)
    assert e.pair_evidence == []
    assert e.pair_evidence_meta == {"divergencias": 0, "exibidas": 0,
                                    "truncado": False, "limite": 20,
                                    "como_ver_o_resto": e.pair_evidence_meta[
                                        "como_ver_o_resto"]}
