"""
OpenWEC — when several `drivers` rows carry one name, what can be proved?

`drivers` holds 2.862 rows for at most 1.783 people: 1.079 folded names are
spread over 2.219 rows. The cause is known and closed — PR #45 hardened the
loader, PR #46 documented the cycle — but the acquired duplication is still
there, and nothing in this module touches it.

What this module adds is a **classification with its evidence attached**, so
that a future decision about reconciliation is made on measured grounds
rather than on spelling.

**No result of this module authorises merging a row, deleting a row or
repointing a reference.** `driver_ids` always holds **every** row of the
group. There is no `canonical_driver_id` field, and adding one would defeat
the purpose: a lower id, an earlier `created_at`, more laps or more metadata
are **not** evidence of correctness.

## The group key, and why that fold

Groups come from `full_name_key` in `row_identity.py` — the same fold the
loader uses to decide a driver is already stored. Borrowing it means this
assessment answers the question the loader actually asks, and that a group
here is a group there. The fold joins first and last before folding, because
the boundary between them is a parser artefact: `split_wec_driver_name` cuts
at the last space, so `Giedo VAN DER GARDE` becomes `('Giedo VAN DER',
'GARDE')` while another path produced `('Giedo', 'Van Der Garde')`.

**Folding alike is a statement about text, never about people.** Two people
with one name produce the same key, and nothing in this table separates
them. That is why the fold opens the question instead of answering it.

## What counts as evidence

Only the columns of `drivers` that are **not** the name and not bookkeeping.
The set is discovered from the catalogue, so a column added later joins the
comparison instead of being skipped in silence.

Each field lands in exactly one state, and the three are kept apart because
they are three different facts:

  agreeing      two or more rows state a value and the values are equal
  contradicting two or more rows state a value and the values differ
  exclusive     exactly one row states it — it corroborates nothing, and it
                contradicts nothing, but discarding that row would lose it

A field absent from every row is none of the three. **A stored NULL is
silence, not agreement.**

## Two warnings this module is built around

**`country` divergence is not proof of two people.** The column's semantics
are not established — nationality, licence, or the source's own choice — and
the audit of PR #46 measured a case where it diverges within one person:
Devlin DeFrancesco is CAN on one row and ITA on another under the **same**
`imsa_driver_id`. A country conflict is reported as a contradiction in the
*evidence*, and the justification says so explicitly.

**IMSA identifiers are not infallible.** `Job van Uitert` carries **two**
different `imsa_driver_id` across four rows. An external id is treated as
one more field that can agree, conflict or be absent — never as an arbiter.

## Read only

Every statement is a `SELECT`. The CLI opens its connection with
`default_transaction_read_only=on`, and the report — the one thing that
writes — is produced after the connection is closed, to a path the caller
names explicitly, outside the repository and outside `raw/`.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "loader"))

from row_identity import full_name_key  # noqa: E402

# ── Classifications ──────────────────────────────────────────
#
# One group gets exactly one of these, by the precedence in `classify`.
# Everything a classification leaves unsaid travels as a flag, because the
# categories are **not** mutually exclusive in the data: DeFrancesco has a
# contradiction and a corroboration at once.

CORROBORATED = "CORROBORATED"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
NOT_A_PERSON = "NOT_A_PERSON"

CLASSIFICATIONS = (CORROBORATED, REVIEW_REQUIRED, CONFLICTING_EVIDENCE,
                   INSUFFICIENT_EVIDENCE, NOT_A_PERSON)

# What each one claims, kept next to the name so a report can carry it.
MEANING = {
    CORROBORATED: ("evidencia independente concordante, sem contradicao "
                   "conhecida. NAO significa identidade comprovada"),
    REVIEW_REQUIRED: ("ha indicios de equivalencia, mas falta analise "
                      "adicional: a evidencia existe de um lado so"),
    CONFLICTING_EVIDENCE: ("atributos contraditorios ou identificadores "
                           "externos incompativeis. NAO prova pessoas "
                           "distintas"),
    INSUFFICIENT_EVIDENCE: ("somente a equivalencia textual sustenta a "
                            "hipotese"),
    NOT_A_PERSON: ("marcador de inscricao, como TBD, e nao uma identidade "
                   "humana"),
}

# ── Flags ────────────────────────────────────────────────────
#
# Additional signals. A flag never changes the classification; it carries
# what the single label cannot.

FLAG_MONONYM = "MONONYM_OR_PSEUDONYM"
FLAG_EXTERNAL_ID_AGREES = "EXTERNAL_ID_AGREES"
FLAG_EXTERNAL_ID_CONFLICT = "EXTERNAL_ID_CONFLICT"
FLAG_EXTERNAL_ID_ABSENT = "EXTERNAL_ID_ABSENT"
FLAG_COUNTRY_CONFLICT = "COUNTRY_CONFLICT"
FLAG_CASE_ONLY = "CASE_ONLY_DIFFERENCE"
FLAG_SEGMENTATION = "SEGMENTATION_DIFFERENCE"
FLAG_MULTI_ROW = "MULTI_ROW_GROUP"
FLAG_SAME_CREATION_DATE = "SAME_CREATION_DATE"
FLAG_DISTINCT_IDS_SAME_RESULT = "DISTINCT_IDS_IN_ONE_RESULT"
FLAG_REPEATED_ID_SAME_RESULT = "REPEATED_ID_IN_ONE_RESULT"
FLAG_EVIDENCE_ON_ONE_ROW_ONLY = "EVIDENCE_ON_ONE_ROW_ONLY"
FLAG_NO_EVIDENCE_ON_ANY_ROW = "NO_EVIDENCE_ON_ANY_ROW"
FLAG_WEAK_CONTRADICTION_ONLY = "CONTRADICTION_ONLY_IN_WEAK_ATTRIBUTES"
FLAG_EXTERNAL_ID_ON_A_MARKER = "EXTERNAL_ID_ON_A_MARKER"

# ── Impediments to a future reconciliation ───────────────────

IMPEDIMENT_NO_EVIDENCE = "NO_INDEPENDENT_EVIDENCE"
IMPEDIMENT_CONTRADICTING = "CONTRADICTING_ATTRIBUTE"
IMPEDIMENT_EXTERNAL_ID_CONFLICT = "EXTERNAL_ID_CONFLICT"
IMPEDIMENT_NOT_A_PERSON = "NOT_A_PERSON"
IMPEDIMENT_EXCLUSIVE_METADATA = "EXCLUSIVE_METADATA_ON_A_ROW"
IMPEDIMENT_MULTI_ROW = "MULTI_ROW_GROUP"
IMPEDIMENT_DISTINCT_IDS_SAME_RESULT = "DISTINCT_IDS_IN_ONE_RESULT"
IMPEDIMENT_NAME_BEARING_VIEW = "NAME_BEARING_VIEW_DEPENDS_ON_SPELLING"

# ── Markers ──────────────────────────────────────────────────
#
# A marker is a **token of the name itself**, not a shape of the row.
#
# This distinction is the whole reason the set is a literal list rather than
# a rule like "first_name is empty". `Bankcy` and `Hash` are mononymous
# pseudonyms: real competitors whose entry carries one name, which leaves
# `first_name` empty. Classifying them by that shape would erase people.
# `TBD` is a placeholder for an entry not yet announced, and merging two of
# them across different events would invent a person.
MARKER_TOKENS = frozenset({
    "tbd", "tba", "na", "n/a", "none", "null", "unknown", "desconhecido",
    "driver", "piloto", "x", "xx", "xxx", "?", "-", "--",
})


def marker_name(first: str | None, last: str | None) -> bool:
    """
    Whether this name is a placeholder rather than a person.

    Every stated token has to be a marker. One real token is enough to make
    the name a name: `TBD Smith` is not a placeholder, and this returns
    False for it.
    """
    tokens = [t for t in ((first or "") + " " + (last or "")).split() if t]
    if not tokens:
        return False
    return all(t.strip().lower() in MARKER_TOKENS for t in tokens)


def mononym(first: str | None, last: str | None) -> bool:
    """One of the two name fields is empty — a pseudonym or a single name."""
    return bool((first or "").strip()) != bool((last or "").strip())


# ── Evidence columns ─────────────────────────────────────────

# Why each is left out. A column not named here joins the evidence set
# automatically, which is the safe default: a column added to `drivers`
# becomes evidence rather than being silently ignored.
EXCLUDED_COLUMNS = {
    "id": "surrogate key, different by construction for any two rows",
    "first_name": "the name being folded, not evidence about whose it is",
    "last_name": "the name being folded, not evidence about whose it is",
    "created_at": "bookkeeping of the ingestion, not a fact about the person",
}

# Treated apart in the report because the brief for this module requires
# absence, agreement and conflict to be told apart for external ids
# specifically. They are still ordinary fields for classification: an
# external id that conflicts is a contradiction, never an arbiter.
EXTERNAL_ID_COLUMNS = ("imsa_driver_id", "imsa_driver_plug_id")

# Columns measured to diverge **within one external identity**, so a
# contradiction in them is weak evidence of two people.
#
# The measurement is `attribute_stability`, and it is run rather than
# quoted. On the development corpus, of the 71 `imsa_driver_id` values
# carried by more than one row — the strongest corroboration this table
# offers — `imsa_driver_plug_id` diverges in 15 and `imsa_driver_rating` in
# 8, against 2 for `country` and 5 for `license`. The slot id is the least
# identity-stable column in the table, and the rating is one a competitor
# legitimately changes between seasons.
#
# This never changes a classification. A contradiction stays a
# contradiction; the flag says what kind it is.
WEAKLY_IDENTIFYING_COLUMNS = {
    "imsa_driver_plug_id": ("diverge dentro de um mesmo imsa_driver_id; "
                            "parece indexar a inscricao, nao a pessoa"),
    "imsa_driver_rating": ("a classificacao desportiva de um piloto muda "
                           "entre temporadas por desenho"),
}


class DriverIdentityError(Exception):
    """A read failed. Nothing is written, and no group is called resolved."""


def evidence_columns(cur) -> tuple[str, ...]:
    """The `drivers` columns that count as independent evidence."""
    cur.execute("""
        SELECT column_name FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'drivers'
         ORDER BY ordinal_position
    """)
    todas = [linha[0] for linha in cur.fetchall()]
    if not todas:
        raise DriverIdentityError("a tabela drivers nao foi encontrada")
    return tuple(c for c in todas if c not in EXCLUDED_COLUMNS)


# ── Rows ─────────────────────────────────────────────────────

@dataclass
class DriverRow:
    driver_id: int
    first_name: str | None
    last_name: str | None
    created_at: str | None
    evidence: dict = dataclass_field(default_factory=dict)
    laps: int = 0
    result_drivers: int = 0

    @property
    def spelling(self) -> str:
        return f"{self.first_name or ''}|{self.last_name or ''}"

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["spelling"] = self.spelling
        return d


def group_rows(cur, colunas: tuple[str, ...]) -> dict[str, list[DriverRow]]:
    """
    Every `drivers` row, folded into groups, with its reference counts.

    The fold happens here rather than in SQL on purpose: `full_name_key` is
    the loader's own function, and reimplementing it as a `regexp_replace`
    would be a second definition free to drift from the first.

    The whole table is read. No `LIMIT`, no sampling: a group this module
    did not see is a group it cannot classify, and silence about it would
    read as absence.
    """
    campos = ", ".join(f"d.{c}" for c in colunas)
    cur.execute(f"""
        SELECT d.id, d.first_name, d.last_name, d.created_at::text, {campos},
               (SELECT count(*) FROM laps l WHERE l.driver_id = d.id),
               (SELECT count(*) FROM result_drivers rd WHERE rd.driver_id = d.id)
          FROM drivers d
         ORDER BY d.id
    """)
    grupos: dict[str, list[DriverRow]] = {}
    for linha in cur.fetchall():
        corte = 4 + len(colunas)
        row = DriverRow(
            driver_id=linha[0], first_name=linha[1], last_name=linha[2],
            created_at=linha[3],
            evidence=dict(zip(colunas, linha[4:corte])),
            laps=linha[corte], result_drivers=linha[corte + 1])
        grupos.setdefault(full_name_key(row.first_name, row.last_name),
                          []).append(row)
    return grupos


# How a diacritic-folded grouping relates to the groups that exist today.
# These are statements about **grouping structure**, never about people.
VARIANT_MERGE = "FUNDE_GRUPOS_EXISTENTES"
VARIANT_ABSORBS = "INCORPORA_REGISTROS_ISOLADOS"
VARIANT_NEW = "NOVO_AGRUPAMENTO"
VARIANT_UNCHANGED = "AGRUPAMENTO_INALTERADO"

VARIANT_STRUCTURES = (VARIANT_MERGE, VARIANT_ABSORBS, VARIANT_NEW,
                      VARIANT_UNCHANGED)

VARIANT_MEANING = {
    VARIANT_MERGE: ("duas ou mais chaves que hoje ja sao grupo passariam a "
                    "ser um so agrupamento"),
    VARIANT_ABSORBS: ("um grupo existente passaria a conter registros que "
                      "hoje nao estao em grupo algum"),
    VARIANT_NEW: ("registros hoje isolados passariam a formar um "
                  "agrupamento entre si"),
    VARIANT_UNCHANGED: ("as mesmas linhas, sob uma chave escrita sem "
                        "acento: nada muda"),
}


def _sem_acento(s: str) -> str:
    """Experimental fold, used to **compare** and never to group anything."""
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def variant_structure(ids: list[int], chave_de: dict[int, str],
                      grupos_atuais: dict) -> str:
    """
    Which of the four shapes this folded grouping has.

    The question is answered from the **current keys its members carry**,
    not from the folded key's spelling. That is the distinction the first
    version of this measurement missed.
    """
    origens = {chave_de[i] for i in ids}
    ja_eram_grupo = {o for o in origens if o in grupos_atuais}
    if len(origens) == 1:
        return VARIANT_UNCHANGED
    if len(ja_eram_grupo) >= 2:
        return VARIANT_MERGE
    if len(ja_eram_grupo) == 1:
        return VARIANT_ABSORBS
    return VARIANT_NEW


def fold_blind_spot(todos: dict[str, list[DriverRow]]) -> dict:
    """
    What this module's own grouping cannot see — counted as groupings.

    `full_name_key` folds case and punctuation and **not diacritics**, so
    `Jérôme` and `Jerome` are two keys and their rows are never compared
    here. That is deliberate: the fold has to be the loader's, or a group in
    this report would not be a group there.

    **The first version of this function counted keys and reported them as
    groups, and the two are not the same thing.** It compared each folded
    key against the set of keys that already name a group, which is wrong in
    both directions:

      · a folded key that merely lost an accent was counted as new even
        when it groups exactly the same rows — `andrelotterer` against the
        existing `andrélotterer`, the same two ids. **29 of the 40 it
        reported were this**, and the module already saw every one of them.
      · a grouping that genuinely changed was **not** counted when its
        folded key happened to already name a group — `antoniofelixdacosta`
        absorbing `antóniofélixdacosta`. **27 were missed this way.**

    `40 − 29 + 27 = 38`. Both numbers are reported below under names that
    say which is which, because the 40 is a true count of alternative keys
    and a false count of anything else.

    Measured on the rows already read, so it costs no extra query, and
    reported rather than acted on. **Nothing here changes a key, a group, a
    classification or a row, and no grouping below is an identity.**
    """
    dobrado: dict[str, list[int]] = {}
    for chave, linhas in todos.items():
        dobrado.setdefault(_sem_acento(chave), []).extend(
            r.driver_id for r in linhas)

    atuais = {k: v for k, v in todos.items() if len(v) > 1}
    maiores = {k: v for k, v in dobrado.items() if len(v) > 1}
    chave_de = {r.driver_id: k for k, linhas in todos.items() for r in linhas}
    voltas = {r.driver_id: r.laps for linhas in todos.values() for r in linhas}
    vinculos = {r.driver_id: r.result_drivers
                for linhas in todos.values() for r in linhas}

    estrutura = {k: variant_structure(ids, chave_de, atuais)
                 for k, ids in maiores.items()}
    alterados = sorted(k for k, e in estrutura.items()
                       if e != VARIANT_UNCHANGED)
    chaves_alternativas = sorted(k for k in maiores if k not in atuais)
    sem_efeito = [k for k in chaves_alternativas
                  if estrutura[k] == VARIANT_UNCHANGED]

    envolvidos = sorted({i for k in alterados for i in maiores[k]})
    em_grupo = {r.driver_id for v in atuais.values() for r in v}
    por_estrutura: dict[str, int] = {}
    for k in alterados:
        por_estrutura[estrutura[k]] = por_estrutura.get(estrutura[k], 0) + 1

    return {
        "dobra_usada": ("full_name_key de row_identity: caixa e pontuacao, "
                        "nao diacriticos"),
        "grupos_com_a_dobra_atual": len(atuais),
        "grupos_se_diacriticos_fossem_dobrados": len(maiores),
        "linhas_com_a_dobra_atual": sum(len(v) for v in atuais.values()),
        "linhas_se_diacriticos_fossem_dobrados": sum(
            len(v) for v in maiores.values()),
        # Chaves, e so chaves. Este numero nao conta grupos nem pessoas.
        "chaves_alternativas": len(chaves_alternativas),
        "chaves_alternativas_sem_efeito_no_agrupamento": len(sem_efeito),
        "agrupamentos_alterados": len(alterados),
        "agrupamentos_alterados_por_estrutura": por_estrutura,
        "significados": dict(VARIANT_MEANING),
        "registros_envolvidos": len(envolvidos),
        "registros_envolvidos_hoje_isolados": len(
            [i for i in envolvidos if i not in em_grupo]),
        "voltas_envolvidas": sum(voltas[i] for i in envolvidos),
        "result_drivers_envolvidos": sum(vinculos[i] for i in envolvidos),
        "exemplos": alterados[:20],
        "reconciliacao": (
            f"{len(chaves_alternativas)} chaves alternativas "
            f"- {len(sem_efeito)} sem efeito no agrupamento "
            f"+ {len(alterados) - (len(chaves_alternativas) - len(sem_efeito))} "
            f"alterados cuja chave ja era chave de grupo "
            f"= {len(alterados)} agrupamentos alterados"),
        "leitura": ("o numero de grupos e um limite inferior; estes nao sao "
                    "avaliados por este modulo, nenhuma chave foi alterada, "
                    "e um agrupamento aqui e equivalencia textual, nunca "
                    "identidade"),
    }


def variant_groupings(todos: dict[str, list[DriverRow]],
                      colunas: tuple[str, ...]) -> list[dict]:
    """
    The groupings a diacritic fold would change, one by one, with evidence.

    This is the read-only exposure of the 38. Each entry says which current
    keys it spans, which rows it would bring together, what the evidence
    across those rows does, and **every driver id involved**. There is no
    canonical row here either: the fold is not applied, nothing is merged,
    and a grouping is a statement about text.
    """
    dobrado: dict[str, list[int]] = {}
    for chave, linhas in todos.items():
        dobrado.setdefault(_sem_acento(chave), []).extend(
            r.driver_id for r in linhas)

    atuais = {k: v for k, v in todos.items() if len(v) > 1}
    chave_de = {r.driver_id: k for k, linhas in todos.items() for r in linhas}
    por_id = {r.driver_id: r for linhas in todos.values() for r in linhas}

    saida = []
    for chave in sorted(dobrado):
        ids = sorted(dobrado[chave])
        if len(ids) < 2:
            continue
        estrutura = variant_structure(ids, chave_de, atuais)
        if estrutura == VARIANT_UNCHANGED:
            continue
        linhas = [por_id[i] for i in ids]
        e = compare_evidence(linhas, colunas)
        origens = sorted({chave_de[i] for i in ids})
        saida.append({
            "chave_dobrada": chave,
            "estrutura": estrutura,
            "significado": VARIANT_MEANING[estrutura],
            # Todos, sempre. Nenhum canonico e escolhido aqui tampouco.
            "driver_ids": ids,
            "grafias": [f"{r.first_name or ''}|{r.last_name or ''}"
                        for r in linhas],
            "chaves_atuais": origens,
            "chaves_atuais_que_ja_sao_grupo": [o for o in origens if o in atuais],
            "evidencia": {"concordante": sorted(e.agreeing),
                          "contraditoria": sorted(e.contradicting),
                          "exclusiva": sorted(e.exclusive),
                          "ausente": sorted(e.absent)},
            "classificacao_de_evidencia": classify(linhas, e)[0],
            "laps": sum(r.laps for r in linhas),
            "result_drivers": sum(r.result_drivers for r in linhas),
            "o_que_isto_nao_e": ("equivalencia textual sob uma dobra que NAO "
                                 "esta em vigor; nao e identidade provada e "
                                 "nao autoriza fusao alguma"),
        })
    return saida


# ── Comparing the evidence ───────────────────────────────────

@dataclass
class FieldEvidence:
    agreeing: dict = dataclass_field(default_factory=dict)
    contradicting: dict = dataclass_field(default_factory=dict)
    exclusive: dict = dataclass_field(default_factory=dict)
    absent: list = dataclass_field(default_factory=list)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def compare_evidence(linhas: list[DriverRow],
                     colunas: tuple[str, ...]) -> FieldEvidence:
    """
    Each evidence field placed in exactly one state.

    A value stated by one row only is `exclusive`, not `agreeing`. That
    distinction is the difference between "the rows corroborate each other"
    and "one row knows something the others do not", and collapsing it is
    how a weak corroboration gets reported as a strong one.
    """
    e = FieldEvidence()
    for coluna in colunas:
        declarados = {r.driver_id: r.evidence.get(coluna)
                      for r in linhas if r.evidence.get(coluna) is not None}
        if not declarados:
            e.absent.append(coluna)
            continue
        valores = {str(v) for v in declarados.values()}
        if len(declarados) == 1:
            (did, valor), = declarados.items()
            e.exclusive[coluna] = {"valor": str(valor), "somente_em": did}
        elif len(valores) == 1:
            e.agreeing[coluna] = {"valor": valores.pop(),
                                  "em": sorted(declarados)}
        else:
            e.contradicting[coluna] = {
                str(did): str(v) for did, v in sorted(declarados.items())}
    return e


def external_id_state(e: FieldEvidence) -> dict:
    """
    Absence, agreement and conflict told apart for the external ids.

    Kept separate from the general field comparison because an external id
    carries a different kind of claim — it is the source's own assertion of
    identity — and because this module must never let it decide. It appears
    here as a reported state, and in `classify` as one ordinary field.
    """
    estados = {}
    for coluna in EXTERNAL_ID_COLUMNS:
        if coluna in e.contradicting:
            estados[coluna] = {"estado": "CONFLICT",
                               "valores": e.contradicting[coluna]}
        elif coluna in e.agreeing:
            estados[coluna] = {"estado": "AGREEMENT",
                               "valor": e.agreeing[coluna]["valor"],
                               "em": e.agreeing[coluna]["em"]}
        elif coluna in e.exclusive:
            estados[coluna] = {"estado": "ONE_ROW_ONLY",
                               "valor": e.exclusive[coluna]["valor"],
                               "somente_em": e.exclusive[coluna]["somente_em"]}
        else:
            estados[coluna] = {"estado": "ABSENT"}
    return estados


def attribute_stability(cur, colunas: tuple[str, ...]) -> dict:
    """
    How often each column diverges **inside one external identity**.

    Rows sharing a non-null `imsa_driver_id` are the closest this table gets
    to a known-same-person set. A column that disagrees across them is
    disagreeing about one person, so a contradiction in it says little about
    whether two rows are two people.

    Measured rather than asserted, and reported as context: no
    classification reads this.
    """
    medidas = {}
    for coluna in colunas:
        if coluna == "imsa_driver_id":
            continue
        cur.execute(f"""
            SELECT count(*) FILTER (WHERE distintos > 1), count(*)
              FROM (SELECT count(DISTINCT {coluna})
                           FILTER (WHERE {coluna} IS NOT NULL) AS distintos
                      FROM drivers
                     WHERE imsa_driver_id IS NOT NULL
                  GROUP BY imsa_driver_id
                    HAVING count(*) > 1) AS por_identidade
        """)
        linha = cur.fetchone()
        if linha is None:
            raise DriverIdentityError(
                f"a medicao de estabilidade de {coluna} nao devolveu linha")
        divergem, total = linha
        medidas[coluna] = {
            "identidades_externas_com_mais_de_uma_linha": total,
            "nas_quais_a_coluna_diverge": divergem,
            "fraca": coluna in WEAKLY_IDENTIFYING_COLUMNS,
        }
    return {"base": ("linhas que compartilham um imsa_driver_id nao nulo; "
                     "divergencia ai e divergencia dentro de uma pessoa"),
            "colunas": medidas,
            "declaradas_fracas": dict(WEAKLY_IDENTIFYING_COLUMNS)}


# ── Spelling ─────────────────────────────────────────────────

def spelling_difference(linhas: list[DriverRow]) -> str:
    """
    How the spellings differ, once they are known to fold alike.

    `CASE_ONLY` means the two fields match case-insensitively field by
    field. `SEGMENTATION` means they do not — the boundary between first and
    last moved, which is what `split_wec_driver_name` and
    `split_compound_name` do to the same name.
    """
    cruas = {(r.first_name or "", r.last_name or "") for r in linhas}
    if len(cruas) == 1:
        return "IDENTICAL"
    dobradas = {(f.lower(), l.lower()) for f, l in cruas}
    return "CASE_ONLY" if len(dobradas) == 1 else "SEGMENTATION"


# ── Dependencies ─────────────────────────────────────────────

def result_slot_collisions(cur, ids: list[int]) -> dict:
    """
    Two rows of this group inside one `results` row.

    Two **different** ids in different slots would be positive evidence of
    distinct people: they raced the same car as two drivers. The **same** id
    twice is a different fact — a pre-existing duplication in
    `result_drivers` — and it is not evidence about identity at all.

    PR #46 §5.2 measured the first and found zero. This reports both, so the
    zero is not mistaken for the absence of the second.
    """
    cur.execute("""
        SELECT rd1.result_id, rd1.driver_id, rd2.driver_id
          FROM result_drivers rd1
          JOIN result_drivers rd2
            ON rd2.result_id = rd1.result_id AND rd2.slot > rd1.slot
         WHERE rd1.driver_id = ANY(%s) AND rd2.driver_id = ANY(%s)
         ORDER BY rd1.result_id, rd1.driver_id, rd2.driver_id
    """, (ids, ids))
    distintos, repetidos = [], []
    for result_id, a, b in cur.fetchall():
        (distintos if a != b else repetidos).append(
            {"result_id": result_id, "driver_ids": [a, b]})
    return {"ids_distintos_no_mesmo_result": distintos,
            "id_repetido_no_mesmo_result": repetidos}


# `v_driver_career` materialises `first_name || ' ' || last_name` as a
# column. Rows of one group therefore appear in it under different names,
# and the view splits one career. It carries no foreign key, so a search for
# dependencies by constraint would not find it.
NAME_BEARING_VIEWS = ("v_driver_career",)


def career_view_rows(cur, ids: list[int]) -> dict:
    """How `v_driver_career` renders this group today, and under how many names."""
    cur.execute("""
        SELECT d.id, (d.first_name || ' ' || d.last_name) AS exibido,
               (SELECT count(*) FROM result_drivers rd
                  JOIN results r ON r.id = rd.result_id
                  JOIN sessions s ON s.id = r.session_id
                 WHERE rd.driver_id = d.id
                   AND s.session_type = 'Race'
                   AND (s.snapshot_hour IS NULL OR s.snapshot_hour = 24))
          FROM drivers d WHERE d.id = ANY(%s) ORDER BY d.id
    """, (ids,))
    por_id = [{"driver_id": i, "exibido_como": n, "linhas_de_carreira": c}
              for i, n, c in cur.fetchall()]
    return {"view": NAME_BEARING_VIEWS[0],
            "nomes_distintos": len({x["exibido_como"] for x in por_id}),
            "linhas_de_carreira": sum(x["linhas_de_carreira"] for x in por_id),
            "por_id": por_id}


# ── Classification ───────────────────────────────────────────

def classify(linhas: list[DriverRow], e: FieldEvidence) -> tuple[str, list[str]]:
    """
    One classification, by a stated precedence, plus the flags it omits.

    The order is not arbitrary, and each step says what it refuses to let
    the next one claim:

      1. `NOT_A_PERSON` — the name is a placeholder. No evidence question
         arises about a marker, and asking it would be asking whose name
         `TBD` is. It comes first so that a marker is never reported as a
         corroborated person.

      2. `CONFLICTING_EVIDENCE` — something contradicts. It outranks
         corroboration because a group can have both, and reporting only the
         agreement would hide the disagreement. DeFrancesco is exactly this:
         `country` conflicts while `imsa_driver_id` agrees, and the flags
         carry both.

      3. `CORROBORATED` — at least one field is stated by two or more rows
         and agrees, and nothing contradicts. It says the evidence points
         one way, and the meaning string says what it does not say.

      4. `REVIEW_REQUIRED` — evidence exists, but only ever on one row of
         the group, so it can neither corroborate nor contradict. There is
         something for a human to look at.

      5. `INSUFFICIENT_EVIDENCE` — the fold is all there is.
    """
    flags: list[str] = []

    if len(linhas) > 2:
        flags.append(FLAG_MULTI_ROW)
    if any(mononym(r.first_name, r.last_name) for r in linhas):
        flags.append(FLAG_MONONYM)
    diferenca = spelling_difference(linhas)
    if diferenca == "CASE_ONLY":
        flags.append(FLAG_CASE_ONLY)
    elif diferenca == "SEGMENTATION":
        flags.append(FLAG_SEGMENTATION)
    datas = {(r.created_at or "")[:10] for r in linhas}
    if len(datas) == 1:
        flags.append(FLAG_SAME_CREATION_DATE)

    externos = EXTERNAL_ID_COLUMNS
    if any(c in e.contradicting for c in externos):
        flags.append(FLAG_EXTERNAL_ID_CONFLICT)
    elif any(c in e.agreeing for c in externos):
        flags.append(FLAG_EXTERNAL_ID_AGREES)
    elif not any(c in e.exclusive for c in externos):
        flags.append(FLAG_EXTERNAL_ID_ABSENT)
    if "country" in e.contradicting:
        flags.append(FLAG_COUNTRY_CONFLICT)

    if not e.agreeing and not e.contradicting and not e.exclusive:
        flags.append(FLAG_NO_EVIDENCE_ON_ANY_ROW)
    elif not e.agreeing and not e.contradicting:
        flags.append(FLAG_EVIDENCE_ON_ONE_ROW_ONLY)

    if e.contradicting and set(e.contradicting) <= set(WEAKLY_IDENTIFYING_COLUMNS):
        flags.append(FLAG_WEAK_CONTRADICTION_ONLY)

    if all(marker_name(r.first_name, r.last_name) for r in linhas):
        # An external identifier agreeing on a placeholder is the plainest
        # demonstration that the identifier does not establish a person.
        if any(c in e.agreeing for c in EXTERNAL_ID_COLUMNS):
            flags.append(FLAG_EXTERNAL_ID_ON_A_MARKER)
        return NOT_A_PERSON, flags
    if e.contradicting:
        return CONFLICTING_EVIDENCE, flags
    if e.agreeing:
        return CORROBORATED, flags
    if e.exclusive:
        return REVIEW_REQUIRED, flags
    return INSUFFICIENT_EVIDENCE, flags


def justification(classificacao: str, e: FieldEvidence, flags: list[str]) -> str:
    """Why this group got this label, in the terms the evidence was measured in."""
    if classificacao == NOT_A_PERSON:
        base = ("todos os tokens do nome sao marcadores de inscricao; "
                "nenhuma pergunta de identidade se aplica")
        if FLAG_EXTERNAL_ID_ON_A_MARKER in flags:
            return (base + "; um identificador externo CONCORDA entre estas "
                    "linhas, o que demonstra que ele nao estabelece pessoa")
        return base
    if classificacao == CONFLICTING_EVIDENCE:
        campos = ", ".join(sorted(e.contradicting))
        extra = ""
        if FLAG_COUNTRY_CONFLICT in flags:
            extra = ("; country divergente NAO prova pessoas distintas: a "
                     "semantica do campo nao esta estabelecida")
        if FLAG_EXTERNAL_ID_AGREES in flags:
            extra += ("; um identificador externo CONCORDA no mesmo grupo, "
                      "o que torna a contradicao da fonte, nao da identidade")
        if FLAG_WEAK_CONTRADICTION_ONLY in flags:
            extra += ("; a contradicao esta SOMENTE em atributos medidos "
                      "como instaveis dentro de uma mesma identidade externa")
        return f"contradicao em {campos}{extra}"
    if classificacao == CORROBORATED:
        campos = ", ".join(sorted(e.agreeing))
        return (f"{campos} declarado(s) por duas ou mais linhas e "
                f"concordante(s), sem contradicao conhecida. Nao demonstra "
                f"identidade: duas pessoas de mesmo nome e mesmo pais "
                f"produziriam esta mesma evidencia")
    if classificacao == REVIEW_REQUIRED:
        campos = ", ".join(sorted(e.exclusive))
        return (f"evidencia presente apenas em uma linha ({campos}); nao "
                f"corrobora nem contradiz, e precisa de analise adicional")
    return ("nenhuma linha do grupo declara atributo independente; so a "
            "equivalencia textual sustenta a hipotese")


# ── Impediments ──────────────────────────────────────────────

def impediments_of(linhas: list[DriverRow], e: FieldEvidence,
                   classificacao: str, colisoes: dict) -> list[str]:
    """
    What a reconciliation would have to answer for, named one by one.

    An empty list is not permission. It means this module found nothing in
    the way, which is a statement about what was measured and not about what
    is allowed.
    """
    impedimentos: list[str] = []
    if classificacao == NOT_A_PERSON:
        impedimentos.append(IMPEDIMENT_NOT_A_PERSON)
    if e.contradicting:
        impedimentos.append(IMPEDIMENT_CONTRADICTING)
    if any(c in e.contradicting for c in EXTERNAL_ID_COLUMNS):
        impedimentos.append(IMPEDIMENT_EXTERNAL_ID_CONFLICT)
    if not e.agreeing and not e.contradicting and not e.exclusive:
        impedimentos.append(IMPEDIMENT_NO_EVIDENCE)
    if e.exclusive:
        impedimentos.append(IMPEDIMENT_EXCLUSIVE_METADATA)
    if len(linhas) > 2:
        impedimentos.append(IMPEDIMENT_MULTI_ROW)
    if colisoes["ids_distintos_no_mesmo_result"]:
        impedimentos.append(IMPEDIMENT_DISTINCT_IDS_SAME_RESULT)
    # Always: the view renders the name, so any reconciliation changes what
    # it returns, and it is reachable by no foreign key.
    impedimentos.append(IMPEDIMENT_NAME_BEARING_VIEW)
    return impedimentos


# ── The group ────────────────────────────────────────────────

@dataclass
class GroupAssessment:
    key: str
    driver_ids: list = dataclass_field(default_factory=list)
    rows: list = dataclass_field(default_factory=list)
    spellings: list = dataclass_field(default_factory=list)
    spelling_difference: str = "IDENTICAL"
    created_at: list = dataclass_field(default_factory=list)
    classification: str = INSUFFICIENT_EVIDENCE
    meaning: str = ""
    flags: list = dataclass_field(default_factory=list)
    evidence: dict = dataclass_field(default_factory=dict)
    evidence_meta: dict = dataclass_field(default_factory=dict)
    # Ids covered by at least one agreeing field, taken from the **whole**
    # evidence before any truncation. Keeping it here is what makes
    # `--evidence-limit` structurally unable to move a coverage answer:
    # `corroboration_reaches` reads this and never the displayed dict.
    corroborated_ids: list = dataclass_field(default_factory=list)
    external_ids: dict = dataclass_field(default_factory=dict)
    references: dict = dataclass_field(default_factory=dict)
    dependencies: dict = dataclass_field(default_factory=dict)
    impediments: list = dataclass_field(default_factory=list)
    justification: str = ""

    def as_dict(self) -> dict:
        return dict(self.__dict__)


DEFAULT_EVIDENCE_LIMIT = 20


def _truncate(evidencia: FieldEvidence, limite: int) -> tuple[dict, dict]:
    """
    Cap how many evidence entries are listed, never how many are counted.

    The classification is computed from the whole `FieldEvidence` before
    this runs, so no limit can move a group between categories. The metadata
    says how many there were and whether the list was cut.
    """
    completo = evidencia.as_dict()
    total = sum(len(completo[k]) for k in ("agreeing", "contradicting",
                                           "exclusive"))
    if limite <= 0:
        return completo, {"entradas": total, "exibidas": total,
                          "truncado": False, "limite": limite,
                          "como_ver_o_resto": _COMO_VER}

    exibido, restante = {}, limite
    for chave in ("contradicting", "agreeing", "exclusive"):
        itens = list(completo[chave].items())[:max(restante, 0)]
        exibido[chave] = dict(itens)
        restante -= len(itens)
    exibido["absent"] = completo["absent"]
    exibidas = sum(len(exibido[k]) for k in ("agreeing", "contradicting",
                                             "exclusive"))
    return exibido, {"entradas": total, "exibidas": exibidas,
                     "truncado": exibidas < total, "limite": limite,
                     "como_ver_o_resto": _COMO_VER}


_COMO_VER = ("--evidence-limit 0 lista todas; a contagem, as flags e a "
             "classificacao nao dependem deste limite")


def assess_group(cur, chave: str, linhas: list[DriverRow],
                 colunas: tuple[str, ...],
                 evidence_limit: int = DEFAULT_EVIDENCE_LIMIT) -> GroupAssessment:
    """One folded name and every row that carries it."""
    linhas = sorted(linhas, key=lambda r: r.driver_id)
    ids = [r.driver_id for r in linhas]

    e = compare_evidence(linhas, colunas)
    classificacao, flags = classify(linhas, e)
    colisoes = result_slot_collisions(cur, ids)
    if colisoes["ids_distintos_no_mesmo_result"]:
        flags.append(FLAG_DISTINCT_IDS_SAME_RESULT)
    if colisoes["id_repetido_no_mesmo_result"]:
        flags.append(FLAG_REPEATED_ID_SAME_RESULT)

    evidencia, meta = _truncate(e, evidence_limit)
    corroborados: set[int] = set()
    for achado in e.agreeing.values():
        corroborados |= set(achado.get("em", []))
    g = GroupAssessment(
        key=chave,
        # Every id, always. A preference is not demonstrated here, and a
        # single id would read as one.
        driver_ids=ids,
        rows=[r.as_dict() for r in linhas],
        spellings=[r.spelling for r in linhas],
        spelling_difference=spelling_difference(linhas),
        created_at=[r.created_at for r in linhas],
        classification=classificacao,
        meaning=MEANING[classificacao],
        flags=sorted(set(flags)),
        evidence=evidencia,
        evidence_meta=meta,
        corroborated_ids=sorted(corroborados),
        external_ids=external_id_state(e),
        references={
            "laps": {r.driver_id: r.laps for r in linhas},
            "result_drivers": {r.driver_id: r.result_drivers for r in linhas},
            "laps_total": sum(r.laps for r in linhas),
            "result_drivers_total": sum(r.result_drivers for r in linhas),
        },
        dependencies={"v_driver_career": career_view_rows(cur, ids),
                      "colisoes_em_result_drivers": colisoes},
        impediments=impediments_of(linhas, e, classificacao, colisoes),
        justification=justification(classificacao, e, flags),
    )
    return g


def assess_all(cur, evidence_limit: int = DEFAULT_EVIDENCE_LIMIT) -> dict:
    """
    Every group of equivalent names, in one pass, for the reconciliation.

    The counts are whatever the groups say. No figure here is adjusted to
    agree with an earlier investigation; a divergence is reported as a
    divergence and explained in the document.
    """
    colunas = evidence_columns(cur)
    todos = group_rows(cur, colunas)
    grupos = {k: v for k, v in todos.items() if len(v) > 1}
    estabilidade = attribute_stability(cur, colunas)

    avaliados = [assess_group(cur, k, v, colunas, evidence_limit)
                 for k, v in sorted(grupos.items())]

    classificacoes: dict[str, int] = {}
    flags: dict[str, int] = {}
    impedimentos: dict[str, int] = {}
    tamanhos: dict[int, int] = {}
    for g in avaliados:
        classificacoes[g.classification] = classificacoes.get(g.classification, 0) + 1
        tamanhos[len(g.driver_ids)] = tamanhos.get(len(g.driver_ids), 0) + 1
        for f in g.flags:
            flags[f] = flags.get(f, 0) + 1
        for i in g.impediments:
            impedimentos[i] = impedimentos.get(i, 0) + 1

    return {
        "drivers_total": sum(len(v) for v in todos.values()),
        "chaves_distintas": len(todos),
        "grupos": len(avaliados),
        "linhas_em_grupos": sum(len(g.driver_ids) for g in avaliados),
        "linhas_absorvidas_se_uma_por_grupo": (
            sum(len(g.driver_ids) for g in avaliados) - len(avaliados)),
        "colunas_de_evidencia": list(colunas),
        "colunas_excluidas": dict(EXCLUDED_COLUMNS),
        "estabilidade_dos_atributos": estabilidade,
        "ponto_cego_da_dobra": fold_blind_spot(todos),
        "agrupamentos_de_variante": variant_groupings(todos, colunas),
        "classificacoes": classificacoes,
        "significados": dict(MEANING),
        "tamanhos_de_grupo": {str(k): v for k, v in sorted(tamanhos.items())},
        "flags": flags,
        "impedimentos": impedimentos,
        "referencias": {
            "laps_em_grupos": sum(g.references["laps_total"] for g in avaliados),
            "result_drivers_em_grupos": sum(
                g.references["result_drivers_total"] for g in avaliados),
        },
        "evidencia": {
            "entradas": sum(g.evidence_meta["entradas"] for g in avaliados),
            "exibidas": sum(g.evidence_meta["exibidas"] for g in avaliados),
            "grupos_truncados": sum(1 for g in avaliados
                                    if g.evidence_meta["truncado"]),
            "limite": evidence_limit,
        },
        "groups": [g.as_dict() for g in avaliados],
    }


# ── Reconciliation with the carrier assessment (PR #58) ──────

# The population of PR #58: three result rows for the entry, exactly two of
# its cars holding laps. Reproduced here rather than imported, so that this
# module's reconciliation does not depend on that module agreeing with it.
CARRIER_PAIRS_SQL = """
WITH portadores AS (
  SELECT DISTINCT l.session_id, c.number, l.car_id
    FROM laps l JOIN cars c ON c.id = l.car_id),
dois AS (SELECT session_id, number FROM portadores
          GROUP BY 1, 2 HAVING count(*) = 2),
tres AS (SELECT r.session_id, c.number
           FROM results r JOIN cars c ON c.id = r.car_id
          GROUP BY 1, 2 HAVING count(*) = 3),
pop AS (SELECT d.session_id, d.number FROM dois d JOIN tres t USING (session_id, number)),
pares AS (SELECT p.session_id, p.number, a.car_id AS car_a, b.car_id AS car_b
            FROM pop p
            JOIN portadores a ON a.session_id = p.session_id AND a.number = p.number
            JOIN portadores b ON b.session_id = p.session_id AND b.number = p.number
                             AND a.car_id < b.car_id)
SELECT la.driver_id, lb.driver_id, count(*)
  FROM pares p
  JOIN laps la ON la.car_id = p.car_a AND la.session_id = p.session_id
  JOIN laps lb ON lb.car_id = p.car_b AND lb.session_id = p.session_id
              AND lb.lap_number = la.lap_number
 WHERE la.driver_id IS DISTINCT FROM lb.driver_id
 GROUP BY 1, 2 ORDER BY 3 DESC, 1, 2
"""


def corroboration_reaches(g: GroupAssessment, ids: list[int]) -> dict:
    """
    Whether the agreeing evidence covers the specific rows in question.

    A group's classification is about the group. It is not about any two of
    its rows, and the difference is not academic: PR #46 §6.1 measured
    `Roman De Angelis`, where three rows share an identifier and the two
    that carry the laps have none. Consolidating what is corroborated would
    leave exactly the rows that collide untouched.

    `ids` are the rows actually implicated — the carriers of the diverging
    laps. `TODOS` means every one of them is covered by at least one
    agreeing field, `PARCIAL` that some are, `NENHUM` that none is.
    """
    cobertos = set(g.corroborated_ids)
    atingidos = sorted(set(ids) & cobertos)
    if not cobertos:
        alcance = "SEM_CORROBORACAO_NO_GRUPO"
    elif len(atingidos) == len(set(ids)):
        alcance = "TODOS"
    elif atingidos:
        alcance = "PARCIAL"
    else:
        alcance = "NENHUM"
    return {"alcance": alcance,
            "ids_corroborados": atingidos,
            "ids_implicados": sorted(set(ids)),
            "o_que_significa": (
                "a classificacao vale para o grupo; a corroboracao pode "
                "estar em linhas que nao carregam as voltas em disputa")}


def carrier_reconciliation(cur, evidence_limit: int = 0) -> dict:
    """
    The lap-level divergences of PR #58, read through this classification.

    Two readings are kept apart deliberately, because collapsing them is the
    error this whole module exists to avoid:

      **Comparable by name** — the two ids fold to one key, so the
      divergence is the duplication of `drivers` showing through. That is
      what can be said.

      **The same person** — not established by the fold, and not by this
      function. A group classified `INSUFFICIENT_EVIDENCE` stays that way
      however many laps hang off it.

    `DRIVER_ID_MISSING` — one side null — is reported separately and is
    **not** addressable by reconciling names: a missing id is an absent
    fact, and no amount of folding supplies it.
    """
    colunas = evidence_columns(cur)
    grupos = group_rows(cur, colunas)
    de_id: dict[int, str] = {}
    for chave, linhas in grupos.items():
        for r in linhas:
            de_id[r.driver_id] = chave

    cur.execute(CARRIER_PAIRS_SQL)
    brutos = cur.fetchall()

    divergentes = [(a, b, n) for a, b, n in brutos
                   if a is not None and b is not None]
    ausentes = [(a, b, n) for a, b, n in brutos
                if (a is None) != (b is None)]

    mesmo, cruzado, fora = [], [], []
    for a, b, n in divergentes:
        ka, kb = de_id.get(a), de_id.get(b)
        if ka is not None and ka == kb:
            mesmo.append((ka, a, b, n))
        elif ka is None or kb is None:
            fora.append((a, b, n))
        else:
            cruzado.append((a, b, n))

    chaves = sorted({k for k, _, _, _ in mesmo})
    por_classe: dict[str, int] = {}
    pares_por_classe: dict[str, int] = {}
    alcanca: dict[str, int] = {}
    detalhe = []
    for chave in chaves:
        g = assess_group(cur, chave, grupos[chave], colunas, evidence_limit)
        voltas = sum(n for k, _, _, n in mesmo if k == chave)
        envolvidos = sorted({x for k, a, b, _ in mesmo if k == chave
                             for x in (a, b)})
        cobertura = corroboration_reaches(g, envolvidos)
        por_classe[g.classification] = por_classe.get(g.classification, 0) + 1
        pares_por_classe[g.classification] = (
            pares_por_classe.get(g.classification, 0) + voltas)
        rotulo = cobertura["alcance"]
        alcanca[rotulo] = alcanca.get(rotulo, 0) + 1
        detalhe.append({"key": chave, "driver_ids": g.driver_ids,
                        "classification": g.classification,
                        "flags": g.flags, "voltas_divergentes": voltas,
                        "ids_que_carregam_as_voltas": envolvidos,
                        "corroboracao_alcanca_os_portadores": cobertura,
                        "impediments": g.impediments})

    return {
        "populacao": ("inscricoes com tres linhas de resultado e exatamente "
                      "dois portadores de voltas (a populacao da PR #58)"),
        "pares_de_volta_com_driver_id_divergente": sum(n for _, _, n in divergentes),
        "pares_de_volta_com_driver_id_ausente_de_um_lado": sum(
            n for _, _, n in ausentes),
        "no_mesmo_grupo_de_nome_equivalente": sum(n for _, _, _, n in mesmo),
        "em_grupos_diferentes": sum(n for _, _, n in cruzado),
        "fora_de_qualquer_grupo": sum(n for _, _, n in fora),
        "grupos_implicados": len(chaves),
        "grupos_implicados_por_classificacao": por_classe,
        "pares_por_classificacao": pares_por_classe,
        "corroboracao_alcanca_os_portadores": alcanca,
        "o_que_isto_nao_diz": (
            "comparavel por nome nao e identidade comprovada: um grupo "
            "INSUFFICIENT_EVIDENCE continua sem evidencia por mais voltas "
            "que carregue. E o driver_id ausente de um lado nao se resolve "
            "normalizando nome algum"),
        "grupos": detalhe,
    }


def assess_one(cur, chave: str,
               evidence_limit: int = DEFAULT_EVIDENCE_LIMIT) -> GroupAssessment:
    """One group, by its folded key or by any spelling that folds to it."""
    colunas = evidence_columns(cur)
    grupos = group_rows(cur, colunas)
    alvo = chave if chave in grupos else full_name_key(chave, "")
    if alvo not in grupos:
        raise DriverIdentityError(f"nenhuma linha de drivers dobra para {chave!r}")
    if len(grupos[alvo]) < 2:
        # Classifying one row would answer a question that was not asked.
        # With a single row nothing can agree or contradict, so the labels
        # would describe the absence of a second row rather than the
        # evidence about a person.
        raise DriverIdentityError(
            f"{alvo!r} tem uma unica linha de drivers "
            f"(id {grupos[alvo][0].driver_id}); nao e um grupo de nomes "
            f"equivalentes e nao ha o que classificar")
    return assess_group(cur, alvo, grupos[alvo], colunas, evidence_limit)


# ── CLI ──────────────────────────────────────────────────────

def read_only_connection(cfg: dict):
    import psycopg2
    return psycopg2.connect(connect_timeout=5,
                            options="-c default_transaction_read_only=on",
                            **cfg)


def write_report(dados: dict, destino: Path | None) -> int:
    """
    The only write, and it happens after the connection is closed.

    A destination that cannot be written changes the exit code and nothing
    else; the database has already been left as it was found.
    """
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
    ap.add_argument("--key", help="one group, by folded key or by a spelling")
    ap.add_argument("--all", action="store_true",
                    help="every group of equivalent names")
    ap.add_argument("--reconcile-carriers", action="store_true",
                    help="the lap divergences of PR #58, read through this "
                         "classification")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5433)
    ap.add_argument("--dbname", default="openwec")
    ap.add_argument("--user", default="openwec")
    ap.add_argument("--password", default="")
    ap.add_argument("--evidence-limit", type=int, default=DEFAULT_EVIDENCE_LIMIT,
                    help="how many evidence entries to list; 0 lists every "
                         "one. It never changes a count or a classification")
    ap.add_argument("--json", type=Path,
                    help="write the report here; a scratch path outside the "
                         "repository and outside raw/ is the recommendation")
    args = ap.parse_args()

    if not args.all and not args.key and not args.reconcile_carriers:
        ap.error("informe --key, --all ou --reconcile-carriers")

    cfg = {"host": args.host, "port": args.port, "dbname": args.dbname,
           "user": args.user, "password": args.password}
    conn = read_only_connection(cfg)
    try:
        with conn.cursor() as cur:
            if args.reconcile_carriers:
                dados = carrier_reconciliation(cur, args.evidence_limit)
                grupos = []
            elif args.all:
                dados = assess_all(cur, args.evidence_limit)
                grupos = []
            else:
                dados = assess_one(cur, args.key, args.evidence_limit).as_dict()
                grupos = [dados]
    except DriverIdentityError as exc:
        conn.rollback()
        conn.close()
        print(f"LEITURA FALHOU: {exc}")
        return 2
    finally:
        if not conn.closed:
            conn.rollback()
            conn.close()
    # No database from here on.

    if args.reconcile_carriers:
        print(f"pares de volta com driver_id divergente: "
              f"{dados['pares_de_volta_com_driver_id_divergente']}")
        print(f"  no mesmo grupo de nome equivalente: "
              f"{dados['no_mesmo_grupo_de_nome_equivalente']}")
        print(f"  em grupos diferentes:               "
              f"{dados['em_grupos_diferentes']}")
        print(f"  fora de qualquer grupo:             "
              f"{dados['fora_de_qualquer_grupo']}")
        print(f"pares com driver_id ausente de um lado: "
              f"{dados['pares_de_volta_com_driver_id_ausente_de_um_lado']}")
        print(f"grupos implicados: {dados['grupos_implicados']}")
        print(f"  por classificacao: {dados['grupos_implicados_por_classificacao']}")
        print(f"  voltas por classificacao: {dados['pares_por_classificacao']}")
        print(f"  a corroboracao alcanca os portadores? "
              f"{dados['corroboracao_alcanca_os_portadores']}")
        print(f"ATENCAO: {dados['o_que_isto_nao_diz']}")

    if args.all:
        print(f"drivers: {dados['drivers_total']}  "
              f"grupos: {dados['grupos']}  "
              f"linhas em grupos: {dados['linhas_em_grupos']}")
        print(f"  classificacoes: {dados['classificacoes']}")
        print(f"  tamanhos: {dados['tamanhos_de_grupo']}")
        print(f"  referencias: {dados['referencias']}")
        cego = dados["ponto_cego_da_dobra"]
        print(f"  ponto cego da dobra: "
              f"{cego['chaves_alternativas']} chaves alternativas, das quais "
              f"{cego['chaves_alternativas_sem_efeito_no_agrupamento']} nao "
              f"mudam agrupamento")
        print(f"    agrupamentos alterados: {cego['agrupamentos_alterados']}  "
              f"{cego['agrupamentos_alterados_por_estrutura']}")
        print(f"    envolvem {cego['registros_envolvidos']} registros, "
              f"{cego['voltas_envolvidas']} voltas, "
              f"{cego['result_drivers_envolvidos']} result_drivers")
        print(f"    {cego['reconciliacao']}")
        print(f"  impedimentos: {dados['impedimentos']}")
        print("  flags: " + "  ".join(
            f"{k}={v}" for k, v in sorted(dados["flags"].items())))

    for g in grupos:
        print(f"{g['key']}: {g['classification']}  "
              f"[{g['spelling_difference']}]")
        print(f"  driver_ids: {g['driver_ids']}  (nenhum canonico escolhido)")
        print(f"  grafias: {g['spellings']}")
        if g["flags"]:
            print("  flags: " + ", ".join(g["flags"]))
        print(f"  externos: {g['external_ids']}")
        print(f"  referencias: laps={g['references']['laps_total']}  "
              f"result_drivers={g['references']['result_drivers_total']}")
        if g["impediments"]:
            print("  impedimentos: " + ", ".join(g["impediments"]))
        print(f"  justificativa: {g['justification']}")

    return write_report(dados, args.json)


if __name__ == "__main__":
    raise SystemExit(main())
