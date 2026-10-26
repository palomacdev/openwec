"""
OpenWEC — is this result hanging off the session it came from?

A race weekend publishes several championships. Each has its own "Practice
2", and before PR #41 the loader resolved a session with

    WHERE sr.key = %s AND se.raw_id = %s AND e.raw_id = %s AND s.name = %s
    LIMIT 1

— the bare session name, `LIMIT 1`, no `ORDER BY`. Where four championships
share a name, that returns an arbitrary one.

This module reports what the acquired data looks like under that history.
**It moves nothing.** No result, no lap, no row is repointed, and no
session is declared to be the right home for anything.

## Championship is not a relation

The schema is `series → seasons → events → sessions → results`. There is no
`championships` table and no championship column anywhere: the championship
exists **only as text inside `sessions.source_url`**, in the seventh path
segment. So no constraint can keep a result out of another championship's
session — the database does not know what a championship is.

What the schema does keep is `UNIQUE (event_id, raw_id)` on `sessions`, and
`raw_id` carries the start time (`202407120835_Practice 1`). Sessions of
different championships therefore do **not** collapse. What goes wrong is
what gets attached to them.

## Three tiers of evidence, and the third is empty

Every signal declares how it was obtained, because they are not worth the
same:

  `OBSERVED`        read from the catalogue. No inference.
  `CORPUS_DERIVED`  computed from the corpus itself, **which may already
                    carry the defect being looked for**. Measured: a class
                    profile built from this corpus returns 2 suspicious
                    IMSA sessions where the structure shows 516, because
                    the reference profile is itself contaminated.
  `INDEPENDENT`     evidence from outside the corpus. **There is none.**

The third tier is in the contract, named and empty, because `ingest_log`
holds 0 rows, `results.source_url` is populated on 0 of 668.273 rows, and
`raw/imsa/` does not contain the championships in question. Every entry
carries `independent_evidence: []` so the absence is a field rather than a
footnote.

**`CORPUS_DERIVED` signals corroborate each other only internally, and they
are likely correlated** — the same ingestion history produces all of them.
No combination of them becomes independent evidence.

## Two digests, deliberately apart

`car_set_digest` is over the set of car **numbers**. `result_content_digest`
is over the result rows themselves. They answer different questions, and
collapsing them is how "the same cars appear" gets reported as "the same
results were loaded twice". Equality of car sets is **never** treated as
proof of duplicated results.

## Read only

Every statement is a `SELECT`. The CLI opens its connection with
`default_transaction_read_only=on`, and the report is written after the
connection is closed, to a path the caller names.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path

# ── Evidence tiers ───────────────────────────────────────────

OBSERVED = "OBSERVED"
CORPUS_DERIVED = "CORPUS_DERIVED"
INDEPENDENT = "INDEPENDENT"

TIERS = (OBSERVED, CORPUS_DERIVED, INDEPENDENT)

TIER_MEANING = {
    OBSERVED: "lido do catalogo; nenhuma inferencia",
    CORPUS_DERIVED: ("computado do proprio acervo, que pode ja carregar o "
                     "defeito procurado; corroboracao INTERNA e "
                     "provavelmente correlacionada entre si"),
    INDEPENDENT: ("evidencia de fora do acervo; nenhuma existe hoje "
                  "(ingest_log vazia, results.source_url em 0 de 668.273, "
                  "raw/imsa sem os campeonatos em questao)"),
}

# ── Signals ──────────────────────────────────────────────────

AMBIGUOUS_SESSION_NAME = "AMBIGUOUS_SESSION_NAME"
SIBLING_SAME_NAME_EMPTY = "SIBLING_SAME_NAME_EMPTY"
SESSION_HAS_NO_RESULTS = "SESSION_HAS_NO_RESULTS"
NO_PROVENANCE_URL = "NO_PROVENANCE_URL"
CARS_REPEATED_WITHIN_SESSION = "CARS_REPEATED_WITHIN_SESSION"

CLASS_PROFILE_FOREIGN = "CLASS_PROFILE_FOREIGN"
CHAMPIONSHIP_NEVER_LOADED = "CHAMPIONSHIP_NEVER_LOADED"
CAR_SET_IDENTICAL_TO_OTHER_CHAMPIONSHIP = "CAR_SET_IDENTICAL_TO_OTHER_CHAMPIONSHIP"
CONTENT_MULTIPLE_OF_SIBLING = "CONTENT_MULTIPLE_OF_SIBLING"
CHAMPIONSHIP_PROFILES_INDISTINGUISHABLE = "CHAMPIONSHIP_PROFILES_INDISTINGUISHABLE"

CHAMPIONSHIP_LAYER_NOT_APPLICABLE = "CHAMPIONSHIP_LAYER_NOT_APPLICABLE"

SIGNAL_TIER = {
    AMBIGUOUS_SESSION_NAME: OBSERVED,
    SIBLING_SAME_NAME_EMPTY: OBSERVED,
    SESSION_HAS_NO_RESULTS: OBSERVED,
    NO_PROVENANCE_URL: OBSERVED,
    CARS_REPEATED_WITHIN_SESSION: OBSERVED,
    CHAMPIONSHIP_LAYER_NOT_APPLICABLE: OBSERVED,
    CLASS_PROFILE_FOREIGN: CORPUS_DERIVED,
    CHAMPIONSHIP_NEVER_LOADED: CORPUS_DERIVED,
    CAR_SET_IDENTICAL_TO_OTHER_CHAMPIONSHIP: CORPUS_DERIVED,
    CONTENT_MULTIPLE_OF_SIBLING: CORPUS_DERIVED,
    CHAMPIONSHIP_PROFILES_INDISTINGUISHABLE: CORPUS_DERIVED,
}

# Sinais que sao RESSALVA, nao evidencia: reportados sempre, e nunca
# usados para classificar nem para corroborar.
CAVEAT_SIGNALS = frozenset({CHAMPIONSHIP_PROFILES_INDISTINGUISHABLE})

SIGNAL_MEANING = {
    AMBIGUOUS_SESSION_NAME: ("outra sessao do mesmo evento tem o mesmo nome; "
                             "e a condicao que o LIMIT 1 nao conseguia "
                             "desambiguar"),
    SIBLING_SAME_NAME_EMPTY: ("uma sessao de mesmo nome no mesmo evento nao "
                              "tem resultado algum"),
    SESSION_HAS_NO_RESULTS: "esta sessao nao tem resultado algum",
    NO_PROVENANCE_URL: "a sessao nao registra de que arquivo veio",
    CARS_REPEATED_WITHIN_SESSION: (
        "um numero de carro aparece em mais de uma linha de results "
        "desta sessao. `UNIQUE (session_id, car_id)` permite isso, "
        "porque os car_id sao distintos. E a classe de triplicacao da "
        "PR #57, que e outro defeito: NAO entra na decisao de "
        "classificacao deste modulo"),
    CHAMPIONSHIP_LAYER_NOT_APPLICABLE: ("nesta serie o setimo segmento da URL "
                                        "nomeia a propria serie, nao um "
                                        "campeonato"),
    CLASS_PROFILE_FOREIGN: ("as classes desta sessao nao cabem no perfil do "
                            "seu proprio campeonato; o perfil vem do acervo "
                            "e pode estar contaminado"),
    CHAMPIONSHIP_NEVER_LOADED: ("nenhuma classe exclusiva deste campeonato "
                                "existe no acervo, e ainda assim a sessao "
                                "tem conteudo"),
    CAR_SET_IDENTICAL_TO_OTHER_CHAMPIONSHIP: (
        "o CONJUNTO DE NUMEROS DE CARRO coincide com o de uma sessao de "
        "outro campeonato no mesmo evento. NAO e prova de que os resultados "
        "sao os mesmos: para isso existe result_content_digest"),
    CONTENT_MULTIPLE_OF_SIBLING: (
        "a contagem de resultados e multiplo inteiro da de uma sessao de "
        "mesmo nome com o mesmo conteudo. Compativel com varias cargas "
        "sobre a mesma linha, e tambem com coincidencia de tamanho"),
    CHAMPIONSHIP_PROFILES_INDISTINGUISHABLE: (
        "o perfil de classes do proprio campeonato e identico ao de outro: "
        "ali o perfil nao serve como discriminador"),
}

# ── Championship layer, measured per series ──────────────────

LAYER_REAL = "REAL"
LAYER_MIXED = "MIXED"
LAYER_SERIES_LABEL = "SERIES_LABEL"

# Which series the championship inference is allowed for, and why.
#
# This is a decision, not a measurement, and it is written here so that it
# can be argued with. `REAL` means the seventh path segment names genuinely
# different championships at one event; `SERIES_LABEL` means it names the
# series itself; `MIXED` means it does both and has not been investigated.
#
# Measured labels, after stripping the numeric prefix: IMSA 48, ELMS 49,
# WEC 9 (all variants of "FIA WEC" plus Rookie Test / Prologue / Test Day),
# LEMANSCUP 5 (two real series plus test labels), ALMS 1.
CHAMPIONSHIP_LAYER = {
    "IMSA": LAYER_REAL,
    "ELMS": LAYER_REAL,
    "LEMANSCUP": LAYER_MIXED,
    "WEC": LAYER_SERIES_LABEL,
    "ALMS": LAYER_SERIES_LABEL,
}

LAYER_MEANING = {
    LAYER_REAL: "o setimo segmento nomeia campeonatos distintos no evento",
    LAYER_MIXED: ("nomeia series reais e rotulos de teste ao mesmo tempo; "
                  "sem inferencia automatica ate investigacao propria"),
    LAYER_SERIES_LABEL: "nomeia a propria serie; nao ha o que inferir",
}

# `CHAMPIONSHIP_NEVER_LOADED` is computed only where it is measurable.
#
# In IMSA it is: no class of any non-WeatherTech championship exists in the
# corpus at all. In ELMS it is **not**, because support championships were
# partially loaded — `Mitjet Italia` carries FX1 and FX2 as well as LMP2 and
# LMP3. Nothing measured in IMSA is carried over to ELMS.
NEVER_LOADED_SERIES = ("IMSA",)
NEVER_LOADED_BASELINE = {"IMSA": "WeatherTech"}

# ── Classifications ──────────────────────────────────────────

UNDETERMINED_NO_PROVENANCE = "UNDETERMINED_NO_PROVENANCE"
STRUCTURAL_INCOMPATIBILITY_COHORT = "STRUCTURAL_INCOMPATIBILITY_COHORT"
ASSOCIATION_SUSPECT = "ASSOCIATION_SUSPECT"
PROVENANCE_METADATA_INCONSISTENT = "PROVENANCE_METADATA_INCONSISTENT"
AMBIGUOUS_NAME_ONLY = "AMBIGUOUS_NAME_ONLY"
NO_NAME_AMBIGUITY = "NO_NAME_AMBIGUITY"

CLASSIFICATIONS = (UNDETERMINED_NO_PROVENANCE, STRUCTURAL_INCOMPATIBILITY_COHORT,
                   ASSOCIATION_SUSPECT, PROVENANCE_METADATA_INCONSISTENT,
                   AMBIGUOUS_NAME_ONLY, NO_NAME_AMBIGUITY)

CLASSIFICATION_MEANING = {
    UNDETERMINED_NO_PROVENANCE: (
        "a sessao nao registra origem; nada pode ser confirmado nem "
        "refutado sobre ela"),
    STRUCTURAL_INCOMPATIBILITY_COHORT: (
        "COORTE de incompatibilidade estrutural de alta confianca. NAO "
        "afirma que cada resultado foi comprovadamente associado a sessao "
        "errada: a origem individual nao existe, e uma sessao pode conter "
        "dados de varias cargas"),
    ASSOCIATION_SUSPECT: (
        "ha o que investigar; nada esta demonstrado"),
    PROVENANCE_METADATA_INCONSISTENT: (
        "algo discorda entre metadado e conteudo, e NADA aqui diz qual dos "
        "dois lados esta errado"),
    AMBIGUOUS_NAME_ONLY: (
        "SOMENTE que o nome e ambiguo no evento. Nao e confirmacao e nao e "
        "suspeita: nomes iguais com conteudos distintos sao o caso normal"),
    NO_NAME_AMBIGUITY: (
        "APENAS ausencia de ambiguidade nominal: a selecao por nome nao "
        "podia errar. NAO comprova proveniencia, porque nenhum resultado "
        "aponta para um arquivo"),
}


class SessionAssociationError(Exception):
    """A read failed. Nothing is written, and no session is classified."""


# ── Parsing the championship out of the URL ──────────────────

CHAMPIONSHIP_SEGMENT = 6          # zero-based index of the seventh segment
NUMERIC_PREFIX = re.compile(r"^[0-9]+_")


def championship_of(source_url: str | None) -> str | None:
    """
    The championship label, as the publisher's path writes it.

    The numeric prefix is stripped because it varies by event — the same
    championship is `01_IMSA WeatherTech…` at one round and `21_IMSA
    WeatherTech…` at another. Nothing else is folded: two labels that
    differ in their letters stay different labels.
    """
    if not source_url:
        return None
    partes = source_url.split("/")
    if len(partes) <= CHAMPIONSHIP_SEGMENT:
        return None
    bruto = partes[CHAMPIONSHIP_SEGMENT].replace("%20", " ").strip()
    if not bruto:
        return None
    return NUMERIC_PREFIX.sub("", bruto) or None


def layer_of(series: str) -> str:
    """Which championship layer this series has. Unknown series get no
    inference, which is the safe direction."""
    return CHAMPIONSHIP_LAYER.get(series, LAYER_SERIES_LABEL)


# ── Digests: two of them, and they answer different questions ─

def digest(valores: list) -> str | None:
    """A stable digest over an ordered list. None for an empty list."""
    if not valores:
        return None
    texto = "\x1f".join("" if v is None else str(v) for v in valores)
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()[:32]


def car_set_digest(numeros: list[str]) -> str | None:
    """
    Over the **set** of car numbers, sorted and deduplicated.

    This says which cars appear. It says nothing about the result rows, and
    it is never read as evidence that results were duplicated.
    """
    return digest(sorted({n for n in numeros if n is not None}))


def result_content_digest(linhas: list[tuple]) -> str | None:
    """
    Over the result rows themselves, sorted.

    Two sessions with this digest equal hold the same results, not merely
    the same cars. **It answers "are these the same rows", and it cannot
    answer "are these the same rows repeated".** Lists of different lengths
    produce different digests by construction, so asking for an equal digest
    *and* different counts asks for something impossible — see
    `multiplicity_factor`, which is what the multiplication question needs.
    """
    return digest(sorted(row_signature(linha) for linha in linhas))


def row_signature(linha) -> str:
    """One result row, as the string the multiplicity comparison counts."""
    return "\x1e".join("" if v is None else str(v) for v in linha)


def content_multiset(linhas: list[tuple]) -> dict[str, int]:
    """
    How many times each row signature occurs.

    The multiset is what a multiplication question is actually about: a
    session loaded three times holds every row three times, and that is
    invisible to a digest over the sorted list, which simply differs.
    """
    contagem: dict[str, int] = {}
    for linha in linhas:
        assinatura = row_signature(linha)
        contagem[assinatura] = contagem.get(assinatura, 0) + 1
    return contagem


def multiplicity_factor(candidata: dict[str, int],
                        referencia: dict[str, int]) -> int | None:
    """
    The integer factor by which one session repeats another, or None.

    A real multiplication requires **all** of:

      · both multisets are non-empty, and the reference counts are positive
      · the two hold **the same row signatures** — a session that repeats
        another holds nothing it did not hold
      · every signature's count is the **same** integer multiple of the
        reference's
      · that factor is greater than 1

    Any of those failing is not a multiplication. A session that merely has
    more rows, or the same number of cars, or some signatures in common,
    returns None.
    """
    if not candidata or not referencia:
        return None
    if set(candidata) != set(referencia):
        return None
    fator: int | None = None
    for assinatura, base in referencia.items():
        if base <= 0:                       # denominador positivo
            return None
        n = candidata[assinatura]
        if n % base:
            return None
        atual = n // base
        if fator is None:
            fator = atual
        elif atual != fator:                # as frequencias nao sao proporcionais
            return None
    if fator is None or fator <= 1:
        return None
    return fator


# ── Reading ──────────────────────────────────────────────────

SESSIONS_SQL = """
SELECT s.id, s.event_id, s.name, s.raw_id, s.session_at::text, s.source_url,
       ev.name, se.label, sr.key::text
  FROM sessions s
  JOIN events ev ON ev.id = s.event_id
  JOIN seasons se ON se.id = ev.season_id
  JOIN series sr ON sr.id = se.series_id
 ORDER BY s.id
"""

CONTENT_SQL = """
SELECT r.session_id, c.number, c.car_class,
       r.position, r.status::text, r.laps_completed, r.total_time_raw
  FROM results r JOIN cars c ON c.id = r.car_id
 ORDER BY r.session_id, c.number, r.id
"""

LAPS_SQL = "SELECT session_id, count(*) FROM laps GROUP BY session_id"


@dataclass
class SessionRow:
    session_id: int
    event_id: int
    name: str
    raw_id: str | None
    session_at: str | None
    source_url: str | None
    event_name: str | None
    season: str | None
    series: str | None
    championship: str | None = None
    layer: str = LAYER_SERIES_LABEL
    results: int = 0
    laps: int = 0
    car_numbers: list = dataclass_field(default_factory=list)
    classes: list = dataclass_field(default_factory=list)
    car_set: str | None = None
    content: str | None = None
    content_multiset: dict = dataclass_field(default_factory=dict)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def read_sessions(cur) -> list[SessionRow]:
    """Every session with its context, its content digests and its counts."""
    cur.execute(SESSIONS_SQL)
    linhas = cur.fetchall()
    if not linhas:
        raise SessionAssociationError("nenhuma sessao foi lida")
    sessoes = {}
    for sid, eid, nome, raw, quando, url, ev, se, sr in linhas:
        s = SessionRow(session_id=sid, event_id=eid, name=nome, raw_id=raw,
                       session_at=quando, source_url=url, event_name=ev,
                       season=se, series=sr)
        s.championship = championship_of(url)
        s.layer = layer_of(sr)
        sessoes[sid] = s

    cur.execute(CONTENT_SQL)
    por_sessao: dict[int, list] = {}
    for sid, numero, classe, pos, status, voltas, tempo in cur.fetchall():
        por_sessao.setdefault(sid, []).append(
            (numero, classe, pos, status, voltas, tempo))
    for sid, conteudo in por_sessao.items():
        s = sessoes.get(sid)
        if s is None:
            continue
        s.results = len(conteudo)
        s.car_numbers = [linha[0] for linha in conteudo]
        s.classes = sorted({linha[1] for linha in conteudo if linha[1]})
        s.car_set = car_set_digest(s.car_numbers)
        s.content = result_content_digest(conteudo)
        s.content_multiset = content_multiset(conteudo)

    cur.execute(LAPS_SQL)
    for sid, n in cur.fetchall():
        if sid in sessoes:
            sessoes[sid].laps = n
    return [sessoes[k] for k in sorted(sessoes)]


# ── Reference profiles, and why they can be wrong ────────────

def class_profiles(sessoes: list[SessionRow], ambiguos: set[int]) -> dict:
    """
    The classes each championship shows, taken from sessions whose name is
    unique in their event.

    Those are the sessions the `LIMIT 1` could not have misplaced, so they
    are the least bad reference available. **They are not a good one**: if
    every session of a championship is ambiguous, or if the unambiguous ones
    were themselves fed foreign content, the profile inherits it. Measured:
    the Michelin Pilot profile built this way is identical to the
    WeatherTech one, which is why `CLASS_PROFILE_FOREIGN` finds 2 IMSA
    sessions where the structural signal finds 516.
    """
    perfis: dict[tuple, set] = {}
    for s in sessoes:
        if s.session_id in ambiguos or not s.championship or not s.classes:
            continue
        perfis.setdefault((s.series, s.championship), set()).update(s.classes)
    return {k: sorted(v) for k, v in perfis.items()}


def never_loaded_championships(sessoes: list[SessionRow]) -> dict:
    """
    Championships of which **no class at all** exists in the corpus.

    Computed only for the series in `NEVER_LOADED_SERIES`, and only against
    that series' baseline championship. In IMSA this is measurable: every
    class present belongs to WeatherTech. In ELMS it is not, and it is
    therefore not computed there.
    """
    achados = {}
    for serie in NEVER_LOADED_SERIES:
        base = NEVER_LOADED_BASELINE[serie]
        da_serie = [s for s in sessoes if s.series == serie and s.championship]
        base_classes: set = set()
        outras: dict[str, set] = {}
        for s in da_serie:
            if base.lower() in s.championship.lower():
                base_classes.update(s.classes)
            else:
                outras.setdefault(s.championship, set()).update(s.classes)
        for camp, classes in outras.items():
            exclusivas = classes - base_classes
            if not exclusivas:
                achados[(serie, camp)] = {
                    "classes_vistas": sorted(classes),
                    "exclusivas_do_campeonato": [],
                    "baseline": base,
                }
    return achados


# ── Signals ──────────────────────────────────────────────────

@dataclass
class Signal:
    name: str
    tier: str
    evidence: dict = dataclass_field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"signal": self.name, "tier": self.tier,
                "meaning": SIGNAL_MEANING.get(self.name, ""),
                "corroboracao_interna": self.tier == CORPUS_DERIVED,
                "evidence": self.evidence}


def multiple_of_sibling(s: SessionRow, irmaos: list[SessionRow]) -> dict | None:
    """
    Whether this session repeats a same-name sibling, proportionally.

    **The first version of this asked for something impossible.** It
    required `result_content_digest` to be equal *and* the counts to
    differ — and that digest is over the whole sorted list, so lists of
    different lengths differ by construction. The two conditions could
    never both hold, and the zero it reported demonstrated nothing.

    The question is about **multiplicities**, so it is asked of the row
    multiset: the same signatures, every one of them the same integer
    multiple, factor above 1. `multiplicity_factor` holds that rule.

    The evidence records the compared session, both counts, the integer
    factor, how many distinct signatures were compared and the query that
    reproduces it.
    """
    if not s.results or not s.content_multiset:
        return None
    for outro in irmaos:
        if outro.session_id == s.session_id:
            continue
        if outro.results <= 0:                      # denominador positivo
            continue
        fator = multiplicity_factor(s.content_multiset, outro.content_multiset)
        if fator is None:
            continue
        return {
            "comparada_com": outro.session_id,
            "resultados_desta": s.results,
            "resultados_da_comparada": outro.results,
            "razao_inteira": fator,
            "assinaturas_distintas": len(s.content_multiset),
            "criterio": ("todas as frequencias desta sessao sao o MESMO "
                         "multiplo inteiro das da comparada, e o conjunto "
                         "de assinaturas e identico"),
            "digest_usado": "multiset de assinaturas de linha",
            "digest": s.content,
            "como_reproduzir": (
                f"SELECT session_id, count(*) FROM results WHERE session_id "
                f"IN ({s.session_id}, {outro.session_id}) GROUP BY 1"),
        }
    return None


def signals_for(s: SessionRow, por_evento: dict, perfis: dict,
                nunca_carregados: dict, perfis_iguais: dict) -> list[Signal]:
    """Every signal this session raises, each with reproducible evidence."""
    sinais: list[Signal] = []
    mesmo_nome = [o for o in por_evento.get(s.event_id, [])
                  if o.name == s.name and o.session_id != s.session_id]

    if s.source_url is None:
        sinais.append(Signal(NO_PROVENANCE_URL, OBSERVED, {
            "como_reproduzir": (f"SELECT source_url FROM sessions WHERE id = "
                                f"{s.session_id}")}))
    if s.layer != LAYER_REAL:
        sinais.append(Signal(CHAMPIONSHIP_LAYER_NOT_APPLICABLE, OBSERVED, {
            "serie": s.series, "camada": s.layer,
            "motivo": LAYER_MEANING[s.layer]}))
    if not s.results:
        sinais.append(Signal(SESSION_HAS_NO_RESULTS, OBSERVED, {
            "como_reproduzir": (f"SELECT count(*) FROM results WHERE "
                                f"session_id = {s.session_id}")}))
    distintos = len({n for n in s.car_numbers})
    if s.results and distintos and s.results > distintos:
        sinais.append(Signal(CARS_REPEATED_WITHIN_SESSION, OBSERVED, {
            "linhas_de_results": s.results,
            "numeros_de_carro_distintos": distintos,
            "razao": (s.results / distintos if s.results % distintos
                      else s.results // distintos),
            "razao_e_inteira": s.results % distintos == 0,
            "como_reproduzir": (
                f"SELECT count(*) AS linhas, count(DISTINCT c.number) AS carros "
                f"FROM results r JOIN cars c ON c.id = r.car_id "
                f"WHERE r.session_id = {s.session_id}"),
            "nota": ("defeito distinto do de associacao; medido e reportado, "
                     "nao usado para classificar"),
        }))

    if mesmo_nome:
        sinais.append(Signal(AMBIGUOUS_SESSION_NAME, OBSERVED, {
            "irmaos": [{"session_id": o.session_id, "raw_id": o.raw_id,
                        "championship": o.championship,
                        "results": o.results} for o in mesmo_nome],
            "como_reproduzir": (
                f"SELECT id, raw_id, name FROM sessions WHERE event_id = "
                f"{s.event_id} AND name = {s.name!r}"),
        }))
        vazios = [o.session_id for o in mesmo_nome if not o.results]
        if vazios:
            sinais.append(Signal(SIBLING_SAME_NAME_EMPTY, OBSERVED, {
                "vazios": vazios,
                "como_reproduzir": (
                    f"SELECT s.id, count(r.id) FROM sessions s LEFT JOIN "
                    f"results r ON r.session_id = s.id WHERE s.id IN "
                    f"({', '.join(str(v) for v in vazios)}) GROUP BY s.id"),
            }))

    if s.layer == LAYER_REAL and s.championship:
        chave = (s.series, s.championship)
        if s.results and chave in nunca_carregados:
            sinais.append(Signal(CHAMPIONSHIP_NEVER_LOADED, CORPUS_DERIVED,
                                 dict(nunca_carregados[chave],
                                      campeonato=s.championship,
                                      serie=s.series)))
        perfil = perfis.get(chave)
        if perfil and s.classes and not set(s.classes) <= set(perfil):
            sinais.append(Signal(CLASS_PROFILE_FOREIGN, CORPUS_DERIVED, {
                "classes_da_sessao": s.classes,
                "perfil_do_campeonato": perfil,
                "fora_do_perfil": sorted(set(s.classes) - set(perfil)),
                "perfil_derivado_do_acervo": True,
            }))
        if chave in perfis_iguais:
            sinais.append(Signal(CHAMPIONSHIP_PROFILES_INDISTINGUISHABLE,
                                 CORPUS_DERIVED,
                                 {"identico_ao_de": perfis_iguais[chave]}))

        if s.car_set:
            outros = [o for o in por_evento.get(s.event_id, [])
                      if o.session_id != s.session_id and o.car_set == s.car_set
                      and o.championship and o.championship != s.championship]
            if outros:
                sinais.append(Signal(
                    CAR_SET_IDENTICAL_TO_OTHER_CHAMPIONSHIP, CORPUS_DERIVED, {
                        "sessoes": [{"session_id": o.session_id,
                                     "championship": o.championship,
                                     "results": o.results,
                                     "mesmo_result_content_digest":
                                         o.content == s.content}
                                    for o in outros],
                        "digest_usado": "car_set_digest",
                        "digest": s.car_set,
                        "advertencia": ("conjunto de carros igual NAO e "
                                        "conjunto de resultados igual"),
                    }))

    multiplo = multiple_of_sibling(s, mesmo_nome)
    if multiplo:
        sinais.append(Signal(CONTENT_MULTIPLE_OF_SIBLING, CORPUS_DERIVED,
                             multiplo))
    return sinais


# ── Classification ───────────────────────────────────────────

def classify(s: SessionRow, sinais: list[Signal]) -> str:
    """
    One classification, by a stated precedence.

      1. `UNDETERMINED_NO_PROVENANCE` — no origin, so nothing can be said.

      2. `STRUCTURAL_INCOMPATIBILITY_COHORT` — the championship's classes
         are absent from the corpus, the session has content, **and at
         least one other signal corroborates**. `CHAMPIONSHIP_NEVER_LOADED`
         alone does not reach this: it is one `CORPUS_DERIVED` measurement,
         and one of those is not a cohort.

      3. `ASSOCIATION_SUSPECT` — a `CORPUS_DERIVED` signal **and** an
         `OBSERVED` one that is about a disagreement. `SESSION_HAS_NO_RESULTS`,
         `CHAMPIONSHIP_LAYER_NOT_APPLICABLE` and
         `CARS_REPEATED_WITHIN_SESSION` describe the session rather than
         its association, so they are reported and not counted here — the
         last one is PR #57's triplication, a different defect.
         `CHAMPIONSHIP_PROFILES_INDISTINGUISHABLE` is excluded for a
         different reason: it is a caveat about the method, and letting a
         limitation corroborate the conclusion it limits is circular.
         An observational signal alone never gets here:
         ambiguity plus an empty sibling is still only two observations
         about names and counts, and names repeat legitimately.

      4. `PROVENANCE_METADATA_INCONSISTENT` — a `CORPUS_DERIVED` signal
         with nothing observed beside it. Something disagrees; nothing says
         which side.

      5. `AMBIGUOUS_NAME_ONLY` — the name repeats and nothing else holds.

      6. `NO_NAME_AMBIGUITY` — the name is unique in the event, and that is
         all this says.
    """
    nomes = {sig.name for sig in sinais}
    derivados = {sig.name for sig in sinais if sig.tier == CORPUS_DERIVED}
    # A ressalva nao e evidencia. `CHAMPIONSHIP_PROFILES_INDISTINGUISHABLE`
    # diz que o perfil NAO discrimina naquele evento — uma afirmacao sobre a
    # fraqueza do metodo, nao sobre esta sessao. Deixa-la corroborar faria a
    # limitacao sustentar a conclusao que ela limita.
    derivados -= CAVEAT_SIGNALS
    observados = {sig.name for sig in sinais if sig.tier == OBSERVED}
    # `CHAMPIONSHIP_LAYER_NOT_APPLICABLE` and `SESSION_HAS_NO_RESULTS`
    # describe the session, not a disagreement about it.
    observados -= {CHAMPIONSHIP_LAYER_NOT_APPLICABLE, SESSION_HAS_NO_RESULTS,
                   NO_PROVENANCE_URL, CARS_REPEATED_WITHIN_SESSION}

    if NO_PROVENANCE_URL in nomes:
        return UNDETERMINED_NO_PROVENANCE
    if CHAMPIONSHIP_NEVER_LOADED in derivados and s.results and (
            observados or (derivados - {CHAMPIONSHIP_NEVER_LOADED})):
        return STRUCTURAL_INCOMPATIBILITY_COHORT
    if derivados and observados:
        return ASSOCIATION_SUSPECT
    if derivados:
        return PROVENANCE_METADATA_INCONSISTENT
    if AMBIGUOUS_SESSION_NAME in nomes:
        return AMBIGUOUS_NAME_ONLY
    return NO_NAME_AMBIGUITY


WHAT_THIS_IS_NOT = (
    "nenhum resultado ou volta e movido, e nenhuma sessao e declarada a "
    "casa correta de coisa alguma. Os resultados de uma coorte NAO sao "
    "registros recuperaveis: a origem individual nao existe e uma sessao "
    "pode conter dados de varias cargas. Conjunto de carros igual nao prova "
    "resultados duplicados; multiplo exato nao prova erro; perfis "
    "indistinguiveis nao provam contaminacao, provam que ali o perfil nao "
    "discrimina. Exposicao e propriedade da sessao, nao da volta."
)


@dataclass
class SessionAssessment:
    session: dict = dataclass_field(default_factory=dict)
    signals: list = dataclass_field(default_factory=list)
    independent_evidence: list = dataclass_field(default_factory=list)
    classification: str = NO_NAME_AMBIGUITY
    meaning: str = ""
    signal_names: list = dataclass_field(default_factory=list)
    signals_meta: dict = dataclass_field(default_factory=dict)
    o_que_isto_nao_e: str = WHAT_THIS_IS_NOT

    def as_dict(self) -> dict:
        return dict(self.__dict__)


DEFAULT_EVIDENCE_LIMIT = 20


def assess_sessions(sessoes: list[SessionRow],
                    evidence_limit: int = DEFAULT_EVIDENCE_LIMIT
                    ) -> list[SessionAssessment]:
    """Every session, classified, with its evidence attached."""
    por_evento: dict[int, list[SessionRow]] = {}
    for s in sessoes:
        por_evento.setdefault(s.event_id, []).append(s)

    ambiguos = {s.session_id for s in sessoes
                if any(o.name == s.name and o.session_id != s.session_id
                       for o in por_evento[s.event_id])}
    perfis = class_profiles(sessoes, ambiguos)
    nunca = never_loaded_championships(sessoes)

    iguais: dict[tuple, list] = {}
    for chave, perfil in perfis.items():
        for outra, outro in perfis.items():
            if outra != chave and outra[0] == chave[0] and outro == perfil:
                iguais.setdefault(chave, []).append(outra[1])

    saida = []
    for s in sessoes:
        sinais = signals_for(s, por_evento, perfis, nunca, iguais)
        classe = classify(s, sinais)
        # Os totais sao do conjunto INTEIRO. Truncar e apresentacao, e
        # contar depois de truncar faria `--evidence-limit` mudar um numero
        # global, que e exatamente o que ele nao pode fazer.
        todos = [sig.name for sig in sinais]
        mostrados = sinais if evidence_limit <= 0 else sinais[:evidence_limit]
        a = SessionAssessment(
            session={
                "session_id": s.session_id, "series": s.series,
                "season": s.season, "event_id": s.event_id,
                "event_name": s.event_name, "session_name": s.name,
                "raw_id": s.raw_id, "session_at": s.session_at,
                "source_url": s.source_url, "championship": s.championship,
                "championship_layer": s.layer,
                "results": s.results, "laps": s.laps,
                "distinct_cars": len({n for n in s.car_numbers}),
                "class_profile": s.classes,
                "car_set_digest": s.car_set,
                "result_content_digest": s.content,
            },
            signals=[sig.as_dict() for sig in mostrados],
            independent_evidence=[],
            classification=classe,
            meaning=CLASSIFICATION_MEANING[classe],
            signal_names=todos,
            signals_meta={
                "total": len(todos),
                "exibidos": len(mostrados),
                "truncado": len(mostrados) < len(todos),
                "limite": evidence_limit,
                "como_ver_o_resto": (
                    "--evidence-limit 0 lista todos os sinais; as contagens "
                    "globais, os resultados e a classificacao nao dependem "
                    "deste limite"),
            },
        )
        saida.append(a)
    return saida


def assess_all(cur, evidence_limit: int = DEFAULT_EVIDENCE_LIMIT) -> dict:
    """
    The whole catalogue, in one pass.

    The counts are whatever the sessions say. No figure is adjusted to agree
    with an earlier measurement; a divergence is reported as a divergence.
    """
    sessoes = read_sessions(cur)
    avaliadas = assess_sessions(sessoes, evidence_limit)

    classes: dict[str, int] = {}
    resultados: dict[str, int] = {}
    voltas: dict[str, int] = {}
    por_serie: dict[str, dict] = {}
    sinais: dict[str, int] = {}
    for a in avaliadas:
        c = a.classification
        classes[c] = classes.get(c, 0) + 1
        resultados[c] = resultados.get(c, 0) + a.session["results"]
        voltas[c] = voltas.get(c, 0) + a.session["laps"]
        serie = a.session["series"]
        por_serie.setdefault(serie, {})
        por_serie[serie][c] = por_serie[serie].get(c, 0) + 1
        # `signal_names` e o conjunto inteiro, capturado antes do
        # truncamento; somar `a.signals` contaria so o que coube na tela.
        for nome in a.signal_names:
            sinais[nome] = sinais.get(nome, 0) + 1

    return {
        "sessoes": len(avaliadas),
        "classificacoes": classes,
        "resultados_por_classificacao": resultados,
        "voltas_por_classificacao": voltas,
        "classificacoes_por_serie": por_serie,
        "significados": dict(CLASSIFICATION_MEANING),
        "sinais": sinais,
        "camadas_de_evidencia": dict(TIER_MEANING),
        "significado_dos_sinais": dict(SIGNAL_MEANING),
        "camada_por_serie": dict(CHAMPIONSHIP_LAYER),
        "significado_das_camadas": dict(LAYER_MEANING),
        "campeonato_nunca_carregado_computado_em": list(NEVER_LOADED_SERIES),
        "evidencia_independente": {
            "disponivel": False,
            "motivo": TIER_MEANING[INDEPENDENT],
        },
        "o_que_isto_nao_e": WHAT_THIS_IS_NOT,
        "evidencia": {
            "limite": evidence_limit,
            "como_ver_o_resto": ("--evidence-limit 0 lista todos os sinais; "
                                 "a contagem e a classificacao nao dependem "
                                 "deste limite"),
        },
        "sessions": [a.as_dict() for a in avaliadas],
    }


def cohort(dados: dict, classificacao: str) -> list[dict]:
    """The sessions of one classification, for a focused read."""
    return [s for s in dados["sessions"] if s["classification"] == classificacao]


# ── CLI ──────────────────────────────────────────────────────

def read_only_connection(cfg: dict):
    import psycopg2
    return psycopg2.connect(connect_timeout=5,
                            options="-c default_transaction_read_only=on",
                            **cfg)


def write_report(dados: dict, destino: Path | None) -> int:
    """The only write, and it happens after the connection is closed."""
    if destino is None:
        return 0
    try:
        destino.write_text(json.dumps(dados, ensure_ascii=False, indent=2,
                                      default=str), encoding="utf-8")
    except OSError as exc:
        print(f"o relatorio nao pode ser escrito em {destino}: {exc}")
        print("o banco nao foi tocado: a conexao ja estava fechada")
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--all", action="store_true", help="every session")
    ap.add_argument("--classification", help="list one cohort")
    ap.add_argument("--session-id", type=int, help="one session")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5433)
    ap.add_argument("--dbname", default="openwec")
    ap.add_argument("--user", default="openwec")
    ap.add_argument("--password", default="")
    ap.add_argument("--evidence-limit", type=int, default=DEFAULT_EVIDENCE_LIMIT,
                    help="how many signals to list; 0 lists every one. It "
                         "never changes a count or a classification")
    ap.add_argument("--json", type=Path,
                    help="write the report here; a scratch path outside the "
                         "repository and outside raw/ is the recommendation")
    args = ap.parse_args()

    if not args.all and not args.classification and args.session_id is None:
        ap.error("informe --all, --classification ou --session-id")

    cfg = {"host": args.host, "port": args.port, "dbname": args.dbname,
           "user": args.user, "password": args.password}
    conn = read_only_connection(cfg)
    try:
        with conn.cursor() as cur:
            dados = assess_all(cur, args.evidence_limit)
    except SessionAssociationError as exc:
        conn.rollback()
        conn.close()
        print(f"LEITURA FALHOU: {exc}")
        return 2
    finally:
        if not conn.closed:
            conn.rollback()
            conn.close()
    # No database from here on.

    print(f"sessoes: {dados['sessoes']}")
    print(f"  classificacoes: {dados['classificacoes']}")
    print(f"  resultados: {dados['resultados_por_classificacao']}")
    print(f"  camada por serie: {dados['camada_por_serie']}")
    print(f"  evidencia independente disponivel: "
          f"{dados['evidencia_independente']['disponivel']}")
    print("  sinais: " + "  ".join(f"{k}={v}"
                                   for k, v in sorted(dados["sinais"].items())))

    if args.classification:
        alvo = cohort(dados, args.classification)
        print(f"\n{args.classification}: {len(alvo)} sessoes")
        for a in alvo[:40]:
            s = a["session"]
            print(f"  {s['session_id']:>6} {s['series']:<10} {s['season']:<10} "
                  f"{s['session_name']:<28} res={s['results']:<5} "
                  f"camp={s['championship']}")
    if args.session_id is not None:
        for a in dados["sessions"]:
            if a["session"]["session_id"] == args.session_id:
                print("\n" + json.dumps(a, ensure_ascii=False, indent=2,
                                        default=str))

    return write_report(dados, args.json)


if __name__ == "__main__":
    raise SystemExit(main())
