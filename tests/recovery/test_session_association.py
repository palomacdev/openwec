"""
Is this result hanging off the session it came from?

What these tests pin is the distance between three things the module must
never collapse: a name that repeats, a corpus that agrees with itself, and
evidence from outside the corpus. The third does not exist, and the report
says so in a field rather than in a footnote.

They also pin the two refusals the contract for this module was revised
around: an observational signal never reaches `ASSOCIATION_SUSPECT` on its
own, and `CHAMPIONSHIP_NEVER_LOADED` never reaches the cohort on its own.

No database: `assess_sessions` is a pure function over rows, and
`read_sessions` is exercised against a cursor that answers from a queue and
records every statement.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from database.recovery.session_association import (  # noqa: E402
    AMBIGUOUS_NAME_ONLY,
    AMBIGUOUS_SESSION_NAME,
    ASSOCIATION_SUSPECT,
    CAR_SET_IDENTICAL_TO_OTHER_CHAMPIONSHIP,
    CARS_REPEATED_WITHIN_SESSION,
    CHAMPIONSHIP_LAYER,
    CHAMPIONSHIP_LAYER_NOT_APPLICABLE,
    CHAMPIONSHIP_NEVER_LOADED,
    CHAMPIONSHIP_PROFILES_INDISTINGUISHABLE,
    CLASS_PROFILE_FOREIGN,
    CLASSIFICATION_MEANING,
    CLASSIFICATIONS,
    CONTENT_MULTIPLE_OF_SIBLING,
    CORPUS_DERIVED,
    INDEPENDENT,
    LAYER_MIXED,
    LAYER_REAL,
    LAYER_SERIES_LABEL,
    NEVER_LOADED_SERIES,
    NO_NAME_AMBIGUITY,
    NO_PROVENANCE_URL,
    OBSERVED,
    PROVENANCE_METADATA_INCONSISTENT,
    SESSION_HAS_NO_RESULTS,
    SIBLING_SAME_NAME_EMPTY,
    STRUCTURAL_INCOMPATIBILITY_COHORT,
    TIER_MEANING,
    UNDETERMINED_NO_PROVENANCE,
    SessionAssociationError,
    SessionRow,
    assess_sessions,
    car_set_digest,
    championship_of,
    class_profiles,
    classify,
    layer_of,
    multiple_of_sibling,
    read_sessions,
    content_multiset,
    multiplicity_factor,
    result_content_digest,
    signals_for,
    write_report,
)

from tests.recovery.test_recovery_guard import CursorFalso  # noqa: E402

URL_WT = ("https://imsa.results.alkamelcloud.com/Results/24_2024/"
          "13_Canadian%20Tire/01_IMSA%20WeatherTech%20SportsCar%20Championship/"
          "202407131035_Practice%202/03_Results.CSV")
URL_VP = ("https://imsa.results.alkamelcloud.com/Results/24_2024/"
          "13_Canadian%20Tire/03_IMSA%20VP%20Racing%20SportsCar%20Challenge/"
          "202407120835_Practice%201/03_Results.CSV")


def sessao(sid: int, *, evento: int = 1, nome: str = "Practice 1",
           serie: str = "IMSA", url: str | None = URL_VP,
           carros: list | None = None, classes: list | None = None,
           resultados: int | None = None, voltas: int = 0,
           conteudo: str | None = None,
           linhas: list | None = None) -> SessionRow:
    numeros = carros if carros is not None else []
    s = SessionRow(session_id=sid, event_id=evento, name=nome,
                   raw_id=f"2024071208{sid:02d}_{nome}", session_at=None,
                   source_url=url, event_name="Canadian Tire", season="2024",
                   series=serie)
    s.championship = championship_of(url)
    s.layer = layer_of(serie)
    s.car_numbers = numeros
    s.classes = sorted(classes or [])
    s.results = len(numeros) if resultados is None else resultados
    s.laps = voltas
    s.car_set = car_set_digest(numeros)
    brutas = linhas if linhas is not None else [(n,) for n in numeros]
    s.content = conteudo if conteudo is not None else (
        result_content_digest(brutas) if brutas else None)
    s.content_multiset = content_multiset(brutas) if brutas else {}
    return s


def avaliar(sessoes: list[SessionRow]):
    return {a.session["session_id"]: a.as_dict()
            for a in assess_sessions(sessoes, 0)}


# ── O campeonato sai da URL, e o prefixo numerico varia ───────

def test_o_campeonato_vem_do_setimo_segmento_sem_o_prefixo():
    assert championship_of(URL_WT) == "IMSA WeatherTech SportsCar Championship"
    assert championship_of(URL_VP) == "IMSA VP Racing SportsCar Challenge"


def test_o_mesmo_campeonato_com_prefixo_diferente_e_o_mesmo():
    a = URL_WT
    b = URL_WT.replace("/01_IMSA%20WeatherTech", "/21_IMSA%20WeatherTech")
    assert championship_of(a) == championship_of(b)


def test_url_ausente_ou_curta_nao_inventa_campeonato():
    assert championship_of(None) is None
    assert championship_of("https://x/y") is None
    assert championship_of("https://x/a/b/c//g") is None


# ── A camada por serie (ajustes 1 e 8) ────────────────────────

def test_a_camada_por_serie_e_a_aprovada():
    assert layer_of("IMSA") == LAYER_REAL
    assert layer_of("ELMS") == LAYER_REAL
    assert layer_of("LEMANSCUP") == LAYER_MIXED
    assert layer_of("WEC") == LAYER_SERIES_LABEL
    assert layer_of("ALMS") == LAYER_SERIES_LABEL


def test_uma_serie_desconhecida_nao_recebe_inferencia():
    """A direcao segura: sem camada declarada, nao se infere campeonato."""
    assert layer_of("UMA_SERIE_NOVA") == LAYER_SERIES_LABEL


@pytest.mark.parametrize("serie", ["WEC", "ALMS", "LEMANSCUP"])
def test_fora_de_REAL_nenhum_sinal_de_campeonato_e_emitido(serie):
    a = sessao(1, serie=serie, carros=["1", "2"], classes=["GTD"])
    b = sessao(2, serie=serie, nome="Practice 1", url=URL_WT,
               carros=["1", "2"], classes=["GTD"])
    r = avaliar([a, b])
    nomes = {s["signal"] for s in r[1]["signals"]}
    assert CHAMPIONSHIP_LAYER_NOT_APPLICABLE in nomes
    assert CHAMPIONSHIP_NEVER_LOADED not in nomes
    assert CAR_SET_IDENTICAL_TO_OTHER_CHAMPIONSHIP not in nomes
    assert CLASS_PROFILE_FOREIGN not in nomes


def test_champeonato_nunca_carregado_so_e_computado_onde_e_medivel():
    """
    No ELMS campeonatos de apoio foram parcialmente carregados, entao o
    criterio nao se aplica la. Nada medido no IMSA e transportado.
    """
    assert NEVER_LOADED_SERIES == ("IMSA",)
    assert "ELMS" not in NEVER_LOADED_SERIES
    assert CHAMPIONSHIP_LAYER["ELMS"] == LAYER_REAL   # camada sim, criterio nao


# ── Os dois digests sao coisas diferentes (ajuste 4) ──────────

def test_mesmo_conjunto_de_carros_nao_e_mesmo_conjunto_de_resultados():
    """
    A distincao que o contrato exige: dois registros de sessao podem ter os
    mesmos carros e resultados completamente diferentes.
    """
    carros = ["7", "9", "10"]
    a = result_content_digest([("7", "GTD", 1, "Classified", 30, "1:00")])
    b = result_content_digest([("7", "GTD", 9, "DNF", 2, "0:10")])
    assert car_set_digest(carros) == car_set_digest(list(reversed(carros)))
    assert a != b


def test_o_digest_de_carros_ignora_repeticao_e_ordem():
    assert car_set_digest(["9", "9", "7"]) == car_set_digest(["7", "9"])
    assert car_set_digest([]) is None


def test_o_sinal_de_conjunto_igual_avisa_que_nao_e_prova():
    a = sessao(1, carros=["1", "2"], classes=["GTD"],
               conteudo="conteudo-A")
    b = sessao(2, nome="Qualifying", url=URL_WT, carros=["1", "2"],
               classes=["GTD"], conteudo="conteudo-B")
    r = avaliar([a, b])
    sig = next(s for s in r[1]["signals"]
               if s["signal"] == CAR_SET_IDENTICAL_TO_OTHER_CHAMPIONSHIP)
    assert sig["evidence"]["digest_usado"] == "car_set_digest"
    assert sig["evidence"]["sessoes"][0]["mesmo_result_content_digest"] is False
    assert "NAO e conjunto de resultados igual" in sig["evidence"]["advertencia"]


# ── A multiplicacao, com a evidencia que o ajuste 5 exige ─────

def linha_de(numero, posicao):
    return (numero, "GTD", posicao, "Classified", 30, "1:00")


def test_multiplicacao_proporcional_e_detectada_e_documentada():
    """
    Positivo: a sessao carrega cada linha tres vezes. O digest sozinho nao
    veria isso — listas de tamanhos diferentes diferem por construcao —,
    e por isso a comparacao e por multiset.
    """
    base = [linha_de("1", 1), linha_de("2", 2)]
    ref = sessao(2, carros=["1", "2"], linhas=base)
    alvo = sessao(1, carros=["1", "2"], resultados=6, linhas=base * 3)
    assert ref.content != alvo.content          # o digest difere, e isso e o ponto
    ev = multiple_of_sibling(alvo, [ref])
    assert ev["comparada_com"] == 2
    assert ev["resultados_desta"] == 6
    assert ev["resultados_da_comparada"] == 2
    assert ev["razao_inteira"] == 3
    assert ev["assinaturas_distintas"] == 2
    assert "MESMO multiplo inteiro" in ev["criterio"]
    assert "multiset" in ev["digest_usado"]
    assert "SELECT" in ev["como_reproduzir"]


def test_o_fator_precisa_ser_o_mesmo_em_todas_as_assinaturas():
    """
    Negativo: uma linha dobra e a outra triplica. Isso nao e uma sessao
    carregada n vezes, e o sinal recusa.
    """
    ref = content_multiset([linha_de("1", 1), linha_de("2", 2)])
    desproporcional = content_multiset(
        [linha_de("1", 1)] * 2 + [linha_de("2", 2)] * 3)
    assert multiplicity_factor(desproporcional, ref) is None


def test_fator_um_nao_e_multiplicacao():
    base = [linha_de("1", 1), linha_de("2", 2)]
    assert multiplicity_factor(content_multiset(base),
                               content_multiset(base)) is None


def test_conteudo_diferente_nao_e_multiplicacao():
    """Negativo: mesmos carros, linhas diferentes."""
    ref = sessao(2, carros=["1", "2"],
                 linhas=[linha_de("1", 1), linha_de("2", 2)])
    alvo = sessao(1, carros=["1", "2"], resultados=4,
                  linhas=[linha_de("1", 9), linha_de("2", 8)] * 2)
    assert multiple_of_sibling(alvo, [ref]) is None


def test_assinaturas_a_mais_nao_sao_multiplicacao():
    """
    Negativo: a candidata tem tudo o que a referencia tem, em dobro, MAIS
    uma linha que a referencia nao tem. Quem repete nao acrescenta.
    """
    ref = content_multiset([linha_de("1", 1)])
    com_extra = content_multiset([linha_de("1", 1)] * 2 + [linha_de("9", 5)])
    assert multiplicity_factor(com_extra, ref) is None


def test_mesma_quantidade_de_carros_nao_basta():
    """
    Negativo: duas sessoes com 2 carros cada e contagens 6 e 2. A razao
    inteira existe, e o conteudo nao coincide.
    """
    ref = sessao(2, carros=["1", "2"],
                 linhas=[linha_de("1", 1), linha_de("2", 2)])
    alvo = sessao(1, carros=["7", "8"], resultados=6,
                  linhas=[linha_de("7", 1), linha_de("8", 2)] * 3)
    assert alvo.results % ref.results == 0      # a quantidade bate...
    assert multiple_of_sibling(alvo, [ref]) is None   # ...e nao basta


def test_o_denominador_precisa_ser_positivo():
    vazio = sessao(2, carros=[], resultados=0)
    alvo = sessao(1, carros=["1"], resultados=6,
                  linhas=[linha_de("1", 1)] * 6)
    assert multiple_of_sibling(alvo, [vazio]) is None
    assert multiplicity_factor({"a": 2}, {}) is None
    assert multiplicity_factor({}, {"a": 1}) is None


def test_o_digest_de_conteudo_nao_responde_a_pergunta_de_multiplicidade():
    """
    O defeito da primeira versao, fixado: exigir digest igual E contagens
    diferentes e pedir o impossivel.
    """
    base = [linha_de("1", 1)]
    um = result_content_digest(base)
    tres = result_content_digest(base * 3)
    assert um != tres
    assert multiplicity_factor(content_multiset(base * 3),
                               content_multiset(base)) == 3


def test_repeticao_dentro_da_sessao_e_observada_e_nao_classifica():
    """
    102 linhas para 34 carros e a triplicacao da PR #57 — outro defeito.
    O modulo mede e reporta, e nao deixa isso mover a classificacao.
    """
    sozinha = sessao(1, nome="Unica", carros=["1"] * 102, resultados=102,
                     classes=["GTD"])
    sozinha.car_numbers = [str(n % 34) for n in range(102)]
    sozinha.car_set = car_set_digest(sozinha.car_numbers)
    r = avaliar([sozinha])
    sig = next(s for s in r[1]["signals"]
               if s["signal"] == CARS_REPEATED_WITHIN_SESSION)
    assert sig["tier"] == OBSERVED
    assert sig["evidence"]["linhas_de_results"] == 102
    assert sig["evidence"]["numeros_de_carro_distintos"] == 34
    assert sig["evidence"]["razao"] == 3
    assert sig["evidence"]["razao_e_inteira"] is True
    assert "PR #57" in sig["meaning"]
    # e nao muda nada: nome unico, nenhum sinal derivado
    assert r[1]["classification"] == NO_NAME_AMBIGUITY


# ── Ajuste 2: observacional sozinho nunca sobe ────────────────

def test_ambiguidade_com_apenas_sinais_observacionais_nao_vira_suspeita():
    """
    Nome repetido e irmao vazio sao duas observacoes sobre nomes e
    contagens. Nomes repetem legitimamente; isso nao e suspeita.
    """
    cheia = sessao(1, carros=["1", "2"], classes=["GTD"], url=URL_WT)
    vazia = sessao(2, carros=[], resultados=0, url=URL_WT)
    r = avaliar([cheia, vazia])
    nomes = {s["signal"] for s in r[1]["signals"]}
    assert AMBIGUOUS_SESSION_NAME in nomes
    assert SIBLING_SAME_NAME_EMPTY in nomes
    assert not {s["signal"] for s in r[1]["signals"]
                if s["tier"] == CORPUS_DERIVED}
    assert r[1]["classification"] == AMBIGUOUS_NAME_ONLY


def test_campeonato_nunca_carregado_sozinho_nao_alcanca_a_coorte():
    """
    Uma unica medicao CORPUS_DERIVED nao e uma coorte. Sem corroboracao,
    cai para inconsistencia de metadado.
    """
    unica = sessao(1, nome="Unica", url=URL_VP, carros=["1", "2"],
                   classes=["GTD"])
    base = sessao(2, nome="Outra", evento=2, url=URL_WT, carros=["5"],
                  classes=["GTD"])
    r = avaliar([unica, base])
    nomes = {s["signal"] for s in r[1]["signals"]}
    assert CHAMPIONSHIP_NEVER_LOADED in nomes
    assert AMBIGUOUS_SESSION_NAME not in nomes
    assert CAR_SET_IDENTICAL_TO_OTHER_CHAMPIONSHIP not in nomes
    assert r[1]["classification"] == PROVENANCE_METADATA_INCONSISTENT


def test_com_corroboracao_alcanca_a_coorte():
    a = sessao(1, url=URL_VP, carros=["1", "2"], classes=["GTD"])
    b = sessao(2, url=URL_WT, carros=[], resultados=0)
    c = sessao(3, nome="Outra", url=URL_WT, carros=["5"], classes=["GTD"])
    r = avaliar([a, b, c])
    assert r[1]["classification"] == STRUCTURAL_INCOMPATIBILITY_COHORT
    assert "NAO afirma que cada resultado" in r[1]["meaning"]


# ── Precedencia e casos-limite ────────────────────────────────

def test_sem_url_vence_tudo():
    a = sessao(1, url=None, carros=["1", "2"], classes=["GTD"])
    b = sessao(2, url=URL_WT, carros=["1", "2"], classes=["GTD"])
    r = avaliar([a, b])
    assert r[1]["classification"] == UNDETERMINED_NO_PROVENANCE
    assert NO_PROVENANCE_URL in {s["signal"] for s in r[1]["signals"]}


def test_sessao_vazia_e_ambigua_fica_em_ambiguous_name_only():
    """Sem conteudo nao ha o que estar no lugar errado."""
    vazia = sessao(1, carros=[], resultados=0, url=URL_VP)
    outra = sessao(2, carros=["1"], classes=["GTD"], url=URL_WT)
    r = avaliar([vazia, outra])
    nomes = {s["signal"] for s in r[1]["signals"]}
    assert SESSION_HAS_NO_RESULTS in nomes
    assert r[1]["classification"] == AMBIGUOUS_NAME_ONLY


def test_nome_unico_diz_apenas_isso():
    a = sessao(1, nome="Unica", url=URL_WT, carros=["1"], classes=["GTD"])
    r = avaliar([a])
    assert r[1]["classification"] == NO_NAME_AMBIGUITY
    assert "NAO comprova proveniencia" in r[1]["meaning"]


def test_nomes_iguais_com_conteudos_distintos_sao_o_caso_normal():
    a = sessao(1, url=URL_WT, carros=["1", "2"], classes=["GTD"],
               conteudo="A")
    b = sessao(2, url=URL_WT, carros=["7", "8"], classes=["GTD"],
               conteudo="B")
    r = avaliar([a, b])
    assert r[1]["classification"] == AMBIGUOUS_NAME_ONLY
    assert "caso normal" in r[1]["meaning"]


def test_classes_incompativeis_com_o_perfil_do_proprio_campeonato():
    """`CLASS_PROFILE_FOREIGN`, e o perfil vem do acervo — por isso a
    evidencia diz que ele pode estar contaminado."""
    # perfil de referencia: sessao de nome unico no evento 2
    ref = sessao(10, evento=2, nome="Unica", serie="ELMS", url=URL_WT,
                 carros=["1"], classes=["LMP2"])
    a = sessao(1, serie="ELMS", url=URL_WT, carros=["3"], classes=["TCR"])
    b = sessao(2, serie="ELMS", url=URL_WT, carros=["4"], classes=["LMP2"])
    r = avaliar([ref, a, b])
    sig = next(s for s in r[1]["signals"] if s["signal"] == CLASS_PROFILE_FOREIGN)
    assert sig["tier"] == CORPUS_DERIVED
    assert sig["evidence"]["fora_do_perfil"] == ["TCR"]
    assert sig["evidence"]["perfil_derivado_do_acervo"] is True


def test_perfis_indistinguiveis_sao_sinalizados():
    wt = sessao(10, evento=2, nome="A", url=URL_WT, carros=["1"], classes=["GTD"])
    vp = sessao(11, evento=2, nome="B", url=URL_VP, carros=["2"], classes=["GTD"])
    r = avaliar([wt, vp])
    nomes = {s["signal"] for s in r[10]["signals"]}
    assert CHAMPIONSHIP_PROFILES_INDISTINGUISHABLE in nomes


# ── As tres camadas, e a terceira vazia (ajustes 1 e 3) ───────

def test_evidencia_independente_e_sempre_vazia_e_declarada():
    a = sessao(1, url=URL_VP, carros=["1"], classes=["GTD"])
    r = avaliar([a])
    assert r[1]["independent_evidence"] == []
    assert "nenhuma existe hoje" in TIER_MEANING[INDEPENDENT]
    assert "ingest_log vazia" in TIER_MEANING[INDEPENDENT]


def test_sinais_derivados_declaram_que_a_corroboracao_e_interna():
    a = sessao(1, url=URL_VP, carros=["1", "2"], classes=["GTD"])
    b = sessao(2, url=URL_WT, carros=[], resultados=0)
    c = sessao(3, nome="Outra", url=URL_WT, carros=["5"], classes=["GTD"])
    r = avaliar([a, b, c])
    derivados = [s for s in r[1]["signals"] if s["tier"] == CORPUS_DERIVED]
    assert derivados
    for s in derivados:
        assert s["corroboracao_interna"] is True
    for s in (s for s in r[1]["signals"] if s["tier"] == OBSERVED):
        assert s["corroboracao_interna"] is False
    assert "correlacionada" in TIER_MEANING[CORPUS_DERIVED]


def test_nenhuma_combinacao_de_derivados_vira_evidencia_independente():
    a = sessao(1, url=URL_VP, carros=["1", "2"], classes=["GTD"])
    b = sessao(2, nome="Qualifying", url=URL_WT, carros=["1", "2"],
               classes=["GTD"])
    c = sessao(3, nome="Outra", url=URL_WT, carros=["5"], classes=["GTD"])
    r = avaliar([a, b, c])
    derivados = [s for s in r[1]["signals"] if s["tier"] == CORPUS_DERIVED]
    assert len(derivados) >= 2, [s["signal"] for s in derivados]
    assert r[1]["independent_evidence"] == []


# ── Toda classificacao recusa alguma coisa ────────────────────

def test_cada_classificacao_declara_o_que_nao_afirma():
    assert set(CLASSIFICATION_MEANING) == set(CLASSIFICATIONS)
    assert "NAO afirma" in CLASSIFICATION_MEANING[STRUCTURAL_INCOMPATIBILITY_COHORT]
    assert "NADA aqui diz qual" in CLASSIFICATION_MEANING[PROVENANCE_METADATA_INCONSISTENT]
    assert "SOMENTE" in CLASSIFICATION_MEANING[AMBIGUOUS_NAME_ONLY]
    assert "NAO comprova" in CLASSIFICATION_MEANING[NO_NAME_AMBIGUITY]


def test_o_relatorio_recusa_tratar_a_coorte_como_recuperavel():
    a = sessao(1, url=URL_VP, carros=["1"], classes=["GTD"])
    r = avaliar([a])
    texto = r[1]["o_que_isto_nao_e"]
    assert "NAO sao registros recuperaveis" in texto
    assert "varias cargas" in texto
    assert "nao prova resultados duplicados" in texto
    assert "Exposicao e propriedade da sessao" in texto


# ── O limite de evidencias nao move nada ──────────────────────

def test_o_limite_de_evidencias_nao_muda_a_classificacao():
    sessoes = [sessao(1, url=URL_VP, carros=["1", "2"], classes=["GTD"]),
               sessao(2, url=URL_WT, carros=[], resultados=0),
               sessao(3, nome="Outra", url=URL_WT, carros=["5"], classes=["GTD"])]
    completo = assess_sessions(sessoes, 0)
    cortado = assess_sessions(sessoes, 1)
    assert [a.classification for a in completo] == [a.classification for a in cortado]
    assert len(cortado[0].signals) == 1
    assert len(completo[0].signals) > 1


# ── Leitura ───────────────────────────────────────────────────

def test_read_sessions_monta_os_dois_digests_e_as_contagens():
    cur = CursorFalso([
        [(1, 10, "Practice 1", "r1", "2024-07-12", URL_VP,
          "Canadian Tire", "2024", "IMSA")],
        [(1, "9", "GTDPRO", 1, "Classified", 30, "1:00"),
         (1, "9", "GTDPRO", 1, "Classified", 30, "1:00"),
         (1, "7", "GTD", 2, "Classified", 29, "1:01")],
        [(1, 42)],
    ])
    (s,) = read_sessions(cur)
    assert s.results == 3
    assert s.laps == 42
    assert s.classes == ["GTD", "GTDPRO"]
    assert s.car_set == car_set_digest(["7", "9"])
    assert s.content is not None and s.content != s.car_set
    assert s.championship == "IMSA VP Racing SportsCar Challenge"
    assert s.layer == LAYER_REAL


def test_catalogo_vazio_falha_fechado():
    with pytest.raises(SessionAssociationError, match="nenhuma sessao"):
        read_sessions(CursorFalso([[]]))


def test_nenhuma_instrucao_de_escrita_e_emitida():
    import re

    cur = CursorFalso([
        [(1, 10, "P1", "r1", "2024-07-12", URL_VP, "E", "2024", "IMSA")],
        [(1, "9", "GTD", 1, "Classified", 30, "1:00")],
        [(1, 1)],
    ])
    read_sessions(cur)
    texto = cur.sql.upper()
    for proibida in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "ALTER",
                     "DROP", "CREATE", "GRANT", "COMMIT"):
        assert not re.search(rf"\b{proibida}\b", texto), proibida
    for sql, _ in cur.executados:
        assert sql.strip().upper().startswith("SELECT"), sql


def test_relatorio_em_destino_invalido_nao_derruba_a_leitura(tmp_path):
    assert write_report({"sessoes": 1}, tmp_path / "sem-pasta" / "r.json") == 1
    assert write_report({"sessoes": 1}, None) == 0
    bom = tmp_path / "r.json"
    assert write_report({"sessoes": 1}, bom) == 0
    assert bom.read_text(encoding="utf-8").strip().startswith("{")


# ── Nao interferencia com as PRs #57-#62 ─────────────────────

def test_o_modulo_nao_alcanca_as_avaliacoes_anteriores():
    import ast

    import database.recovery.session_association as mod

    arvore = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
    importados = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            importados.update(a.name for a in no.names)
        elif isinstance(no, ast.ImportFrom):
            importados.add(no.module or "")
    for proibido in ("triplication", "carriers", "driver_identity",
                     "merge_drivers", "normalize_drivers", "subprocess", "os"):
        assert not any(proibido in nome for nome in importados), proibido
    chamadas = {no.func.id for no in ast.walk(arvore)
                if isinstance(no, ast.Call) and isinstance(no.func, ast.Name)}
    assert not {"exec", "eval", "__import__"} & chamadas


def test_o_perfil_de_referencia_so_usa_sessoes_de_nome_unico():
    """A unica referencia disponivel, e documentadamente ma: se todas as
    sessoes de um campeonato forem ambiguas, ele nao tem perfil."""
    a = sessao(1, url=URL_WT, carros=["1"], classes=["GTD"])
    b = sessao(2, url=URL_WT, carros=["2"], classes=["LMP2"])
    perfis = class_profiles([a, b], ambiguos={1, 2})
    assert perfis == {}
    perfis = class_profiles([a, b], ambiguos={2})
    assert perfis == {("IMSA", "IMSA WeatherTech SportsCar Championship"): ["GTD"]}


def test_classify_e_uma_funcao_pura_dos_sinais():
    s = sessao(1, url=URL_VP, carros=["1"], classes=["GTD"])
    assert classify(s, []) == NO_NAME_AMBIGUITY
    assert classify(s, signals_for(s, {1: [s]}, {}, {}, {})) == NO_NAME_AMBIGUITY


def test_a_ressalva_nunca_corrobora_nem_classifica():
    """
    `CHAMPIONSHIP_PROFILES_INDISTINGUISHABLE` diz que o perfil NAO
    discrimina ali. Deixa-la corroborar faria a limitacao sustentar a
    conclusao que ela limita.
    """
    from database.recovery.session_association import CAVEAT_SIGNALS

    assert CHAMPIONSHIP_PROFILES_INDISTINGUISHABLE in CAVEAT_SIGNALS
    wt = sessao(10, evento=2, nome="A", url=URL_WT, carros=["1"], classes=["GTD"])
    vp = sessao(11, evento=2, nome="B", url=URL_VP, carros=["2"], classes=["GTD"])
    r = avaliar([wt, vp])
    # o sinal e emitido...
    assert CHAMPIONSHIP_PROFILES_INDISTINGUISHABLE in {
        s["signal"] for s in r[10]["signals"]}
    # ...e sozinho nao leva a lugar nenhum
    assert r[10]["classification"] == NO_NAME_AMBIGUITY


def test_a_ressalva_nao_completa_a_coorte():
    """Com apenas CHAMPIONSHIP_NEVER_LOADED mais a ressalva, nao ha coorte."""
    unica = sessao(1, nome="Unica", url=URL_VP, carros=["1"], classes=["GTD"])
    base = sessao(2, nome="Outra", url=URL_WT, carros=["5"], classes=["GTD"])
    r = avaliar([unica, base])
    nomes = {s["signal"] for s in r[1]["signals"]}
    assert CHAMPIONSHIP_NEVER_LOADED in nomes
    assert CHAMPIONSHIP_PROFILES_INDISTINGUISHABLE in nomes
    assert r[1]["classification"] == PROVENANCE_METADATA_INCONSISTENT


# ── O limite e apresentacao, nunca contagem ──────────────────

def test_os_totais_globais_de_sinais_nao_dependem_do_limite():
    """
    A regressao que isto fixa: `assess_all` somava os sinais DEPOIS do
    truncamento, de modo que `--evidence-limit 1` reduzia um numero global.
    O limite e apresentacao; contar depois de truncar o tornava semantico.
    """
    from database.recovery.session_association import DEFAULT_EVIDENCE_LIMIT

    cur_args = [
        [(1, 10, "Practice 1", "r1", "2024-07-12", URL_VP, "E", "2024", "IMSA"),
         (2, 10, "Practice 1", "r2", "2024-07-12", URL_WT, "E", "2024", "IMSA"),
         (3, 10, "Outra", "r3", "2024-07-12", URL_WT, "E", "2024", "IMSA")],
        [(1, "9", "GTD", 1, "Classified", 30, "1:00"),
         (1, "9", "GTD", 1, "Classified", 30, "1:00"),
         (1, "7", "GTD", 2, "Classified", 29, "1:01"),
         (3, "5", "GTD", 1, "Classified", 30, "1:00")],
        [(1, 10)],
    ]

    def correr(limite):
        from database.recovery.session_association import assess_all
        return assess_all(CursorFalso([list(x) for x in cur_args]), limite)

    zero = correr(0)
    um = correr(1)
    padrao = correr(DEFAULT_EVIDENCE_LIMIT)

    assert zero["sinais"] == um["sinais"] == padrao["sinais"]
    assert zero["classificacoes"] == um["classificacoes"] == padrao["classificacoes"]
    assert (zero["resultados_por_classificacao"]
            == um["resultados_por_classificacao"]
            == padrao["resultados_por_classificacao"])
    assert (zero["voltas_por_classificacao"]
            == um["voltas_por_classificacao"]
            == padrao["voltas_por_classificacao"])
    # e o truncamento de fato aconteceu em algum lugar
    assert any(a["signals_meta"]["truncado"] for a in um["sessions"])
    assert not any(a["signals_meta"]["truncado"] for a in zero["sessions"])


def test_os_metadados_de_truncamento_dizem_quantos_havia():
    sessoes = [sessao(1, url=URL_VP, carros=["1", "2"], classes=["GTD"]),
               sessao(2, url=URL_WT, carros=[], resultados=0),
               sessao(3, nome="Outra", url=URL_WT, carros=["5"], classes=["GTD"])]
    (a, _, _) = assess_sessions(sessoes, 1)
    assert a.signals_meta["total"] > 1
    assert a.signals_meta["exibidos"] == 1
    assert a.signals_meta["truncado"] is True
    assert a.signals_meta["limite"] == 1
    assert len(a.signal_names) == a.signals_meta["total"]
    assert "--evidence-limit 0" in a.signals_meta["como_ver_o_resto"]
