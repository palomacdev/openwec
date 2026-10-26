"""
One folded name, several `drivers` rows: what can actually be proved?

What these tests pin is the distance between the three claims the module has
to keep apart — the names fold alike, the evidence agrees, the rows are one
person — and the refusal to let the first two stand in for the third.

They also pin the two things the brief for this module forbids outright: a
divergent `country` must never read as proof of distinct people, and an IMSA
identifier must never read as an arbiter. `Job van Uitert` carries two of
them, and `Devlin DeFrancesco` contradicts on country while agreeing on the
identifier; both appear here by name.

No `canonical_driver_id` exists, and no test expects `driver_ids` to hold
fewer ids than the group has.

No database: the cursor answers from a queue and records every statement.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from database.recovery.driver_identity import (  # noqa: E402
    CONFLICTING_EVIDENCE,
    CORROBORATED,
    EXCLUDED_COLUMNS,
    EXTERNAL_ID_COLUMNS,
    FLAG_CASE_ONLY,
    FLAG_COUNTRY_CONFLICT,
    FLAG_EVIDENCE_ON_ONE_ROW_ONLY,
    FLAG_EXTERNAL_ID_ABSENT,
    FLAG_EXTERNAL_ID_AGREES,
    FLAG_EXTERNAL_ID_CONFLICT,
    FLAG_MONONYM,
    FLAG_MULTI_ROW,
    FLAG_NO_EVIDENCE_ON_ANY_ROW,
    FLAG_REPEATED_ID_SAME_RESULT,
    FLAG_SAME_CREATION_DATE,
    FLAG_SEGMENTATION,
    IMPEDIMENT_CONTRADICTING,
    IMPEDIMENT_EXCLUSIVE_METADATA,
    IMPEDIMENT_EXTERNAL_ID_CONFLICT,
    IMPEDIMENT_MULTI_ROW,
    IMPEDIMENT_NAME_BEARING_VIEW,
    IMPEDIMENT_NOT_A_PERSON,
    IMPEDIMENT_NO_EVIDENCE,
    INSUFFICIENT_EVIDENCE,
    MEANING,
    NOT_A_PERSON,
    REVIEW_REQUIRED,
    DriverIdentityError,
    DriverRow,
    assess_all,
    assess_group,
    assess_one,
    classify,
    compare_evidence,
    evidence_columns,
    external_id_state,
    group_rows,
    marker_name,
    mononym,
    spelling_difference,
    write_report,
)

from tests.recovery.test_recovery_guard import CursorFalso  # noqa: E402

# The evidence set as the live catalogue yields it.
EVID = ("short_name", "country", "license", "hometown", "imsa_driver_id",
        "imsa_driver_plug_id", "imsa_driver_rating")


def linha(driver_id: int, first: str | None, last: str | None,
          criado: str = "2026-05-22", laps: int = 0, rd: int = 0,
          **evidencia) -> DriverRow:
    base = dict.fromkeys(EVID)
    base.update(evidencia)
    return DriverRow(driver_id=driver_id, first_name=first, last_name=last,
                     created_at=criado, evidence=base, laps=laps,
                     result_drivers=rd)


def cursor(colisoes: list | None = None, carreira: list | None = None,
           repeticoes: int = 1) -> CursorFalso:
    """A cursor queued for `repeticoes` calls of `assess_group`."""
    respostas: list = []
    for _ in range(repeticoes):
        respostas.append(list(colisoes or []))
        respostas.append(list(carreira or []))
    return CursorFalso(respostas)


def avaliar(linhas: list[DriverRow], chave: str = "grupo",
            colisoes: list | None = None, limite: int = 0):
    carreira = [(r.driver_id, f"{r.first_name} {r.last_name}", 0)
                for r in linhas]
    return assess_group(cursor(colisoes, carreira), chave, linhas, EVID,
                        evidence_limit=limite)


# ── Columns: discovered, not written by hand ─────────────────

def test_as_colunas_de_evidencia_vem_do_catalogo():
    cur = CursorFalso([[("id",), ("first_name",), ("last_name",),
                        ("country",), ("imsa_driver_id",), ("created_at",)]])
    assert evidence_columns(cur) == ("country", "imsa_driver_id")


def test_uma_coluna_nova_vira_evidencia_em_vez_de_ser_ignorada():
    """
    The default has to be inclusion.

    A hand-written list goes stale: a column added to `drivers` would be
    skipped, and two rows contradicting in it would be called corroborated.
    """
    cur = CursorFalso([[("id",), ("first_name",), ("last_name",),
                        ("created_at",), ("wikidata_qid",)]])
    assert "wikidata_qid" in evidence_columns(cur)


def test_o_nome_nunca_e_evidencia_sobre_de_quem_ele_e():
    assert "first_name" in EXCLUDED_COLUMNS
    assert "last_name" in EXCLUDED_COLUMNS
    assert "created_at" in EXCLUDED_COLUMNS


def test_tabela_ausente_falha_em_vez_de_devolver_vazio():
    with pytest.raises(DriverIdentityError, match="drivers nao foi encontrada"):
        evidence_columns(CursorFalso([[]]))


# ── The fold is about text, never about people ───────────────

def test_grupos_usam_a_mesma_dobra_do_carregador():
    """`full_name_key` joins the fields before folding, so a moved split point
    does not create a second person."""
    cur = CursorFalso([[
        (4188, "Antonio Felix", "Da Costa", "2026-05-22", *[None] * 7, 301, 1),
        (335124, "Antonio FELIX DA", "COSTA", "2026-06-17", *[None] * 7, 200, 2),
    ]])
    grupos = group_rows(cur, EVID)
    assert len(grupos) == 1
    assert sorted(r.driver_id for r in next(iter(grupos.values()))) == [4188, 335124]


def test_a_diferenca_de_grafia_e_nomeada():
    assert spelling_difference([linha(1, "Marco", "Sørensen"),
                                linha(2, "Marco", "SØRENSEN")]) == "CASE_ONLY"
    assert spelling_difference([linha(1, "Antonio Felix", "Da Costa"),
                                linha(2, "Antonio FELIX DA", "COSTA")]) == "SEGMENTATION"
    assert spelling_difference([linha(1, "A", "B"), linha(2, "A", "B")]) == "IDENTICAL"


# ── Evidence: three states, told apart ───────────────────────

def test_valor_de_uma_linha_so_e_exclusivo_e_nao_concordancia():
    """
    The difference between "the rows corroborate each other" and "one row
    knows something the others do not" is the whole evidential question.
    """
    e = compare_evidence([linha(1, "A", "B", country="GBR"),
                          linha(2, "A", "B")], EVID)
    assert e.exclusive["country"] == {"valor": "GBR", "somente_em": 1}
    assert not e.agreeing
    assert not e.contradicting


def test_null_armazenado_e_silencio_e_nao_concordancia():
    e = compare_evidence([linha(1, "A", "B"), linha(2, "A", "B")], EVID)
    assert set(e.absent) == set(EVID)
    assert not e.agreeing


def test_identificador_externo_distingue_ausencia_concordancia_e_conflito():
    ausente = external_id_state(compare_evidence(
        [linha(1, "A", "B"), linha(2, "A", "B")], EVID))
    concorda = external_id_state(compare_evidence(
        [linha(1, "A", "B", imsa_driver_id=1625),
         linha(2, "A", "B", imsa_driver_id=1625)], EVID))
    conflita = external_id_state(compare_evidence(
        [linha(1, "A", "B", imsa_driver_id=2174),
         linha(2, "A", "B", imsa_driver_id=3013)], EVID))
    assert ausente["imsa_driver_id"]["estado"] == "ABSENT"
    assert concorda["imsa_driver_id"]["estado"] == "AGREEMENT"
    assert conflita["imsa_driver_id"]["estado"] == "CONFLICT"


# ── Marco Sørensen — the largest pair ────────────────────────

def test_marco_sorensen_sem_evidencia_alguma_e_insuficiente():
    """
    43 and 330979: 399 shared laps, and nothing but the name.

    This is the shape of 17 of the 18 pairs behind the carrier divergences —
    case-only, born in different ingestion windows, no independent field on
    either row. It must not be reported as corroborated.
    """
    g = avaliar([linha(43, "Marco", "Sørensen", "2026-05-22", laps=4712, rd=25),
                 linha(330979, "Marco", "SØRENSEN", "2026-06-17", laps=1729, rd=77)],
                chave="marcosørensen")
    assert g.classification == INSUFFICIENT_EVIDENCE
    assert FLAG_CASE_ONLY in g.flags
    assert FLAG_NO_EVIDENCE_ON_ANY_ROW in g.flags
    assert FLAG_SAME_CREATION_DATE not in g.flags
    assert IMPEDIMENT_NO_EVIDENCE in g.impediments
    assert g.driver_ids == [43, 330979]
    assert g.references["laps_total"] == 6441


# ── Antonio Felix da Costa — the moved split point ───────────

def test_antonio_felix_da_costa_difere_por_segmentacao_e_nao_so_por_caixa():
    """
    4188 and 335124: the only one of the 18 pairs whose split point moved.

    `split_compound_name` carries the particle `FELIX DA` into the surname,
    so the two rows disagree field by field while folding to one key.
    """
    g = avaliar([linha(4188, "Antonio Felix", "Da Costa", "2026-05-22",
                       laps=301, rd=1, country="PRT"),
                 linha(335124, "Antonio FELIX DA", "COSTA", "2026-06-17",
                       laps=200, rd=2)],
                chave="antoniofelixdacosta")
    assert g.spelling_difference == "SEGMENTATION"
    assert FLAG_SEGMENTATION in g.flags
    assert FLAG_CASE_ONLY not in g.flags
    # The country is on one row only: it corroborates nothing.
    assert g.classification == REVIEW_REQUIRED
    assert FLAG_EVIDENCE_ON_ONE_ROW_ONLY in g.flags
    assert IMPEDIMENT_EXCLUSIVE_METADATA in g.impediments
    assert "uma linha" in g.justification


# ── Job van Uitert — two external ids for one name ───────────

def test_job_van_uitert_tem_identificadores_externos_conflitantes():
    """
    Four rows, two `imsa_driver_id`. The identifier is not an arbiter, and
    this is the measurement that says so.
    """
    g = avaliar([linha(20, "Job VAN", "Uitert", "2026-05-22"),
                 linha(33521, "Job", "van Uitert", "2026-06-05",
                       country="NLD", imsa_driver_id=2174),
                 linha(45616, "Job", "Van Uitert", "2026-06-05",
                       country="NLD", imsa_driver_id=3013),
                 linha(329349, "Job VAN", "UITERT", "2026-07-13")],
                chave="jobvanuitert")
    assert g.classification == CONFLICTING_EVIDENCE
    assert FLAG_EXTERNAL_ID_CONFLICT in g.flags
    assert FLAG_MULTI_ROW in g.flags
    assert IMPEDIMENT_EXTERNAL_ID_CONFLICT in g.impediments
    assert IMPEDIMENT_MULTI_ROW in g.impediments
    assert g.external_ids["imsa_driver_id"]["estado"] == "CONFLICT"
    assert g.driver_ids == [20, 33521, 45616, 329349]
    # country agrees across the two rows that state it; the conflict is the
    # identifier, and the classification is not softened by the agreement.
    assert "imsa_driver_id" in g.justification


# ── DeFrancesco — country conflicts, the identifier agrees ───

def test_defrancesco_contradiz_no_pais_e_concorda_no_identificador():
    """
    The case that forbids reading a divergent `country` as two people: CAN
    and ITA under the **same** `imsa_driver_id`.

    The classification is `CONFLICTING_EVIDENCE` because something does
    contradict, and the justification has to say, in the same breath, that
    an identifier agrees and that the country proves nothing.
    """
    g = avaliar([linha(16984, "Devlin", "DeFrancesco", "2026-05-22",
                       country="CAN", imsa_driver_id=1625),
                 linha(34676, "Devlin", "Defrancesco", "2026-06-05",
                       country="ITA", imsa_driver_id=1625)],
                chave="devlindefrancesco")
    assert g.classification == CONFLICTING_EVIDENCE
    assert FLAG_COUNTRY_CONFLICT in g.flags
    assert FLAG_EXTERNAL_ID_AGREES in g.flags
    assert g.external_ids["imsa_driver_id"]["estado"] == "AGREEMENT"
    assert "NAO prova pessoas distintas" in g.justification
    assert "CONCORDA" in g.justification
    assert IMPEDIMENT_CONTRADICTING in g.impediments
    # The agreement does not demote the conflict to an external-id conflict.
    assert IMPEDIMENT_EXTERNAL_ID_CONFLICT not in g.impediments


def test_a_contradicao_vence_a_corroboracao_na_precedencia():
    com_ambos = classify(
        [linha(1, "A", "B", country="CAN", imsa_driver_id=5),
         linha(2, "A", "B", country="ITA", imsa_driver_id=5)],
        compare_evidence(
            [linha(1, "A", "B", country="CAN", imsa_driver_id=5),
             linha(2, "A", "B", country="ITA", imsa_driver_id=5)], EVID))
    assert com_ambos[0] == CONFLICTING_EVIDENCE


# ── TBD — a marker, not a person ─────────────────────────────

def test_marcador_tbd_nao_e_uma_pessoa():
    g = avaliar([linha(24121, "Tbd", "Tbd", "2026-05-22", rd=20),
                 linha(361597, "TBD", "TBD", "2026-07-15", rd=8)],
                chave="tbdtbd")
    assert g.classification == NOT_A_PERSON
    assert IMPEDIMENT_NOT_A_PERSON in g.impediments
    assert "marcadores" in g.justification


def test_marcador_vence_a_ausencia_de_evidencia_na_precedencia():
    """A marker group has no evidence either, and must not be reported as
    `INSUFFICIENT_EVIDENCE` — the question does not apply to it."""
    g = avaliar([linha(1, "TBD", "TBD"), linha(2, "Tbd", "Tbd")])
    assert g.classification == NOT_A_PERSON


def test_um_token_real_impede_a_classificacao_como_marcador():
    assert marker_name("TBD", "TBD") is True
    assert marker_name("TBD", "Smith") is False
    assert marker_name(None, "Bankcy") is False
    assert marker_name(None, None) is False


# ── Bankcy — a mononym, and emphatically a person ────────────

def test_pseudonimo_mononimo_nao_e_classificado_como_marcador():
    """
    `Bankcy` races under one name, which leaves `first_name` empty. A rule
    keyed on that shape would erase a competitor; the marker test is on the
    tokens of the name, so it does not fire here.
    """
    g = avaliar([linha(330193, None, "Bankcy", "2026-06-05", laps=379, rd=6),
                 linha(340622, None, "BANKCY", "2026-07-13", laps=63, rd=4)],
                chave="bankcy")
    assert g.classification != NOT_A_PERSON
    assert g.classification == INSUFFICIENT_EVIDENCE
    assert FLAG_MONONYM in g.flags
    assert IMPEDIMENT_NOT_A_PERSON not in g.impediments
    assert mononym(None, "Bankcy") is True
    assert mononym("Devlin", "DeFrancesco") is False


def test_um_mononimo_com_evidencia_concordante_pode_ser_corroborado():
    g = avaliar([linha(1, None, "Hash", country="GBR", imsa_driver_id=99),
                 linha(2, None, "HASH", country="GBR", imsa_driver_id=99)])
    assert g.classification == CORROBORATED
    assert FLAG_MONONYM in g.flags
    assert FLAG_EXTERNAL_ID_AGREES in g.flags


# ── Three or more rows ───────────────────────────────────────

def test_grupo_com_tres_ou_mais_linhas_devolve_todas_e_e_sinalizado():
    g = avaliar([linha(3843, "Roman DE", "Angelis", "2026-05-22", laps=404),
                 linha(23944, "Roman", "Deangelis", "2026-06-05",
                       country="CAN", imsa_driver_id=1379),
                 linha(30341, "Roman", "De Angelis", "2026-06-05",
                       country="CAN", imsa_driver_id=1379, laps=229),
                 linha(334779, "Roman DE", "ANGELIS", "2026-06-17", laps=622),
                 linha(361420, "Roman", "DeAngelis", "2026-07-15",
                       country="CAN", imsa_driver_id=1379)],
                chave="romandeangelis")
    assert g.driver_ids == [3843, 23944, 30341, 334779, 361420]
    assert FLAG_MULTI_ROW in g.flags
    assert IMPEDIMENT_MULTI_ROW in g.impediments
    # Three rows share the identifier; two have none at all. The agreement
    # is real, and it does not reach the two rows that carry the laps.
    assert g.classification == CORROBORATED
    assert g.evidence["agreeing"]["imsa_driver_id"]["em"] == [23944, 30341, 361420]


# ── Nothing is ever chosen ───────────────────────────────────

def test_nenhum_id_canonico_e_escolhido_em_nenhuma_classificacao():
    """
    The strongest evidence available still returns every id.

    More laps, more metadata, an earlier `created_at` and a lower id are all
    present in this group and none of them selects a row.
    """
    for linhas in (
        [linha(1, "A", "B", country="GBR", imsa_driver_id=7, laps=5000, rd=90),
         linha(2, "A", "B", country="GBR", imsa_driver_id=7, laps=1, rd=1)],
        [linha(1, "TBD", "TBD"), linha(2, "Tbd", "Tbd")],
        [linha(1, "A", "B"), linha(2, "A", "B")],
    ):
        g = avaliar(linhas)
        assert g.driver_ids == [1, 2]
        assert not hasattr(g, "canonical_driver_id")
        assert "canonical_driver_id" not in g.as_dict()


def test_o_relatorio_inteiro_nunca_carrega_um_id_canonico():
    cur = CursorFalso([
        [("id",), ("first_name",), ("last_name",), ("created_at",), ("country",)],
        [(1, "A", "B", "2026-05-22", None, 10, 2),
         (2, "A", "B", "2026-06-05", None, 3, 1)],
        [(0, 0)],
        [], [(1, "A B", 0), (2, "A B", 0)],
    ])
    dados = assess_all(cur)
    assert "canonical" not in json_text(dados)


def json_text(dados: dict) -> str:
    import json
    return json.dumps(dados, default=str)


# ── The evidence limit never moves a group ───────────────────

def test_o_limite_de_evidencias_nao_muda_a_classificacao():
    linhas = [linha(1, "A", "B", country="CAN", license="FIA",
                    hometown="X", short_name="ABC"),
              linha(2, "A", "B", country="ITA", license="FIA",
                    hometown="Y", short_name="ABD")]
    completo = avaliar(linhas, limite=0)
    cortado = avaliar(linhas, limite=1)
    assert completo.classification == cortado.classification == CONFLICTING_EVIDENCE
    assert completo.flags == cortado.flags
    assert completo.impediments == cortado.impediments
    assert completo.evidence_meta["truncado"] is False
    assert cortado.evidence_meta["truncado"] is True
    assert cortado.evidence_meta["entradas"] == completo.evidence_meta["entradas"]
    assert cortado.evidence_meta["exibidas"] == 1
    assert "--evidence-limit 0" in cortado.evidence_meta["como_ver_o_resto"]


def test_o_limite_mostra_a_contradicao_antes_do_resto():
    """When the list is cut, what survives is what most constrains a
    reconciliation."""
    g = avaliar([linha(1, "A", "B", country="CAN", license="FIA"),
                 linha(2, "A", "B", country="ITA")], limite=1)
    assert list(g.evidence["contradicting"]) == ["country"]
    assert not g.evidence["agreeing"]


# ── Dependencies ─────────────────────────────────────────────

def test_a_view_que_carrega_o_nome_e_sempre_um_impedimento():
    """`v_driver_career` renders `first_name || ' ' || last_name` and is
    reachable by no foreign key, so a constraint-based search misses it."""
    g = avaliar([linha(43, "Marco", "Sørensen"), linha(330979, "Marco", "SØRENSEN")])
    assert IMPEDIMENT_NAME_BEARING_VIEW in g.impediments
    assert g.dependencies["v_driver_career"]["nomes_distintos"] == 2


def test_id_repetido_no_mesmo_result_nao_e_evidencia_de_duas_pessoas():
    """
    Two **different** ids in one result would be positive evidence of
    distinct people. The **same** id twice is a pre-existing duplication,
    and the module must not conflate them.
    """
    g = avaliar([linha(24121, "Tbd", "Tbd"), linha(361597, "TBD", "TBD")],
                colisoes=[(61260, 24121, 24121)])
    assert FLAG_REPEATED_ID_SAME_RESULT in g.flags
    assert g.dependencies["colisoes_em_result_drivers"][
        "ids_distintos_no_mesmo_result"] == []


def test_ids_distintos_no_mesmo_result_viram_impedimento():
    from database.recovery.driver_identity import (
        FLAG_DISTINCT_IDS_SAME_RESULT, IMPEDIMENT_DISTINCT_IDS_SAME_RESULT)
    g = avaliar([linha(1, "A", "B"), linha(2, "A", "B")],
                colisoes=[(500, 1, 2)])
    assert FLAG_DISTINCT_IDS_SAME_RESULT in g.flags
    assert IMPEDIMENT_DISTINCT_IDS_SAME_RESULT in g.impediments


# ── Reading failures ─────────────────────────────────────────

def test_uma_chave_inexistente_falha_em_vez_de_devolver_grupo_vazio():
    cur = CursorFalso([
        [("id",), ("first_name",), ("last_name",), ("created_at",), ("country",)],
        [(1, "Outro", "Nome", "2026-05-22", None, 0, 0)],
    ])
    with pytest.raises(DriverIdentityError, match="nenhuma linha"):
        assess_one(cur, "marcosørensen")


def test_falha_de_leitura_propaga_e_nada_e_classificado():
    class CursorQuebrado:
        def execute(self, sql, params=None):
            raise RuntimeError("connection closed")

    with pytest.raises(RuntimeError, match="connection closed"):
        evidence_columns(CursorQuebrado())


def test_relatorio_em_destino_invalido_nao_derruba_a_leitura(tmp_path):
    destino = tmp_path / "sem-tal-pasta" / "r.json"
    assert write_report({"grupos": 1}, destino) == 1
    assert write_report({"grupos": 1}, None) == 0
    bom = tmp_path / "r.json"
    assert write_report({"grupos": 1}, bom) == 0
    assert bom.read_text(encoding="utf-8").strip().startswith("{")


# ── Only SELECT ──────────────────────────────────────────────

def test_nenhuma_instrucao_de_escrita_e_emitida():
    cur = CursorFalso([
        [("id",), ("first_name",), ("last_name",), ("created_at",), ("country",)],
        [(1, "A", "B", "2026-05-22", "GBR", 10, 2),
         (2, "A", "B", "2026-06-05", "GBR", 3, 1)],
        [(0, 0)],
        [], [(1, "A B", 0), (2, "A B", 0)],
    ])
    assess_all(cur)
    import re

    texto = cur.sql.upper()
    # Whole words only: `created_at` is a column, not a `CREATE`. The naive
    # substring test passes for the wrong reason and fails for it too.
    for proibida in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "ALTER",
                     "DROP", "CREATE", "GRANT", "COMMIT"):
        assert not re.search(rf"\b{proibida}\b", texto), proibida
    # And every statement begins with SELECT, which the word test alone
    # would not establish.
    for sql, _ in cur.executados:
        assert sql.strip().upper().startswith("SELECT"), sql
    assert texto.count("SELECT") >= 3


# ── The whole population ─────────────────────────────────────

def test_assess_all_so_devolve_grupos_e_conta_a_tabela_inteira():
    """A row whose name is unique is counted in the table but is not a group."""
    cur = CursorFalso([
        [("id",), ("first_name",), ("last_name",), ("created_at",), ("country",)],
        [(1, "A", "B", "2026-05-22", None, 10, 2),
         (2, "A", "B", "2026-06-05", None, 3, 1),
         (3, "Sozinho", "Unico", "2026-05-22", "BRA", 7, 1)],
        [(0, 0)],
        [], [(1, "A B", 0), (2, "A B", 0)],
    ])
    dados = assess_all(cur)
    assert dados["drivers_total"] == 3
    assert dados["grupos"] == 1
    assert dados["linhas_em_grupos"] == 2
    assert dados["linhas_absorvidas_se_uma_por_grupo"] == 1
    assert dados["classificacoes"] == {INSUFFICIENT_EVIDENCE: 1}
    assert dados["referencias"]["laps_em_grupos"] == 13


def test_todo_significado_declara_o_que_a_classificacao_nao_afirma():
    assert "NAO significa identidade comprovada" in MEANING[CORROBORATED]
    assert "NAO prova pessoas distintas" in MEANING[CONFLICTING_EVIDENCE]
    assert set(MEANING) == {CORROBORATED, REVIEW_REQUIRED, CONFLICTING_EVIDENCE,
                            INSUFFICIENT_EVIDENCE, NOT_A_PERSON}


def test_os_identificadores_externos_sao_tratados_como_campos_comuns():
    """They appear in the evidence comparison like any other column; the
    separate report is presentation, not a different rule."""
    for coluna in EXTERNAL_ID_COLUMNS:
        assert coluna in EVID
        assert coluna not in EXCLUDED_COLUMNS


# ── Weak contradictions, measured and not assumed ────────────

def test_a_estabilidade_dos_atributos_e_medida_e_nao_afirmada():
    """
    Rows sharing one `imsa_driver_id` are the closest this table has to a
    known-same-person set. A column that disagrees across them disagrees
    about one person.
    """
    from database.recovery.driver_identity import attribute_stability

    cur = CursorFalso([[(15, 71)], [(8, 71)]])
    medida = attribute_stability(cur, ("imsa_driver_id", "imsa_driver_plug_id",
                                       "imsa_driver_rating"))
    colunas = medida["colunas"]
    # imsa_driver_id is the key of the measurement, so it is not measured
    # against itself.
    assert "imsa_driver_id" not in colunas
    assert colunas["imsa_driver_plug_id"]["nas_quais_a_coluna_diverge"] == 15
    assert colunas["imsa_driver_plug_id"]["fraca"] is True
    assert colunas["imsa_driver_rating"]["fraca"] is True


def test_medicao_sem_linha_falha_fechado():
    from database.recovery.driver_identity import attribute_stability

    with pytest.raises(DriverIdentityError, match="estabilidade"):
        attribute_stability(CursorFalso([]), ("country",))


def test_contradicao_so_em_atributo_fraco_e_sinalizada_sem_mudar_a_classe():
    """
    `Dion von Moltke`: country, licence, rating and `imsa_driver_id` all
    agree, and only the slot id differs. It stays `CONFLICTING_EVIDENCE` —
    something does contradict — and the flag says the contradiction is of
    the weakest kind.
    """
    from database.recovery.driver_identity import FLAG_WEAK_CONTRADICTION_ONLY

    g = avaliar([linha(6519, "Dion", "Von Moltke", country="USA",
                       license="Silver", imsa_driver_id=118,
                       imsa_driver_plug_id=3),
                 linha(15088, "Dion", "VON MOLTKE", country="USA",
                       license="Silver", imsa_driver_id=118,
                       imsa_driver_plug_id=4)])
    assert g.classification == CONFLICTING_EVIDENCE
    assert FLAG_WEAK_CONTRADICTION_ONLY in g.flags
    assert FLAG_EXTERNAL_ID_AGREES not in g.flags  # the conflict wins the flag
    assert "instaveis" in g.justification


def test_contradicao_em_atributo_forte_nao_recebe_a_flag_de_fraqueza():
    from database.recovery.driver_identity import FLAG_WEAK_CONTRADICTION_ONLY

    g = avaliar([linha(1, "A", "B", country="CAN", imsa_driver_plug_id=3),
                 linha(2, "A", "B", country="ITA", imsa_driver_plug_id=4)])
    assert FLAG_WEAK_CONTRADICTION_ONLY not in g.flags


# ── The marker that carries an external identifier ───────────

def test_identificador_externo_concordante_sobre_um_marcador():
    """
    `TBD` carries `imsa_driver_id = 1240` on both rows, and no real driver
    carries it. The strongest corroboration this table offers agrees about
    something that is not a person.
    """
    from database.recovery.driver_identity import FLAG_EXTERNAL_ID_ON_A_MARKER

    g = avaliar([linha(24121, "Tbd", "Tbd", imsa_driver_id=1240,
                       imsa_driver_plug_id=1, rd=20),
                 linha(361597, "TBD", "TBD", imsa_driver_id=1240,
                       imsa_driver_plug_id=1, rd=8)],
                chave="tbdtbd")
    assert g.classification == NOT_A_PERSON
    assert FLAG_EXTERNAL_ID_ON_A_MARKER in g.flags
    assert "nao estabelece pessoa" in g.justification
    # And the agreement does not promote it out of NOT_A_PERSON.
    assert g.classification != CORROBORATED


# ── The classification is about the group, not about two rows ─

def test_a_corroboracao_pode_nao_alcancar_as_linhas_que_carregam_as_voltas():
    """
    `Roman De Angelis`: three rows share `imsa_driver_id`, and the two that
    carry the laps have none. PR #46 §6.1 measured this; consolidating only
    what is corroborated would leave exactly the colliding rows untouched.
    """
    from database.recovery.driver_identity import corroboration_reaches

    g = avaliar([linha(3843, "Roman DE", "Angelis", laps=404),
                 linha(23944, "Roman", "Deangelis", imsa_driver_id=1379),
                 linha(30341, "Roman", "De Angelis", imsa_driver_id=1379),
                 linha(334779, "Roman DE", "ANGELIS", laps=622),
                 linha(361420, "Roman", "DeAngelis", imsa_driver_id=1379)])
    assert g.classification == CORROBORATED
    alcance = corroboration_reaches(g, [3843, 334779])
    assert alcance["alcance"] == "NENHUM"
    assert alcance["ids_corroborados"] == []


def test_alcance_total_e_parcial_sao_distinguidos():
    from database.recovery.driver_identity import corroboration_reaches

    g = avaliar([linha(1, "A", "B", country="GBR"),
                 linha(2, "A", "B", country="GBR"),
                 linha(3, "A", "B")])
    assert corroboration_reaches(g, [1, 2])["alcance"] == "TODOS"
    assert corroboration_reaches(g, [1, 3])["alcance"] == "PARCIAL"
    assert corroboration_reaches(g, [3])["alcance"] == "NENHUM"


def test_sem_corroboracao_no_grupo_nao_e_o_mesmo_que_nenhum_alcance():
    """`NENHUM` says evidence exists and misses these rows. `SEM_CORROBORACAO`
    says there is none to miss. Collapsing them would hide which."""
    from database.recovery.driver_identity import corroboration_reaches

    g = avaliar([linha(1, "A", "B"), linha(2, "A", "B")])
    assert corroboration_reaches(g, [1, 2])["alcance"] == "SEM_CORROBORACAO_NO_GRUPO"


# ── Reconciling with the carriers of PR #58 ──────────────────

def test_a_reconciliacao_separa_comparavel_por_nome_de_identidade_provada():
    from database.recovery.driver_identity import carrier_reconciliation

    cur = CursorFalso([
        # evidence_columns
        [("id",), ("first_name",), ("last_name",), ("created_at",), ("country",)],
        # group_rows
        [(43, "Marco", "Sørensen", "2026-05-22", None, 4712, 25),
         (330979, "Marco", "SØRENSEN", "2026-06-17", None, 1729, 77)],
        # CARRIER_PAIRS_SQL: one diverging pair and one missing-id pair
        [(43, 330979, 399), (43, None, 12)],
        # assess_group for the one implicated key: collisions, career view
        [], [(43, "Marco Sørensen", 0), (330979, "Marco SØRENSEN", 0)],
    ])
    r = carrier_reconciliation(cur)
    assert r["pares_de_volta_com_driver_id_divergente"] == 399
    assert r["no_mesmo_grupo_de_nome_equivalente"] == 399
    assert r["em_grupos_diferentes"] == 0
    assert r["pares_de_volta_com_driver_id_ausente_de_um_lado"] == 12
    assert r["grupos_implicados"] == 1
    assert r["grupos_implicados_por_classificacao"] == {INSUFFICIENT_EVIDENCE: 1}
    assert "nao e identidade comprovada" in r["o_que_isto_nao_diz"]
    assert "nao se resolve normalizando" in r["o_que_isto_nao_diz"]


def test_um_id_ausente_de_um_lado_nunca_entra_nas_divergencias():
    """A missing id is an absent fact. No fold supplies it, and counting it
    among the name-comparable pairs would promise a resolution that
    normalisation cannot deliver."""
    from database.recovery.driver_identity import carrier_reconciliation

    cur = CursorFalso([
        [("id",), ("first_name",), ("last_name",), ("created_at",), ("country",)],
        [(1, "A", "B", "2026-05-22", None, 0, 0),
         (2, "A", "B", "2026-06-05", None, 0, 0)],
        [(None, 2, 50), (1, None, 30)],
    ])
    r = carrier_reconciliation(cur)
    assert r["pares_de_volta_com_driver_id_divergente"] == 0
    assert r["pares_de_volta_com_driver_id_ausente_de_um_lado"] == 80
    assert r["grupos_implicados"] == 0


# ── The fold's own blind spot ────────────────────────────────

def test_a_dobra_nao_dobra_diacriticos_e_o_modulo_mede_isso():
    """
    `Jérôme` and `Jerome` are two keys, so their rows are never compared.
    That is deliberate — the fold has to be the loader's — and it makes the
    group count a lower bound, which the report must say rather than imply.
    """
    from database.recovery.driver_identity import fold_blind_spot

    todos = {
        "jeromedesadeleer": [linha(1, "Jerome", "De Sadeleer")],
        "jérômedesadeleer": [linha(2, "Jérôme", "De Sadeleer")],
        "marcosørensen": [linha(3, "Marco", "Sørensen"),
                          linha(4, "Marco", "SØRENSEN")],
    }
    cego = fold_blind_spot(todos)
    assert cego["grupos_com_a_dobra_atual"] == 1
    assert cego["grupos_que_so_existem_dobrando_acento"] == 1
    assert "jeromedesadeleer" in cego["exemplos"]
    assert "limite inferior" in cego["leitura"]


def test_o_ponto_cego_nao_altera_nenhuma_chave_nem_classificacao():
    from database.recovery.driver_identity import fold_blind_spot

    todos = {"a": [linha(1, "A", "B"), linha(2, "A", "B")]}
    antes = {k: [r.driver_id for r in v] for k, v in todos.items()}
    fold_blind_spot(todos)
    assert {k: [r.driver_id for r in v] for k, v in todos.items()} == antes


# ── Non-interference with the earlier assessments ────────────

def test_o_modulo_nao_importa_nem_alcanca_as_avaliacoes_anteriores():
    """
    The matrices of PR #57 and PR #58 are not this module's to change.

    The guarantee is structural rather than numerical: nothing from
    `triplication` or `carriers` is imported, so no code path here can reach
    their verdicts. `row_identity.full_name_key` is the only borrowed
    function, and it only reads.
    """
    import database.recovery.driver_identity as mod

    fonte = Path(mod.__file__).read_text(encoding="utf-8")
    assert "import triplication" not in fonte
    assert "from triplication" not in fonte
    assert "import carriers" not in fonte
    assert "from carriers" not in fonte
    assert "from row_identity import full_name_key" in fonte


def test_nenhum_script_de_fusao_ou_normalizacao_e_alcancavel():
    """
    Naming a script in a comment is documentation; importing it or shelling
    out to it is reach. Only the second is forbidden here, so the module
    stays free to explain what `normalize_drivers.py` did to the table.
    """
    import ast

    import database.recovery.driver_identity as mod

    arvore = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
    importados = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            importados.update(a.name for a in no.names)
        elif isinstance(no, ast.ImportFrom):
            importados.add(no.module or "")
    for proibido in ("merge_drivers", "normalize_drivers", "enrich_wikidata",
                     "subprocess", "os"):
        assert not any(proibido in nome for nome in importados), proibido
    # And no dynamic escape hatch either.
    chamadas = {no.func.id for no in ast.walk(arvore)
                if isinstance(no, ast.Call) and isinstance(no.func, ast.Name)}
    assert not {"exec", "eval", "__import__"} & chamadas


def test_o_limite_de_evidencias_nao_muda_o_alcance_da_corroboracao():
    """
    The regression this guards: `corroboration_reaches` once read the
    **displayed** evidence, so a low `--evidence-limit` could drop the
    agreeing field that covered a row and turn `TODOS` into
    `SEM_CORROBORACAO_NO_GRUPO`. Coverage now comes from the whole evidence,
    captured before truncation.
    """
    from database.recovery.driver_identity import corroboration_reaches

    linhas = [linha(1, "A", "B", country="GBR", license="FIA",
                    hometown="X", short_name="ABC", imsa_driver_id=7),
              linha(2, "A", "B", country="GBR", license="FIA",
                    hometown="X", short_name="ABC", imsa_driver_id=7)]
    completo = avaliar(linhas, limite=0)
    cortado = avaliar(linhas, limite=1)
    assert cortado.evidence_meta["truncado"] is True
    assert completo.corroborated_ids == cortado.corroborated_ids == [1, 2]
    assert (corroboration_reaches(completo, [1, 2])["alcance"]
            == corroboration_reaches(cortado, [1, 2])["alcance"] == "TODOS")


def test_uma_linha_sozinha_nao_e_grupo_e_nao_e_classificada():
    """
    With one row nothing can agree or contradict, so every label would
    describe the absence of a second row rather than evidence about a
    person. The module refuses instead of answering.
    """
    cur = CursorFalso([
        [("id",), ("first_name",), ("last_name",), ("created_at",), ("country",)],
        [(99, "Sozinho", "Unico", "2026-05-22", "BRA", 7, 1)],
    ])
    with pytest.raises(DriverIdentityError, match="unica linha"):
        assess_one(cur, "Sozinho Unico")
