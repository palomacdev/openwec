"""
OpenWEC — when one entry's laps hang off two `cars` rows, are they the same laps?

PR #57 blocks an entry whose laps are carried by more than one `car_id`,
because no canonical row can be chosen there. That refusal stands and this
module does not touch it. What it adds is the evidence the refusal was made
without: whether the two carriers hold **the same laps**, and what else
distinguishes them.

Two questions are kept apart on purpose, and only the first is answered here.

**Equivalence** — do the two carriers represent the same set of laps? That
is a question about lap rows, and it has an answer in the data.

**Canonical selection** — is there enough evidence to prefer one carrier
over the other without losing information or changing meaning? **This module
does not answer it.** It returns `candidate_car_ids` with *both* carriers in
it and a list of impediments; it never returns a single id. A lower id, an
earlier `created_at`, more metadata or more references are **not** evidence
of correctness, and nothing here treats them as such.

**No result of this module authorises deleting a row or repointing a
reference.**

## The lap identity, and why that key

Laps are paired on `(session_id, lap_number)` within the entry. The key is
not assumed: `laps` carries `UNIQUE (session_id, car_id, lap_number)`, so a
carrier cannot hold the same lap number twice, which is what makes the
pairing one-to-one. The module **verifies that at run time** rather than
trusting it — a carrier that somehow holds a repeated number yields
`UNDETERMINED`, and no row is picked to stand for the others.

Nothing here uses `DISTINCT ON` or `LIMIT`. Every lap row of both carriers
is read and accounted for, and the counts in the report reconcile with the
rows compared.

## Which columns are compared

The comparison set is **discovered from the catalogue**, not written out by
hand, so a column added to `laps` later joins the sporting comparison
instead of being skipped in silence. Four columns are excluded and each for
a stated reason; `driver_id` is compared, but on its own.

## Read only

Every statement is a `SELECT`. The CLI opens its connection with
`default_transaction_read_only`, and the report — the one thing that writes
— is produced after the connection is closed, to a path the caller names,
outside the repository and outside `raw/`.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "loader"))

from row_identity import full_name_key  # noqa: E402

# ── Population ───────────────────────────────────────────────

OUTSIDE_ASSESSMENT_CLASS = "OUTSIDE_ASSESSMENT_CLASS"

ROWS_PER_ENTRY = 3
CARRIERS_EXPECTED = 2

# ── Pair categories ──────────────────────────────────────────

EXACT_DUPLICATE = "EXACT_DUPLICATE"
DRIVER_ID_DIVERGENCE = "DRIVER_ID_DIVERGENCE"
DRIVER_ID_MISSING = "DRIVER_ID_MISSING"
SPORTING_CONFLICT = "SPORTING_CONFLICT"
UNMATCHED_LAP = "UNMATCHED_LAP"
UNDETERMINED = "UNDETERMINED"

PAIR_CATEGORIES = (EXACT_DUPLICATE, DRIVER_ID_DIVERGENCE, DRIVER_ID_MISSING,
                   SPORTING_CONFLICT, UNMATCHED_LAP, UNDETERMINED)

# ── Equivalence ──────────────────────────────────────────────

EXACT_EQUIVALENCE = "EXACT_EQUIVALENCE"
SPORTING_EQUIVALENCE_WITH_IDENTITY_AMBIGUITY = (
    "SPORTING_EQUIVALENCE_WITH_IDENTITY_AMBIGUITY")
NON_EQUIVALENT = "NON_EQUIVALENT"
UNDETERMINED_EQUIVALENCE = "UNDETERMINED"

# ── Operational verdict ──────────────────────────────────────

CANDIDATE = "CANDIDATE"
REQUIRES_SPECIAL_HANDLING = "REQUIRES_SPECIAL_HANDLING"
BLOCKED = "BLOCKED"

SEVERITY = {CANDIDATE: 0, REQUIRES_SPECIAL_HANDLING: 1, BLOCKED: 2}

# ── Impediments to a future consolidation ────────────────────

IMPEDIMENT_DRIVER_IDENTITY = "DRIVER_IDENTITY_UNRESOLVED"
IMPEDIMENT_EXCLUSIVE_METADATA = "EXCLUSIVE_METADATA_ON_A_CARRIER"
IMPEDIMENT_RESULT_DRIVERS_DIFFER = "RESULT_DRIVERS_DIFFER"
IMPEDIMENT_RESULT_MATERIAL_DIFFERENCE = "RESULT_MATERIAL_DIFFERENCE"
IMPEDIMENT_RESULT_EXCLUSIVE_INFORMATION = "RESULT_EXCLUSIVE_INFORMATION"
IMPEDIMENT_ANALYTICS_WITHOUT_FK = "ANALYTICS_REFERENCES_WITHOUT_FK"
IMPEDIMENT_ANALYTICS_ASYMMETRIC = "ANALYTICS_PRESENT_ON_ONE_CARRIER_ONLY"

# ── Columns ──────────────────────────────────────────────────

# Why each is left out of the comparison. A column not named here joins the
# sporting set automatically, which is the safe default: a new column makes
# two laps differ rather than silently agreeing.
EXCLUDED_COLUMNS = {
    "id": "surrogate key, different by construction for any two rows",
    "session_id": "the join key; equal for both carriers by selection",
    "car_id": "the thing being compared, not an attribute of the lap",
    "lap_number": "the lap's identity within the entry, not an attribute",
    "created_at": "bookkeeping of the ingestion, not a fact about the lap",
}
DRIVER_COLUMN = "driver_id"


@dataclass
class LapColumns:
    sporting: tuple[str, ...]
    driver: str = DRIVER_COLUMN
    excluded: dict = dataclass_field(default_factory=lambda: dict(EXCLUDED_COLUMNS))

    @property
    def all_read(self) -> tuple[str, ...]:
        return ("lap_number", self.driver, *self.sporting)


def lap_columns(cur) -> LapColumns:
    """
    The comparison set, taken from the catalogue rather than written by hand.

    A list in the source is a list that goes stale: a column added to `laps`
    would be skipped, and two laps that differ in it would be reported
    identical. Discovery makes the default the other way round.
    """
    cur.execute("""
        SELECT column_name FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'laps'
         ORDER BY ordinal_position
    """)
    todas = [linha[0] for linha in cur.fetchall()]
    if not todas:
        raise CarrierAssessmentError("a tabela laps nao foi encontrada")
    esportivas = tuple(c for c in todas
                       if c not in EXCLUDED_COLUMNS and c != DRIVER_COLUMN)
    return LapColumns(sporting=esportivas)


class CarrierAssessmentError(Exception):
    """A read failed. Nothing is written, and nothing is called equivalent."""


# Each carrier has its own row in `results`, and those rows are not the
# laps: a carrier whose laps match the other's may still hold a different
# position, status or provenance. Discovered from the catalogue for the same
# reason the lap columns are — a column added later must join the comparison
# rather than be skipped.
RESULT_EXCLUDED_COLUMNS = {
    "id": "surrogate key, different by construction for any two rows",
    "session_id": "the join key; equal for both carriers by selection",
    "car_id": "the carrier itself, not an attribute of the result",
    "created_at": "bookkeeping of the ingestion, not a fact about the result",
}


def result_columns(cur) -> tuple[str, ...]:
    """The `results` columns that take part in the comparison."""
    cur.execute("""
        SELECT column_name FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'results'
         ORDER BY ordinal_position
    """)
    todas = [linha[0] for linha in cur.fetchall()]
    if not todas:
        raise CarrierAssessmentError("a tabela results nao foi encontrada")
    return tuple(c for c in todas if c not in RESULT_EXCLUDED_COLUMNS)


def compare_results(a: dict, b: dict, colunas: tuple[str, ...]) -> dict:
    """
    The two `results` rows, with contradiction told apart from silence.

    A **material difference** is both sides stating something and stating
    different things — a position, a status, a lap count, a source URL. Only
    one of the two can describe the entry, and this module has no basis for
    choosing.

    **Exclusive information** is one side holding a value the other leaves
    NULL. Nothing contradicts, but discarding that carrier would lose it,
    which is a potential loss and not an equivalence.

    Neither is mixed with the lap comparison. Equal laps and differing
    results are a perfectly possible combination, and the report says so.
    """
    materiais: dict = {}
    exclusivos: dict = {}
    for coluna in colunas:
        va, vb = a.get(coluna), b.get(coluna)
        if va is None and vb is None:
            continue
        if va is not None and vb is not None:
            if va != vb:
                materiais[coluna] = {"a": str(va), "b": str(vb)}
        else:
            exclusivos[coluna] = {
                "valor": str(va if va is not None else vb),
                "somente_em": "a" if va is not None else "b"}
    return {"materiais": materiais, "exclusivos": exclusivos,
            "colunas_comparadas": list(colunas),
            "colunas_excluidas": dict(RESULT_EXCLUDED_COLUMNS)}


# ── Reading ──────────────────────────────────────────────────

def carrier_lap_rows(cur, session_id: int, car_id: int,
                     colunas: LapColumns) -> list[dict]:
    """
    Every lap row of this carrier. No `DISTINCT ON`, no `LIMIT`, no ordering
    that hides anything: the caller has to see repetition if it exists.
    """
    campos = ", ".join(f"l.{c}" for c in colunas.all_read)
    cur.execute(f"""
        SELECT {campos} FROM laps l
         WHERE l.session_id = %s AND l.car_id = %s
         ORDER BY l.lap_number, l.id
    """, (session_id, car_id))
    return [dict(zip(colunas.all_read, linha)) for linha in cur.fetchall()]


def lap_carriers(cur, session_id: int, number: str) -> dict[int, int]:
    """The `car_id` of this entry that hold laps, and how many each holds."""
    cur.execute("""
        SELECT r.car_id,
               (SELECT count(*) FROM laps l
                 WHERE l.session_id = r.session_id AND l.car_id = r.car_id)
          FROM results r JOIN cars c ON c.id = r.car_id
         WHERE r.session_id = %s AND c.number = %s
         ORDER BY r.car_id
    """, (session_id, number))
    return {car_id: voltas for car_id, voltas in cur.fetchall()}


# ── Population ───────────────────────────────────────────────

@dataclass
class Population:
    session_id: int
    number: str
    result_rows: int
    car_ids: list[int]
    carriers: list[int]
    valid: bool
    reason: str | None = None

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def population(cur, session_id: int, number: str) -> Population:
    """
    Whether this entry is one the assessment was derived for.

    The criteria below come from the 225 entries investigated: three result
    rows, exactly two of their cars holding laps. An entry with three
    carriers, or with a result count the triplication class does not have,
    is **not** answered by extension — the evidence for it was never
    gathered.
    """
    por_carro = lap_carriers(cur, session_id, number)
    ids = sorted(por_carro)
    portadores = sorted(c for c, n in por_carro.items() if n > 0)
    p = Population(session_id=session_id, number=number, result_rows=len(ids),
                   car_ids=ids, carriers=portadores, valid=False)

    if len(ids) != ROWS_PER_ENTRY:
        p.reason = (f"a inscricao tem {len(ids)} linhas de resultado, nao "
                    f"{ROWS_PER_ENTRY}")
        return p
    if len(portadores) != CARRIERS_EXPECTED:
        p.reason = (f"a inscricao tem {len(portadores)} portadores de voltas, "
                    f"nao {CARRIERS_EXPECTED}")
        return p

    p.valid = True
    return p


# ── Comparing the laps ───────────────────────────────────────

@dataclass
class PairComparison:
    lap_number: int | None
    category: str
    evidence: dict = dataclass_field(default_factory=dict)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def index_by_lap_number(linhas: list[dict]) -> tuple[dict, list[int]]:
    """
    One row per lap number, and the numbers that have more than one.

    `UNIQUE (session_id, car_id, lap_number)` says the second list is always
    empty. It is built anyway: a constraint is a statement about the
    database, and this routine's job is to check rather than to quote it.
    """
    indice: dict[int, list[dict]] = {}
    for linha in linhas:
        indice.setdefault(linha["lap_number"], []).append(linha)
    repetidos = sorted(n for n, rows in indice.items() if len(rows) > 1)
    return indice, repetidos


def compare_lap(a: dict, b: dict, colunas: LapColumns) -> PairComparison:
    """
    One lap on both carriers, placed in exactly one category.

    Sporting attributes are weighed first, because a disagreement there is a
    disagreement about what happened on track. `driver_id` is weighed after
    and on its own: two different ids may be two renderings of one person, so
    a divergence there is reported as an identity question, not as a conflict
    of fact.
    """
    conflitos = {}
    for coluna in colunas.sporting:
        va, vb = a[coluna], b[coluna]
        if va is not None and vb is not None and va != vb:
            conflitos[coluna] = {"a": str(va), "b": str(vb)}
        elif (va is None) != (vb is None):
            conflitos.setdefault("_ausentes", {})[coluna] = {
                "a": None if va is None else str(va),
                "b": None if vb is None else str(vb)}

    materiais = {k: v for k, v in conflitos.items() if k != "_ausentes"}
    if materiais:
        return PairComparison(a["lap_number"], SPORTING_CONFLICT,
                              {"colunas": materiais})

    ausentes = conflitos.get("_ausentes")
    if ausentes:
        # One side holds a sporting value the other does not. Not a conflict
        # of fact, and not an identity question either: it is a gap, and the
        # module does not decide gaps.
        return PairComparison(a["lap_number"], UNDETERMINED,
                              {"atributo_esportivo_ausente_de_um_lado": ausentes})

    da, db = a[colunas.driver], b[colunas.driver]
    if da is None and db is None:
        return PairComparison(a["lap_number"], EXACT_DUPLICATE)
    if (da is None) != (db is None):
        return PairComparison(a["lap_number"], DRIVER_ID_MISSING,
                              {"driver_id": {"a": da, "b": db}})
    if da != db:
        return PairComparison(a["lap_number"], DRIVER_ID_DIVERGENCE,
                              {"driver_id": {"a": da, "b": db}})
    return PairComparison(a["lap_number"], EXACT_DUPLICATE)


@dataclass
class Coverage:
    rows_a: int = 0
    rows_b: int = 0
    distinct_a: int = 0
    distinct_b: int = 0
    repeated_a: list[int] = dataclass_field(default_factory=list)
    repeated_b: list[int] = dataclass_field(default_factory=list)
    range_a: list | None = None
    range_b: list | None = None
    gaps_a: int = 0
    gaps_b: int = 0
    shared: int = 0
    only_a: list[int] = dataclass_field(default_factory=list)
    only_b: list[int] = dataclass_field(default_factory=list)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def gaps(numeros: set[int]) -> int:
    if not numeros:
        return 0
    return max(numeros) - min(numeros) + 1 - len(numeros)


def compare_carriers(linhas_a: list[dict], linhas_b: list[dict],
                     colunas: LapColumns) -> tuple[list[PairComparison], Coverage]:
    """
    Every lap of both carriers, each accounted for exactly once.

    A number repeated inside one carrier is not resolved by choosing a row:
    the whole entry becomes `UNDETERMINED`, because any choice would be this
    module inventing the answer it was asked to find.
    """
    indice_a, repetidos_a = index_by_lap_number(linhas_a)
    indice_b, repetidos_b = index_by_lap_number(linhas_b)

    c = Coverage(
        rows_a=len(linhas_a), rows_b=len(linhas_b),
        distinct_a=len(indice_a), distinct_b=len(indice_b),
        repeated_a=repetidos_a, repeated_b=repetidos_b,
        range_a=[min(indice_a), max(indice_a)] if indice_a else None,
        range_b=[min(indice_b), max(indice_b)] if indice_b else None,
        gaps_a=gaps(set(indice_a)), gaps_b=gaps(set(indice_b)),
        shared=len(set(indice_a) & set(indice_b)),
        only_a=sorted(set(indice_a) - set(indice_b)),
        only_b=sorted(set(indice_b) - set(indice_a)),
    )

    if repetidos_a or repetidos_b:
        return [PairComparison(None, UNDETERMINED, {
            "numero_de_volta_repetido_no_mesmo_portador": {
                "a": repetidos_a, "b": repetidos_b}})], c

    pares: list[PairComparison] = []
    for numero in sorted(set(indice_a) | set(indice_b)):
        em_a, em_b = indice_a.get(numero), indice_b.get(numero)
        if em_a is None or em_b is None:
            pares.append(PairComparison(numero, UNMATCHED_LAP, {
                "presente_em": "b" if em_a is None else "a"}))
            continue
        pares.append(compare_lap(em_a[0], em_b[0], colunas))
    return pares, c


# ── The carriers as whole records ────────────────────────────

CAR_ATTRIBUTES = ("number", "team_id", "vehicle", "manufacturer", "car_class",
                  "car_group", "tires", "imsa_car_id", "imsa_team_id",
                  "tyre_supplier")


@dataclass
class CarrierProfile:
    car_id: int
    car: dict = dataclass_field(default_factory=dict)
    created_at: str | None = None
    result_id: int | None = None
    result: dict = dataclass_field(default_factory=dict)
    result_drivers: list = dataclass_field(default_factory=list)
    laps: int = 0
    analytics_car_session: int = 0
    analytics_stints: int = 0

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def carrier_profile(cur, session_id: int, car_id: int,
                    colunas_de_result: tuple[str, ...] = ()) -> CarrierProfile:
    """
    Everything that hangs off this carrier, not only its laps.

    Equal laps do not make two `cars` rows interchangeable. The attributes
    one of them carries and the other does not, and the rows that point at
    each, are what a consolidation would have to answer for.
    """
    campos = ", ".join(f"c.{c}" for c in CAR_ATTRIBUTES)
    de_result = "".join(f", r.{c}::text" for c in colunas_de_result)
    cur.execute(f"""
        SELECT {campos}, c.created_at::text, r.id{de_result}
          FROM cars c LEFT JOIN results r
            ON r.car_id = c.id AND r.session_id = %s
         WHERE c.id = %s
    """, (session_id, car_id))
    linha = cur.fetchone()
    if not linha:
        raise CarrierAssessmentError(f"o carro {car_id} nao existe")

    corte = len(CAR_ATTRIBUTES)
    p = CarrierProfile(car_id=car_id,
                       car=dict(zip(CAR_ATTRIBUTES, linha[:corte])),
                       created_at=linha[corte],
                       result_id=linha[corte + 1],
                       result=dict(zip(colunas_de_result, linha[corte + 2:])))

    cur.execute("""
        SELECT rd.slot, d.id, d.first_name, d.last_name
          FROM result_drivers rd JOIN drivers d ON d.id = rd.driver_id
         WHERE rd.result_id = %s ORDER BY rd.slot
    """, (p.result_id,))
    p.result_drivers = [{"slot": s, "driver_id": i, "nome": f"{f} {l}".strip()}
                        for s, i, f, l in cur.fetchall()]

    cur.execute("""
        SELECT (SELECT count(*) FROM laps l
                 WHERE l.session_id = %(s)s AND l.car_id = %(c)s),
               (SELECT count(*) FROM analytics_car_session a
                 WHERE a.session_id = %(s)s AND a.car_id = %(c)s),
               (SELECT count(*) FROM analytics_stints a
                 WHERE a.session_id = %(s)s AND a.car_id = %(c)s)
    """, {"s": session_id, "c": car_id})
    p.laps, p.analytics_car_session, p.analytics_stints = cur.fetchone()
    return p


def exclusive_metadata(a: CarrierProfile, b: CarrierProfile) -> dict:
    """Attributes one carrier holds and the other leaves empty."""
    achados: dict = {}
    for coluna in CAR_ATTRIBUTES:
        va, vb = a.car.get(coluna), b.car.get(coluna)
        if (va is None) != (vb is None):
            dono = a.car_id if va is not None else b.car_id
            achados[coluna] = {"valor": va if va is not None else vb,
                               "somente_no_car_id": dono}
    return achados


def driver_identity_note(a: CarrierProfile, b: CarrierProfile,
                         pares: list[PairComparison]) -> dict | None:
    """
    The driver ids the two carriers disagree on, and whether the names fold
    to one.

    `full_name_key` is the fold `row_identity` already uses, borrowed to say
    a pair is **worth looking at**. Two names that fold alike is not proof of
    one person, and nothing here merges anything on that basis.
    """
    ids = set()
    for p in pares:
        if p.category in (DRIVER_ID_DIVERGENCE, DRIVER_ID_MISSING):
            d = p.evidence.get("driver_id", {})
            ids.add((d.get("a"), d.get("b")))
    nos_resultados = {(x["driver_id"], x["nome"]) for x in a.result_drivers} ^ \
                     {(x["driver_id"], x["nome"]) for x in b.result_drivers}
    if not ids and not nos_resultados:
        return None
    return {"pares_nas_voltas": sorted(str(p) for p in ids),
            "divergencia_em_result_drivers": sorted(str(x) for x in nos_resultados)}


def names_fold_alike(cur, driver_a: int | None, driver_b: int | None) -> bool | None:
    """Whether two driver rows carry the same name once folded. Never a merge."""
    if driver_a is None or driver_b is None:
        return None
    cur.execute("SELECT id, first_name, last_name FROM drivers WHERE id = ANY(%s)",
                ([driver_a, driver_b],))
    linhas = {i: (f, l) for i, f, l in cur.fetchall()}
    if len(linhas) != 2:
        return None
    (fa, la), (fb, lb) = linhas[driver_a], linhas[driver_b]
    return full_name_key(fa, la) == full_name_key(fb, lb)


# ── The entry ────────────────────────────────────────────────

@dataclass
class EntryAssessment:
    session_id: int
    number: str
    population: dict = dataclass_field(default_factory=dict)
    columns_compared: dict = dataclass_field(default_factory=dict)
    coverage: dict = dataclass_field(default_factory=dict)
    pair_counts: dict = dataclass_field(default_factory=dict)
    pair_evidence: list = dataclass_field(default_factory=list)
    pair_evidence_meta: dict = dataclass_field(default_factory=dict)
    carriers: list = dataclass_field(default_factory=list)
    equivalence: str = UNDETERMINED_EQUIVALENCE
    verdict: str = BLOCKED
    impediments: list = dataclass_field(default_factory=list)
    candidate_car_ids: list = dataclass_field(default_factory=list)
    notes: dict = dataclass_field(default_factory=dict)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def equivalence_of(contagens: dict) -> str:
    """
    What the pair categories say about the two sets of laps, and only that.

    The order is the order of severity: anything undetermined or unmatched
    stops the question being answerable, a sporting conflict answers it
    negatively, and the identity categories answer it positively about the
    sport while leaving the identity open.
    """
    if contagens.get(UNDETERMINED) or contagens.get(UNMATCHED_LAP):
        return UNDETERMINED_EQUIVALENCE
    if contagens.get(SPORTING_CONFLICT):
        return NON_EQUIVALENT
    if not contagens.get(EXACT_DUPLICATE) and not contagens.get(DRIVER_ID_DIVERGENCE) \
            and not contagens.get(DRIVER_ID_MISSING):
        return UNDETERMINED_EQUIVALENCE
    if contagens.get(DRIVER_ID_DIVERGENCE) or contagens.get(DRIVER_ID_MISSING):
        return SPORTING_EQUIVALENCE_WITH_IDENTITY_AMBIGUITY
    return EXACT_EQUIVALENCE


def verdict_of(equivalencia: str, impedimentos: list[str]) -> str:
    """
    The operational reading, which is about evidence and never about writing.

    `CANDIDATE` says the laps were shown to be the same and nothing known
    stands in the way of studying a consolidation. It does not say a
    consolidation is allowed, and no caller of this module may treat it that
    way.
    """
    if equivalencia in (NON_EQUIVALENT, UNDETERMINED_EQUIVALENCE):
        return BLOCKED
    if equivalencia == SPORTING_EQUIVALENCE_WITH_IDENTITY_AMBIGUITY or impedimentos:
        return REQUIRES_SPECIAL_HANDLING
    return CANDIDATE


DEFAULT_EVIDENCE_LIMIT = 20


def assess_entry(cur, session_id: int, number: str,
                 colunas: LapColumns | None = None,
                 colunas_de_result: tuple[str, ...] | None = None,
                 evidence_limit: int = DEFAULT_EVIDENCE_LIMIT) -> EntryAssessment:
    """
    One entry of the triplication class, read and reported.

    `evidence_limit` caps how many diverging pairs are listed, never how
    many are counted or how the entry is classified. Zero lists every one of
    them, and the report always says how many there were and whether the
    list was cut.
    """
    e = EntryAssessment(session_id=session_id, number=number)

    pop = population(cur, session_id, number)
    e.population = pop.as_dict()
    if not pop.valid:
        e.verdict = BLOCKED
        e.equivalence = UNDETERMINED_EQUIVALENCE
        e.impediments = [OUTSIDE_ASSESSMENT_CLASS]
        e.notes["motivo"] = pop.reason
        return e

    colunas = colunas or lap_columns(cur)
    colunas_de_result = (result_columns(cur) if colunas_de_result is None
                         else colunas_de_result)
    e.columns_compared = {
        "laps": {"esportivas": list(colunas.sporting),
                 "identidade": colunas.driver,
                 "excluidas": colunas.excluded},
        "results": {"comparadas": list(colunas_de_result),
                    "excluidas": dict(RESULT_EXCLUDED_COLUMNS)},
    }

    a_id, b_id = pop.carriers
    pares, cobertura = compare_carriers(
        carrier_lap_rows(cur, session_id, a_id, colunas),
        carrier_lap_rows(cur, session_id, b_id, colunas),
        colunas)

    e.coverage = cobertura.as_dict()
    e.pair_counts = {c: sum(1 for p in pares if p.category == c)
                     for c in PAIR_CATEGORIES}
    e.pair_counts["total"] = len(pares)
    divergentes = [p.as_dict() for p in pares if p.category != EXACT_DUPLICATE]
    mostrados = divergentes if evidence_limit <= 0 else divergentes[:evidence_limit]
    e.pair_evidence = mostrados
    e.pair_evidence_meta = {
        "divergencias": len(divergentes),
        "exibidas": len(mostrados),
        "truncado": len(mostrados) < len(divergentes),
        "limite": evidence_limit,
        "como_ver_o_resto": ("--evidence-limit 0 lista todas; a contagem e a "
                             "classificacao nao dependem deste limite"),
    }

    perfis = [carrier_profile(cur, session_id, a_id, colunas_de_result),
              carrier_profile(cur, session_id, b_id, colunas_de_result)]
    e.carriers = [p.as_dict() for p in perfis]

    e.equivalence = equivalence_of(e.pair_counts)

    impedimentos = []
    if e.pair_counts[DRIVER_ID_DIVERGENCE] or e.pair_counts[DRIVER_ID_MISSING]:
        impedimentos.append(IMPEDIMENT_DRIVER_IDENTITY)
    exclusivos = exclusive_metadata(*perfis)
    if exclusivos:
        impedimentos.append(IMPEDIMENT_EXCLUSIVE_METADATA)
        e.notes["metadado_exclusivo"] = exclusivos
    if {(d["driver_id"], d["slot"]) for d in perfis[0].result_drivers} != \
       {(d["driver_id"], d["slot"]) for d in perfis[1].result_drivers}:
        impedimentos.append(IMPEDIMENT_RESULT_DRIVERS_DIFFER)

    # Os `results` dos dois portadores, comparados a parte das voltas.
    comparacao = compare_results(perfis[0].result, perfis[1].result,
                                 colunas_de_result)
    e.notes["comparacao_de_results"] = comparacao
    if comparacao["materiais"]:
        impedimentos.append(IMPEDIMENT_RESULT_MATERIAL_DIFFERENCE)
    if comparacao["exclusivos"]:
        impedimentos.append(IMPEDIMENT_RESULT_EXCLUSIVE_INFORMATION)

    if perfis[0].analytics_car_session or perfis[1].analytics_car_session:
        impedimentos.append(IMPEDIMENT_ANALYTICS_WITHOUT_FK)
        if bool(perfis[0].analytics_car_session) != bool(perfis[1].analytics_car_session):
            impedimentos.append(IMPEDIMENT_ANALYTICS_ASYMMETRIC)

    nota = driver_identity_note(perfis[0], perfis[1], pares)
    if nota:
        e.notes["identidade_de_piloto"] = nota

    e.impediments = impedimentos
    e.verdict = verdict_of(e.equivalence, impedimentos)
    # Both, always. A preference is not demonstrated here, and a single id
    # would read as one.
    e.candidate_car_ids = sorted(pop.carriers)
    return e


CLASS_ENTRIES_SQL = """
WITH g AS (SELECT r.session_id, c.number, count(*) AS n
             FROM results r JOIN cars c ON c.id = r.car_id GROUP BY 1, 2),
classe AS (SELECT session_id FROM g GROUP BY session_id
            HAVING min(n) = 3 AND max(n) = 3),
rc AS (SELECT r.session_id, c.number, r.car_id
         FROM results r JOIN cars c ON c.id = r.car_id
        WHERE r.session_id IN (SELECT session_id FROM classe)),
carga AS (SELECT rc.*, (SELECT count(*) FROM laps l
                         WHERE l.session_id = rc.session_id
                           AND l.car_id = rc.car_id) AS v FROM rc)
SELECT session_id, number
  FROM carga GROUP BY session_id, number
HAVING count(*) FILTER (WHERE v > 0) > 1
 ORDER BY session_id, number
"""


def class_entries(cur) -> list[tuple[int, str]]:
    """Every multi-carrier entry of the triplication class, in order."""
    cur.execute(CLASS_ENTRIES_SQL)
    return [(s, n) for s, n in cur.fetchall()]


def assess_class(cur, evidence_limit: int = DEFAULT_EVIDENCE_LIMIT) -> dict:
    """
    The whole population, for the reconciliation.

    One pass, one report, and the counts are whatever the entries say. No
    figure here is adjusted to agree with an earlier investigation.
    """
    colunas = lap_columns(cur)
    de_result = result_columns(cur)
    entradas = [assess_entry(cur, s, n, colunas, de_result, evidence_limit)
                for s, n in class_entries(cur)]

    pares = {c: sum(e.pair_counts.get(c, 0) for e in entradas)
             for c in PAIR_CATEGORIES}
    pares["total"] = sum(e.pair_counts.get("total", 0) for e in entradas)

    vereditos: dict[str, int] = {}
    equivalencias: dict[str, int] = {}
    impedimentos: dict[str, int] = {}
    sessoes: dict[str, set] = {}
    for e in entradas:
        vereditos[e.verdict] = vereditos.get(e.verdict, 0) + 1
        equivalencias[e.equivalence] = equivalencias.get(e.equivalence, 0) + 1
        sessoes.setdefault(e.verdict, set()).add(e.session_id)
        for i in e.impediments:
            impedimentos[i] = impedimentos.get(i, 0) + 1

    pior: dict[int, str] = {}
    for e in entradas:
        anterior = pior.get(e.session_id)
        if anterior is None or SEVERITY[e.verdict] > SEVERITY[anterior]:
            pior[e.session_id] = e.verdict
    por_sessao: dict[str, int] = {}
    for v in pior.values():
        por_sessao[v] = por_sessao.get(v, 0) + 1

    return {
        "entradas": len(entradas),
        "sessoes": len({e.session_id for e in entradas}),
        "pares": pares,
        "evidencia": {
            "divergencias": sum(e.pair_evidence_meta["divergencias"]
                                for e in entradas),
            "exibidas": sum(e.pair_evidence_meta["exibidas"] for e in entradas),
            "entradas_truncadas": sum(1 for e in entradas
                                      if e.pair_evidence_meta["truncado"]),
            "limite": evidence_limit,
        },
        "vereditos_por_entrada": vereditos,
        "vereditos_por_sessao": por_sessao,
        "sessoes_tocadas_por_veredito": {k: len(v) for k, v in sessoes.items()},
        "equivalencias": equivalencias,
        "impedimentos": impedimentos,
        "entries": [e.as_dict() for e in entradas],
    }


def assess_session_carriers(cur, session_id: int,
                            evidence_limit: int = DEFAULT_EVIDENCE_LIMIT) -> dict:
    """Every multi-carrier entry of one session."""
    cur.execute("""
        SELECT c.number
          FROM results r JOIN cars c ON c.id = r.car_id
         WHERE r.session_id = %s
      GROUP BY c.number
        HAVING count(*) FILTER (
                 WHERE EXISTS (SELECT 1 FROM laps l
                                WHERE l.session_id = r.session_id
                                  AND l.car_id = r.car_id)) > 1
      ORDER BY c.number
    """, (session_id,))
    numeros = [linha[0] for linha in cur.fetchall()]

    colunas = lap_columns(cur) if numeros else None
    de_result = result_columns(cur) if numeros else None
    entradas = [assess_entry(cur, session_id, n, colunas, de_result,
                             evidence_limit) for n in numeros]
    veredito = max((e.verdict for e in entradas), key=lambda v: SEVERITY[v],
                   default=CANDIDATE)
    return {"session_id": session_id,
            "entradas_com_multiplos_portadores": len(entradas),
            "verdict": veredito if entradas else None,
            "entries": [e.as_dict() for e in entradas]}


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
    ap.add_argument("--session-id", type=int,
                    help="one session; omit with --whole-class")
    ap.add_argument("--number", help="one entry; omit for every multi-carrier entry")
    ap.add_argument("--whole-class", action="store_true",
                    help="every multi-carrier entry of the triplication class")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5433)
    ap.add_argument("--dbname", default="openwec")
    ap.add_argument("--user", default="openwec")
    ap.add_argument("--password", default="")
    ap.add_argument("--evidence-limit", type=int, default=DEFAULT_EVIDENCE_LIMIT,
                    help="how many diverging pairs to list; 0 lists every one. "
                         "It never changes a count or a classification")
    ap.add_argument("--json", type=Path,
                    help="write the report here; a scratch path outside the "
                         "repository and outside raw/ is the recommendation")
    args = ap.parse_args()

    cfg = {"host": args.host, "port": args.port, "dbname": args.dbname,
           "user": args.user, "password": args.password}
    conn = read_only_connection(cfg)
    try:
        with conn.cursor() as cur:
            if args.whole_class:
                dados = assess_class(cur, args.evidence_limit)
                entradas = []
            elif args.session_id is None:
                ap.error("informe --session-id ou --whole-class")
            elif args.number:
                dados = assess_entry(cur, args.session_id, args.number,
                                     evidence_limit=args.evidence_limit).as_dict()
                entradas = [dados]
            else:
                dados = assess_session_carriers(cur, args.session_id,
                                                args.evidence_limit)
                entradas = dados["entries"]
    except CarrierAssessmentError as exc:
        conn.rollback()
        conn.close()
        print(f"LEITURA FALHOU: {exc}")
        return 2
    finally:
        if not conn.closed:
            conn.rollback()
            conn.close()
    # No database from here on.

    if args.whole_class:
        print(f"entradas: {dados['entradas']}  em {dados['sessoes']} sessoes")
        print("  pares: " + "  ".join(f"{k}={v}" for k, v in dados["pares"].items()))
        print(f"  por entrada: {dados['vereditos_por_entrada']}")
        print(f"  por sessao:  {dados['vereditos_por_sessao']}")
        print(f"  equivalencia: {dados['equivalencias']}")
        print(f"  impedimentos: {dados['impedimentos']}")

    for e in entradas:
        print(f"sessao {e['session_id']} #{e['number']}: {e['verdict']}  "
              f"[{e['equivalence']}]")
        if e["pair_counts"]:
            print("  pares: " + "  ".join(
                f"{k}={v}" for k, v in e["pair_counts"].items() if v))
        meta = e.get("pair_evidence_meta") or {}
        if meta.get("divergencias"):
            print(f"  divergencias: {meta['divergencias']}  "
                  f"exibidas: {meta['exibidas']}  "
                  f"truncado: {meta['truncado']}")
        comparacao = (e.get("notes") or {}).get("comparacao_de_results") or {}
        if comparacao.get("materiais"):
            print(f"  results divergem em: {sorted(comparacao['materiais'])}")
        if comparacao.get("exclusivos"):
            print(f"  results com informacao exclusiva: "
                  f"{sorted(comparacao['exclusivos'])}")
        if e["impediments"]:
            print("  impedimentos: " + ", ".join(e["impediments"]))
        print(f"  candidate_car_ids: {e['candidate_car_ids']}  "
              f"(nenhuma preferencia demonstrada)")

    return write_report(dados, args.json)


if __name__ == "__main__":
    raise SystemExit(main())
