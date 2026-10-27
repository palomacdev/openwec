"""
Which IMSA results are copies, and which the planner refuses to touch.

No database: `plan_session` is a pure function over rows and links, and
`build_plan` is exercised against a cursor that answers from a queue.

The refusals are what these tests exist for. A plan that removes a row whose
driver link is not on the row that stays would take that link with it, and
`result_drivers` cascades on delete — so the group must survive the planner,
not the execution.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from database.recovery.dedup_plan import (  # noqa: E402
    DIVERGENT_LINKS,
    EXTERNAL_REFERENCE,
    FACTOR_NOT_UNIFORM,
    NO_DUPLICATION,
    SIGNATURE_FIELDS,
    build_plan,
    choose_preserved,
    read_foreign_keys,
    read_other_references,
    corroboration,
    digest,
    file_signature,
    fold_team,
    group_results,
    plan_digest,
    plan_session,
    read_csv,
    result_signature,
    summarise,
    uniform_factor,
)
from tests.recovery.test_recovery_guard import CursorFalso  # noqa: E402


META = {"session_id": 2686, "session_name": "Practice 1", "series": "IMSA",
        "season": "2016", "event": "Belle Isle", "championship": "WeatherTech"}


def campos(numero: str, **mudanca) -> tuple:
    """Uma linha de resultado, na ordem de SIGNATURE_FIELDS."""
    base = {
        "number": numero, "team": "CORE autosport", "vehicle": "Oreca FLM09",
        "car_class": "PC", "car_group": None, "tires": "C",
        "imsa_car_id": None, "imsa_team_id": None,
        "position": None, "status": "Classified", "laps_completed": 31,
        "total_time_raw": "1:00'01.868", "total_time_s": 3601.868,
        "gap_to_first_raw": None, "gap_to_first_s": None,
        "gap_to_prev_raw": None, "gap_to_prev_s": None,
        "fl_lap_number": 25, "fl_time_raw": "1'48.714", "fl_time_s": 108.714,
        "fl_kph": 178.5,
    }
    base.update(mudanca)
    return tuple(base[c] for c in SIGNATURE_FIELDS)


def copias(numero: str, ids: list[int], **mudanca) -> list[tuple]:
    """As mesmas linhas com ids diferentes — o que uma recarga produz."""
    return [(i, *campos(numero, **mudanca)) for i in ids]


# ── A assinatura ─────────────────────────────────────────────

def test_a_assinatura_exige_todos_os_campos():
    with pytest.raises(ValueError):
        result_signature(("4", "EQUIPE"))


def test_linhas_iguais_em_tudo_menos_o_id_sao_uma_assinatura():
    grupos = group_results(copias("4", [10, 20, 30]))
    assert list(grupos.values()) == [[10, 20, 30]]


def test_um_campo_diferente_ja_separa():
    """Nada e ignorado: se fosse, duas linhas distintas virariam copias."""
    for campo, valor in (("laps_completed", 30), ("fl_kph", 178.6),
                         ("status", "DNF"), ("team", "OUTRA"),
                         ("total_time_raw", "1:00'01.869")):
        grupos = group_results(copias("4", [10]) + copias("4", [20], **{campo: valor}))
        assert len(grupos) == 2, f"{campo} nao separou"


def test_a_escala_decimal_e_a_da_coluna():
    """DECIMAL(8,3) e float tem de comparar iguais quando sao o mesmo valor."""
    from decimal import Decimal
    a = group_results(copias("4", [10], fl_time_s=Decimal("108.714")))
    b = group_results(copias("4", [20], fl_time_s=108.714))
    assert set(a) == set(b)


# ── Fatores ──────────────────────────────────────────────────

def test_fator_1_nao_remove_nada():
    """Requisito 7: a sessao sem duplicacao fica exatamente como esta."""
    grupos = group_results(copias("4", [10]) + copias("22", [11]))
    assert uniform_factor(grupos) == 1
    p = plan_session(META, grupos, {})
    assert p["fator"] == 1
    assert p["ids_removiveis"] == []
    assert p["ids_preservados"] == [10, 11]
    assert p["total_atual"] == p["total_esperado"] == 2
    assert [b["motivo"] for b in p["bloqueios"]] == [NO_DUPLICATION]


@pytest.mark.parametrize("fator", [2, 3, 7])
def test_fator_n_preserva_uma_copia_por_assinatura(fator):
    linhas = []
    for pos, numero in enumerate(("4", "22")):
        linhas += copias(numero, [100 * pos + 10 * i for i in range(1, fator + 1)])
    grupos = group_results(linhas)
    assert uniform_factor(grupos) == fator

    p = plan_session(META, grupos, {})
    assert p["fator"] == fator
    assert p["total_atual"] == 2 * fator
    assert p["total_esperado"] == 2
    assert len(p["ids_preservados"]) == 2
    assert len(p["ids_removiveis"]) == 2 * (fator - 1)
    # O menor id de cada assinatura fica; nenhum id aparece dos dois lados.
    assert p["ids_preservados"] == [10, 110]
    assert not set(p["ids_preservados"]) & set(p["ids_removiveis"])
    assert p["bloqueios"] == []


def test_multiplicidade_desigual_bloqueia_a_sessao_inteira():
    """
    A sessao 2721 do acervo: 39 assinaturas repetindo entre 38 e 394 vezes.
    Um log de voltas com forma de classificacao, nao uma carga multiplicada.
    """
    grupos = group_results(copias("4", [10, 20, 30]) + copias("22", [40, 50]))
    assert uniform_factor(grupos) is None

    p = plan_session(META, grupos, {})
    assert p["fator"] is None
    assert p["ids_removiveis"] == []
    assert p["ids_preservados"] == [10, 20, 30, 40, 50]
    assert p["total_atual"] == p["total_esperado"] == 5
    assert p["bloqueios"][0]["motivo"] == FACTOR_NOT_UNIFORM
    assert p["bloqueios"][0]["multiplicidades"] == [2, 3]


def test_sessao_vazia_nao_tem_fator_e_nao_remove():
    p = plan_session(META, {}, {})
    assert p["fator"] is None and p["ids_removiveis"] == []


# ── Vinculos de piloto ───────────────────────────────────────

def test_vinculos_identicos_liberam_a_remocao():
    grupos = group_results(copias("4", [10, 20, 30]))
    vinculos = {i: {(1, 6178), (2, 6179)} for i in (10, 20, 30)}
    p = plan_session(META, grupos, vinculos)
    assert p["ids_removiveis"] == [20, 30]
    assert p["result_drivers_preservados"] == 2
    assert p["result_drivers_removidos"] == 4


def test_piloto_so_na_copia_removivel_bloqueia_o_grupo():
    """
    O caso do acervo: `Tom Kimber-smith` 6179 nas cinco primeiras copias e
    `Tom Kimber-Smith` 342905 nas duas ultimas. Nenhuma copia carrega os
    dois, entao nenhuma pode ficar sozinha, e decidir que sao a mesma
    pessoa nao cabe aqui.
    """
    grupos = group_results(copias("4", [10, 20, 30]))
    vinculos = {10: {(1, 6178), (2, 6179)},
                20: {(1, 6178), (2, 6179)},
                30: {(1, 6178), (2, 342905)}}
    assert choose_preserved([10, 20, 30], vinculos) is None

    p = plan_session(META, grupos, vinculos)
    assert p["ids_removiveis"] == []
    assert p["ids_preservados"] == [10, 20, 30]
    assert p["total_esperado"] == 3
    bloqueio = p["bloqueios"][0]
    assert bloqueio["motivo"] == DIVERGENT_LINKS
    assert bloqueio["uniao_dos_vinculos"] == [(1, 6178), (2, 6179), (2, 342905)]
    assert bloqueio["vinculos_por_id"]["30"] == [(1, 6178), (2, 342905)]


# ── A copia que carrega a uniao ──────────────────────────────

def test_a_copia_com_todos_os_vinculos_fica_mesmo_nao_sendo_a_primeira():
    """
    O ajuste pedido: a primeira copia tem menos vinculos, outra tem todos.
    Preserva-se a que ja carrega a uniao, sem criar nem mover vinculo.
    """
    grupos = group_results(copias("4", [10, 20, 30]))
    vinculos = {10: {(1, 6178)},
                20: {(1, 6178), (2, 6179)},
                30: {(1, 6178)}}
    assert choose_preserved([10, 20, 30], vinculos) == 20

    p = plan_session(META, grupos, vinculos)
    assert p["ids_preservados"] == [20]
    assert p["ids_removiveis"] == [10, 30]
    assert p["result_drivers_preservados"] == 2
    assert p["bloqueios"] == []


def test_empate_entre_copias_completas_fica_com_o_menor_id():
    vinculos = {i: {(1, 6178), (2, 6179)} for i in (10, 20, 30)}
    assert choose_preserved([10, 20, 30], vinculos) == 10
    assert choose_preserved([30, 10, 20], vinculos) == 30, (
        "choose_preserved honra a ordem recebida; quem ordena e group_results")


def test_sem_vinculo_nenhum_a_uniao_e_vazia_e_a_primeira_fica():
    assert choose_preserved([10, 20], {}) == 10


def test_uma_copia_a_mais_com_vinculo_novo_derruba_a_escolha_anterior():
    """O contrapositivo: basta um vinculo que ninguem mais carregue."""
    vinculos = {10: {(1, 1)}, 20: {(1, 1), (2, 2)}, 30: {(3, 3)}}
    assert choose_preserved([10, 20, 30], vinculos) is None


def test_um_grupo_bloqueado_nao_bloqueia_os_outros():
    """Requisito 6 bloqueia o grupo. O resto da sessao continua removivel."""
    grupos = group_results(copias("4", [10, 20]) + copias("22", [30, 40]))
    vinculos = {10: {(1, 1)}, 20: {(1, 1)}, 30: {(1, 2)}, 40: {(1, 999)}}
    p = plan_session(META, grupos, vinculos)
    assert p["ids_removiveis"] == [20]
    assert sorted(p["ids_preservados"]) == [10, 30, 40]
    assert p["total_atual"] == 4 and p["total_esperado"] == 3
    assert len(p["bloqueios"]) == 1


def test_copia_sem_vinculo_nenhum_pode_sair():
    """Conjunto vazio e subconjunto de qualquer um: nada se perde."""
    grupos = group_results(copias("4", [10, 20]))
    p = plan_session(META, grupos, {10: {(1, 6178)}})
    assert p["ids_removiveis"] == [20]


def test_a_segunda_copia_com_vinculo_e_a_que_fica():
    """
    Sob a regra antiga a primeira ficava e o grupo bloqueava. Agora quem
    carrega a uniao e preservada, e a remocao acontece sem perder vinculo.
    """
    grupos = group_results(copias("4", [10, 20]))
    p = plan_session(META, grupos, {20: {(1, 6178)}})
    assert p["ids_preservados"] == [20]
    assert p["ids_removiveis"] == [10]
    assert p["bloqueios"] == []


# ── Qualquer outra coisa que aponte para um result ───────────

def test_referencia_fora_de_result_drivers_bloqueia_o_grupo():
    grupos = group_results(copias("4", [10, 20]))
    p = plan_session(META, grupos, {}, None, {20: ["stints.result_id"]})
    assert p["ids_removiveis"] == []
    assert p["ids_preservados"] == [10, 20]
    bloqueio = p["bloqueios"][0]
    assert bloqueio["motivo"] == EXTERNAL_REFERENCE
    assert bloqueio["referencias"] == {"20": ["stints.result_id"]}


def test_referencia_na_copia_preservada_nao_bloqueia():
    """Quem fica continua apontado; so a remocao e que precisa estar livre."""
    grupos = group_results(copias("4", [10, 20]))
    p = plan_session(META, grupos, {}, None, {10: ["stints.result_id"]})
    assert p["ids_removiveis"] == [20]
    assert p["bloqueios"] == []


def test_sem_referencias_externas_nada_muda():
    grupos = group_results(copias("4", [10, 20]))
    assert plan_session(META, grupos, {}, None, {}) ==            plan_session(META, grupos, {}, None, None)


def test_as_chaves_sao_lidas_do_catalogo_e_result_drivers_e_a_modelada():
    cur = CursorFalso([[("result_drivers", "result_id", "c"),
                        ("stints", "result_id", "a")]])
    chaves = read_foreign_keys(cur)
    assert chaves[0] == {"tabela": "result_drivers", "coluna": "result_id",
                         "on_delete": "CASCADE", "modelada": True}
    assert chaves[1] == {"tabela": "stints", "coluna": "result_id",
                         "on_delete": "NO ACTION", "modelada": False}


def test_a_chave_modelada_nao_e_consultada_de_novo():
    cur = CursorFalso([[(7,), (9,)]])
    chaves = [{"tabela": "result_drivers", "coluna": "result_id",
               "on_delete": "CASCADE", "modelada": True},
              {"tabela": "stints", "coluna": "result_id",
               "on_delete": "NO ACTION", "modelada": False}]
    assert read_other_references(cur, chaves) == {
        7: ["stints.result_id"], 9: ["stints.result_id"]}
    assert len(cur.executados) == 1
    assert "stints" in cur.sql and "result_drivers" not in cur.sql


# ── Corroboracao por arquivo ─────────────────────────────────

def test_um_arquivo_unico_vira_candidato_com_sha256():
    grupos = group_results(copias("4", [10, 20]))
    indice = {digest(fold_team(a) for a in grupos): [
        {"caminho": "raw/imsa/x/03_Results.CSV", "sha256": "ab" * 32}]}
    p = plan_session(META, grupos, {}, corroboration(grupos, indice))
    assert p["arquivo_candidato"] == "raw/imsa/x/03_Results.CSV"
    assert p["sha256"] == "ab" * 32


def test_candidato_ambiguo_nao_vira_arquivo_unico():
    """Dois arquivos com o mesmo conteudo: os dois ficam listados, nenhum e o."""
    grupos = group_results(copias("4", [10, 20]))
    indice = {digest(fold_team(a) for a in grupos): [
        {"caminho": "raw/imsa/x/03_Results.CSV", "sha256": "ab" * 32},
        {"caminho": "raw/imsa/x/03_Results_Final.CSV", "sha256": "cd" * 32}]}
    p = plan_session(META, grupos, {}, corroboration(grupos, indice))
    assert p["arquivo_candidato"] is None and p["sha256"] is None
    assert len(p["arquivos_candidatos"]) == 2
    # E a ausencia de arquivo nao impede o plano: a evidencia e a repeticao.
    assert p["ids_removiveis"] == [20]


def test_sem_arquivo_o_plano_sai_mesmo_assim():
    grupos = group_results(copias("4", [10, 20]))
    p = plan_session(META, grupos, {}, corroboration(grupos, {}))
    assert p["arquivo_candidato"] is None
    assert p["ids_removiveis"] == [20]


def test_o_nome_da_equipe_so_e_dobrado_para_o_arquivo():
    """
    `upsert_team` conflita na string exata, entao a grafia guardada e a que
    chegou primeiro. Dobrar a caixa na assinatura fundiria duas equipes.
    """
    a = result_signature(campos("4", team="CORE autosport"))
    b = result_signature(campos("4", team="Core Autosport"))
    assert a != b
    assert fold_team(a) == fold_team(b)


def test_a_reconstrucao_historica_le_POSITION_e_nao_POS(tmp_path):
    """
    6e7f610 so conhecia POSITION. Os arquivos IMSA trazem POS, e e por isso
    que `position` esta NULL em todas as linhas IMSA do banco. Reproduzir a
    falha e o ponto: corrigi-la faria a comparacao discordar de tudo.
    """
    csv = tmp_path / "x.CSV"
    csv.write_text("NUMBER;TEAM;POS;LAPS;STATUS\n4;CORE;1;31;Classified\n",
                   encoding="utf-8")
    linhas = read_csv(csv)
    assert linhas[0]["POS"] == "1"
    assert file_signature(linhas[0]).split("\x1e")[8] == ""


# ── Determinismo ─────────────────────────────────────────────

def test_o_plano_nao_depende_da_ordem_de_leitura():
    linhas = copias("4", [30, 10, 20]) + copias("22", [50, 40, 60])
    vinculos = {i: {(1, 7)} for i in (10, 20, 30, 40, 50, 60)}
    a = plan_session(META, group_results(linhas), vinculos)
    b = plan_session(META, group_results(list(reversed(linhas))), vinculos)
    assert a == b
    assert a["ids_preservados"] == [10, 40]
    assert a["ids_removiveis"] == [20, 30, 50, 60]


def test_o_digest_muda_quando_o_plano_muda():
    grupos = group_results(copias("4", [10, 20]))
    um = plan_session(META, grupos, {})
    outro = plan_session(META, group_results(copias("4", [10, 20, 30])), {})
    assert plan_digest([um]) != plan_digest([outro])
    assert plan_digest([um]) == plan_digest([plan_session(META, grupos, {})])


def test_o_digest_ignora_a_ordem_das_sessoes():
    um = plan_session(META, group_results(copias("4", [10, 20])), {})
    dois = plan_session(dict(META, session_id=9), group_results(copias("4", [50, 60])), {})
    assert plan_digest([um, dois]) == plan_digest([dois, um])


# ── O resumo ─────────────────────────────────────────────────

def test_o_resumo_fecha_a_conta():
    """Invariante: atuais == preservados + removiveis, inclusive no bloqueio."""
    sessoes = [
        plan_session(META, group_results(copias("4", [10, 20, 30])), {}),
        plan_session(dict(META, session_id=2), group_results(copias("9", [40])), {}),
        plan_session(dict(META, session_id=3),
                     group_results(copias("7", [50, 60]) + copias("8", [70])), {}),
    ]
    r = summarise(sessoes)
    assert r["results_atuais"] == r["results_preservados"] + r["results_removiveis"]
    assert r["sessoes"] == 3
    assert r["sessoes_sem_duplicacao"] == 1
    assert r["sessoes_bloqueadas_inteiras"] == 1
    assert r["fatores"] == {"1": 1, "3": 1, "None": 1}


def test_o_resumo_separa_bloqueio_de_ausencia_de_duplicacao():
    sem_duplicacao = plan_session(META, group_results(copias("4", [10])), {})
    r = summarise([sem_duplicacao])
    assert r["sessoes_com_bloqueio"] == 0
    assert r["sessoes_sem_duplicacao"] == 1


# ── Contra um cursor ─────────────────────────────────────────

def linha_do_banco(session_id: int, result_id: int, numero: str) -> tuple:
    return (session_id, result_id, *campos(numero))


def test_build_plan_so_seleciona():
    cur = CursorFalso([
        [("result_drivers", "result_id", "c")],
        [linha_do_banco(2686, 10, "4"), linha_do_banco(2686, 20, "4")],
        [(10, 1, 6178), (20, 1, 6178)],
        [(2686, "Practice 1", "IMSA", "2016", "Belle Isle",
          "http://x/Results/16_2016/08_Belle/21_IMSA WeatherTech/2016_P1/a.CSV")],
    ])
    plano = build_plan(cur)
    assert plano["contrato"] == "openwec-dedup-plan-v1"
    assert all(s.strip().upper().startswith("SELECT")
               for s, _ in cur.executados), "o planejador so le"
    # as chaves, e depois results, result_drivers e sessions
    assert len(cur.executados) == 4
    assert plano["dependencias_de_results"][0]["modelada"] is True
    assert plano["results_com_referencia_externa"] == 0

    sessao = plano["sessions"][0]
    assert sessao["session_id"] == 2686
    assert sessao["championship"] == "IMSA WeatherTech"
    assert sessao["ids_removiveis"] == [20]
    assert plano["resumo"]["results_removiveis"] == 1
    assert plano["corroboracao_por_arquivo"] is False


def test_build_plan_sem_resultados_devolve_plano_vazio():
    plano = build_plan(CursorFalso([[("result_drivers", "result_id", "c")], [], [], []]))
    assert plano["sessions"] == []
    assert plano["resumo"]["results_removiveis"] == 0


def test_build_plan_bloqueia_quando_outra_tabela_aponta():
    """Uma chave nao modelada passa a bloquear os ids que ela segura."""
    cur = CursorFalso([
        [("result_drivers", "result_id", "c"), ("stints", "result_id", "a")],
        [(20,)],                                   # stints aponta para o 20
        [linha_do_banco(1, 10, "4"), linha_do_banco(1, 20, "4")],
        [],
        [(1, "Race", "IMSA", "2016", "E", None)],
    ])
    plano = build_plan(cur)
    assert plano["results_com_referencia_externa"] == 1
    sessao = plano["sessions"][0]
    assert sessao["ids_removiveis"] == []
    assert sessao["bloqueios"][0]["motivo"] == EXTERNAL_REFERENCE
    assert plano["resumo"]["grupos_bloqueados_por_referencia"] == 1


def test_build_plan_repetido_da_o_mesmo_digest():
    def executa():
        return build_plan(CursorFalso([
            [("result_drivers", "result_id", "c")],
            [linha_do_banco(1, 10, "4"), linha_do_banco(1, 20, "4"),
             linha_do_banco(2, 30, "9")],
            [(10, 1, 5)],
            [(1, "Race", "IMSA", "2016", "E", None),
             (2, "Race", "IMSA", "2016", "E", None)],
        ]))
    assert executa()["plan_digest"] == executa()["plan_digest"]
