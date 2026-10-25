"""
OpenWEC — which of the three duplicate rows of an entry is the canonical one.

1.315 sessions hold exactly three rows in `results` for every car number, each
on a `cars` row of its own. They are not the lap-log contamination the
recovery path addresses: the values agree. They are three runs of the same
loader, from a time when `upsert_car` could not find the row it had just
written, and `results` is keyed on `(session_id, car_id)` so nothing
conflicted.

This module decides nothing about the database. It reads, and it says, for
each entry, which row could be kept, which could be discarded, and what
stands in the way.

## What "read only" covers here

**The historical database** and **the source files** are read and never
written: every statement is a `SELECT`, the CLI opens its connection with
`default_transaction_read_only`, and the published CSV is opened for reading
by `read_csv` and nothing else.

`--json` is the one thing that writes, and it writes **one local report
file**, chosen by whoever runs the command. It is written after the database
connection is already closed, so a path that does not exist or cannot be
written leaves the database exactly as a run without `--json` would. The
recommended destination is a scratch directory outside the repository and
outside `raw/` — the report carries measurements of a moment, not something
the project keeps.

## The rule, and its cost

The canonical row is **the one its laps already point at**. That preserves
`laps`, `analytics_car_session` and `analytics_stints` without re-pointing
anything, which is the whole reason to prefer it over the oldest row.

  * exactly one `car_id` carries laps  → that one;
  * none carries laps                  → the lowest id, which is also where
                                         the first ingestion left metadata;
  * more than one carries laps         → **nothing is chosen**. Picking one
                                         would attach half an entry's laps
                                         to a row that does not own them.

`MEASURED` over the class: 34.355 entries have their laps on the lowest id,
67 do not, 4.244 have no laps at all, and 225 have laps on more than one.

## What is never done silently

In those 67 entries the row that carries the laps is not the row that
carries `tyre_supplier`. Keeping the laps would drop the metadata, so this
module does not let that pass: the value and the `car_id` it came from are
reported as **complementary metadata**, and the entry is marked `SPECIAL`.
Whether to carry it over is a separate decision, and not this module's.

Drivers are never merged by name. Where the same person appears under two
`drivers` rows — one from the classification, one from the lap log — the
pair is reported as a **candidate**, and the session is marked `SPECIAL`.

## Why `plan_recovery` is not called here

`plan_recovery` pairs published source rows to the cars that carry laps, and
refuses when that pairing is not a bijection. The question here is the other
one: given three car rows that are already in the database for one entry,
which may be kept. The primitive they share is `lap_cars`, which is imported
from that module rather than rewritten, so both answer "whose laps are
these?" the same way.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "loader"))

from load_classification import field, normalize_status, read_csv  # noqa: E402
from row_identity import full_name_key  # noqa: E402
from database.recovery.recover_classification import lap_cars  # noqa: E402

ELIGIBLE = "ELIGIBLE"
SPECIAL = "SPECIAL"
BLOCKED = "BLOCKED"

# Worst wins when an entry or a session carries more than one finding.
SEVERITY = {ELIGIBLE: 0, SPECIAL: 1, BLOCKED: 2}

# Impediments, named so a report can count them apart. Status and source
# revision are deliberately separate: one is the database disagreeing with
# the file, the other is the three ingestions disagreeing with each other,
# and a session can have either without the other.
MATERIAL_DIVERGENCE = "MATERIAL_DIVERGENCE"
MULTIPLE_LAP_CARRIERS = "MULTIPLE_LAP_CARRIERS"
COMPLEMENTARY_METADATA_ON_DISCARDED = "COMPLEMENTARY_METADATA_ON_DISCARDED"
STATUS_DIVERGES_FROM_SOURCE = "STATUS_DIVERGES_FROM_SOURCE"
SOURCE_REVISION_DIVERGES = "SOURCE_REVISION_DIVERGES"
SOURCE_UNVERIFIABLE = "SOURCE_UNVERIFIABLE"
SOURCE_COVERAGE_INCOMPLETE = "SOURCE_COVERAGE_INCOMPLETE"
DRIVER_IDENTITY_SPLIT = "DRIVER_IDENTITY_SPLIT"
OUTSIDE_TRIPLICATION_CLASS = "OUTSIDE_TRIPLICATION_CLASS"

# The premise the criterion was derived under. Everything measured about
# this class — which row carries the laps, where the metadata sits, that
# driver links exist on all three rows or none — was measured over entries
# with exactly three rows. An entry with one, two or four does not belong to
# the population, and an answer for it would be an answer to a question
# nobody asked.
ROWS_PER_ENTRY = 3

# Columns whose disagreement is a conflict of fact. `tyre_supplier` is not
# here: it is never contradicted, only absent, and absence is handled as
# complementary metadata.
RESULT_COLUMNS = (
    "position", "status", "laps_completed", "total_time_raw", "total_time_s",
    "gap_to_first_raw", "gap_to_first_s", "gap_to_prev_raw", "gap_to_prev_s",
    "fl_lap_number", "fl_time_raw", "fl_time_s", "fl_kph",
)
CAR_COLUMNS = (
    "team_id", "vehicle", "manufacturer", "car_class", "car_group", "tires",
    "imsa_car_id", "imsa_team_id",
)
# Columns a discarded row may carry and the canonical one may not.
COMPLEMENTARY_COLUMNS = ("tyre_supplier",)


@dataclass
class EntryAssessment:
    session_id: int
    number: str
    car_ids: list[int]
    canonical: int | None = None
    discarded: list[int] = dataclass_field(default_factory=list)
    verdict: str = ELIGIBLE
    reasons: list[str] = dataclass_field(default_factory=list)
    evidence: dict = dataclass_field(default_factory=dict)
    complementary: dict = dataclass_field(default_factory=dict)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class SessionAssessment:
    session_id: int
    entries: list[EntryAssessment] = dataclass_field(default_factory=list)
    verdict: str = ELIGIBLE
    reasons: list[str] = dataclass_field(default_factory=list)
    evidence: dict = dataclass_field(default_factory=dict)

    def counts(self) -> dict:
        return {v: sum(1 for e in self.entries if e.verdict == v)
                for v in (ELIGIBLE, SPECIAL, BLOCKED)}

    def as_dict(self) -> dict:
        return {"session_id": self.session_id, "verdict": self.verdict,
                "reasons": self.reasons, "evidence": self.evidence,
                "contagens": self.counts(),
                "entries": [e.as_dict() for e in self.entries]}


def worst(*veredictos: str) -> str:
    return max(veredictos, key=lambda v: SEVERITY[v])


# ── Reading one session ──────────────────────────────────────

def entry_rows(cur, session_id: int) -> dict[str, list[dict]]:
    """Every stored row of the session, grouped by car number."""
    colunas = ["number", "car_id", *RESULT_COLUMNS, *CAR_COLUMNS,
               *COMPLEMENTARY_COLUMNS]
    cur.execute(f"""
        SELECT c.number, r.car_id, r.position, r.status::text, r.laps_completed,
               r.total_time_raw, r.total_time_s, r.gap_to_first_raw,
               r.gap_to_first_s, r.gap_to_prev_raw, r.gap_to_prev_s,
               r.fl_lap_number, r.fl_time_raw, r.fl_time_s, r.fl_kph,
               c.team_id, c.vehicle, c.manufacturer, c.car_class, c.car_group,
               c.tires, c.imsa_car_id, c.imsa_team_id, c.tyre_supplier
          FROM results r JOIN cars c ON c.id = r.car_id
         WHERE r.session_id = %s
         ORDER BY c.number, r.car_id
    """, (session_id,))
    por_numero: dict[str, list[dict]] = {}
    for linha in cur.fetchall():
        registro = dict(zip(colunas, linha))
        por_numero.setdefault(registro["number"], []).append(registro)
    return por_numero


def material_divergence(linhas: list[dict]) -> dict[str, list]:
    """
    Columns where the rows hold more than one value, NULL not counted.

    A NULL is silence, not a different answer: a column one ingestion filled
    and another left empty is complementary metadata, and it is dealt with
    where that is decided. What this finds is contradiction.
    """
    divergem: dict[str, list] = {}
    for coluna in (*RESULT_COLUMNS, *CAR_COLUMNS):
        valores = {linha[coluna] for linha in linhas if linha[coluna] is not None}
        if len(valores) > 1:
            divergem[coluna] = sorted(str(v) for v in valores)
    return divergem


def complementary_metadata(linhas: list[dict], canonical: int) -> dict:
    """
    What a discarded row holds and the canonical one does not, with its origin.

    The `car_id` travels with the value on purpose. A later decision to carry
    it over has to be able to say where it came from, and a value without a
    provenance is not evidence of anything.
    """
    escolhida = next(l for l in linhas if l["car_id"] == canonical)
    achados: dict = {}
    for coluna in COMPLEMENTARY_COLUMNS:
        if escolhida[coluna] is not None:
            continue
        for linha in linhas:
            if linha["car_id"] != canonical and linha[coluna] is not None:
                achados[coluna] = {"valor": linha[coluna],
                                   "proveniencia_car_id": linha["car_id"]}
                break
    return achados


# ── The entry ────────────────────────────────────────────────

def assess_entry(session_id: int, number: str, linhas: list[dict],
                 portadores: list[int]) -> EntryAssessment:
    """
    Which row of this entry could be kept, and what stands in the way.

    `portadores` are the `car_id` that carry laps for this number in this
    session, as `lap_cars` reports them.
    """
    ids = sorted(l["car_id"] for l in linhas)
    e = EntryAssessment(session_id=session_id, number=number, car_ids=ids)
    e.evidence["linhas"] = len(linhas)

    if len(linhas) != ROWS_PER_ENTRY:
        # The premise failed, so the criterion does not apply and no answer
        # is given. Everything this module knows was measured over entries
        # with exactly three rows.
        e.verdict = BLOCKED
        e.reasons.append(OUTSIDE_TRIPLICATION_CLASS)
        e.evidence["linhas_esperadas"] = ROWS_PER_ENTRY
        return e

    divergem = material_divergence(linhas)
    if divergem:
        e.verdict = BLOCKED
        e.reasons.append(MATERIAL_DIVERGENCE)
        e.evidence["colunas_divergentes"] = divergem

    com_voltas = sorted(set(portadores) & set(ids))
    e.evidence["car_ids_com_voltas"] = com_voltas

    if len(com_voltas) > 1:
        # Deliberately not resolved: choosing one would give half an entry's
        # laps to a row that does not own them.
        e.verdict = worst(e.verdict, BLOCKED)
        e.reasons.append(MULTIPLE_LAP_CARRIERS)
        return e

    if com_voltas:
        e.canonical = com_voltas[0]
        e.evidence["criterio"] = "portador das voltas"
    else:
        e.canonical = ids[0]
        e.evidence["criterio"] = "menor car_id, nenhum portador de voltas"

    e.discarded = [i for i in ids if i != e.canonical]

    if e.verdict == BLOCKED:
        # A blocked entry still reports what it would have chosen, but the
        # choice is not an answer.
        e.canonical = None
        e.discarded = []
        return e

    e.complementary = complementary_metadata(linhas, e.canonical)
    if e.complementary:
        e.verdict = worst(e.verdict, SPECIAL)
        e.reasons.append(COMPLEMENTARY_METADATA_ON_DISCARDED)

    return e


# ── Session-level impediments ────────────────────────────────

def source_comparison(cur, session_id: int, source_rows: list[dict]) -> dict:
    """
    The database against the file, with every row of both sides accounted for.

    Two different findings come out of here and are reported apart.

    **Divergence** is the two sides describing the same car differently —
    the status defect PR #52 fixed, read back with today's parser. It is
    also separate from `MATERIAL_DIVERGENCE`, which is the three ingestions
    disagreeing with each other rather than with the file.

    **Incomplete coverage** is the two sides not describing the same set of
    cars: a number the file publishes and the database does not hold, one
    the database holds and the file does not publish, or one the file
    publishes twice. None of these is a status mismatch, and none may be
    skipped — a comparison that silently drops the rows it cannot pair
    reports agreement it never established. That is the shape of the defect
    PR #49 made and PR #50 had to correct.
    """
    cur.execute("""
        SELECT DISTINCT ON (c.number) c.number, r.status::text
          FROM results r JOIN cars c ON c.id = r.car_id
         WHERE r.session_id = %s ORDER BY c.number, r.id
    """, (session_id,))
    armazenado = dict(cur.fetchall())

    numeros_fonte = [r.get("NUMBER", "").strip() for r in source_rows]
    vazios = sum(1 for n in numeros_fonte if not n)
    repetidos = sorted({n for n in numeros_fonte
                        if n and numeros_fonte.count(n) > 1})

    divergem = []
    comparados = 0
    for row in source_rows:
        numero = row.get("NUMBER", "").strip()
        if not numero or numero not in armazenado or numero in repetidos:
            continue
        comparados += 1
        esperado = normalize_status(field(row, "STATUS"))
        if esperado != armazenado[numero]:
            divergem.append({"number": numero,
                             "bruto": field(row, "STATUS"),
                             "fonte": esperado,
                             "banco": armazenado[numero]})

    so_na_fonte = sorted({n for n in numeros_fonte
                          if n and n not in armazenado})
    so_no_banco = sorted(n for n in armazenado
                         if n not in set(numeros_fonte))

    return {
        "status_divergentes": divergem,
        "numeros_comparados": comparados,
        "numeros_na_fonte": len(numeros_fonte),
        "numeros_no_banco": len(armazenado),
        "numeros_so_na_fonte": so_na_fonte,
        "numeros_so_no_banco": so_no_banco,
        "numeros_repetidos_na_fonte": repetidos,
        "linhas_da_fonte_sem_numero": vazios,
        "cobertura_completa": not (so_na_fonte or so_no_banco or repetidos
                                   or vazios),
    }


def driver_identity_candidates(cur, session_id: int) -> list[dict]:
    """
    Same person under two `drivers` rows, found without merging anything.

    One row comes from the classification and one from the lap log, and they
    differ only in how the name is written. `full_name_key` is the fold
    `row_identity` already uses to decide that two spellings are the same
    name — borrowed here to *report* a candidate, never to act on one.
    """
    cur.execute("""
        SELECT DISTINCT d.id, d.first_name, d.last_name
          FROM result_drivers rd
          JOIN results r ON r.id = rd.result_id
          JOIN drivers d ON d.id = rd.driver_id
         WHERE r.session_id = %s
    """, (session_id,))
    dos_resultados = cur.fetchall()

    cur.execute("""
        SELECT DISTINCT d.id, d.first_name, d.last_name
          FROM laps l JOIN drivers d ON d.id = l.driver_id
         WHERE l.session_id = %s AND l.driver_id IS NOT NULL
    """, (session_id,))
    das_voltas = cur.fetchall()

    if not dos_resultados or not das_voltas:
        return []

    por_chave: dict[str, list[tuple]] = {}
    for ident, nome, sobrenome in das_voltas:
        por_chave.setdefault(full_name_key(nome or "", sobrenome or ""), []).append(
            (ident, nome, sobrenome))

    ids_das_voltas = {d[0] for d in das_voltas}
    candidatos = []
    for ident, nome, sobrenome in dos_resultados:
        if ident in ids_das_voltas:
            continue
        iguais = por_chave.get(full_name_key(nome or "", sobrenome or ""), [])
        for outro in iguais:
            candidatos.append({
                "id_em_results": ident,
                "nome_em_results": f"{nome} {sobrenome}".strip(),
                "id_em_laps": outro[0],
                "nome_em_laps": f"{outro[1]} {outro[2]}".strip(),
            })
    return candidatos


# ── The session ──────────────────────────────────────────────

def assess_session(cur, session_id: int,
                   source_rows: list[dict] | None = None) -> SessionAssessment:
    """
    Every entry of the session, plus what applies to the session as a whole.

    `source_rows` is the published classification, already parsed. Without
    it the status cannot be checked against anything, which is itself an
    impediment rather than a pass.
    """
    s = SessionAssessment(session_id=session_id)

    por_numero = entry_rows(cur, session_id)
    portadores = lap_cars(cur, session_id)

    for numero in sorted(por_numero):
        e = assess_entry(session_id, numero, por_numero[numero],
                         portadores.get(numero, []))
        s.entries.append(e)
        s.verdict = worst(s.verdict, e.verdict)

    motivos = {m for e in s.entries for m in e.reasons}
    s.reasons.extend(sorted(motivos))

    if any(MATERIAL_DIVERGENCE in e.reasons for e in s.entries):
        s.reasons.append(SOURCE_REVISION_DIVERGES)
        s.evidence["entradas_com_divergencia_material"] = sum(
            1 for e in s.entries if MATERIAL_DIVERGENCE in e.reasons)

    if source_rows is None:
        s.reasons.append(SOURCE_UNVERIFIABLE)
        s.verdict = worst(s.verdict, SPECIAL)
    else:
        comparacao = source_comparison(cur, session_id, source_rows)
        s.evidence["comparacao_com_a_fonte"] = comparacao
        if comparacao["status_divergentes"]:
            s.reasons.append(STATUS_DIVERGES_FROM_SOURCE)
            s.verdict = worst(s.verdict, SPECIAL)
        if not comparacao["cobertura_completa"]:
            # Not the same finding as a divergence, and not a pass: what was
            # not compared was not found to agree.
            s.reasons.append(SOURCE_COVERAGE_INCOMPLETE)
            s.verdict = worst(s.verdict, SPECIAL)

    candidatos = driver_identity_candidates(cur, session_id)
    if candidatos:
        s.reasons.append(DRIVER_IDENTITY_SPLIT)
        s.evidence["pares_de_piloto_candidatos"] = len(candidatos)
        s.evidence["amostra_de_pares"] = candidatos[:5]
        s.verdict = worst(s.verdict, SPECIAL)

    s.reasons = sorted(set(s.reasons))
    return s


# ── CLI ──────────────────────────────────────────────────────

def read_only_connection(cfg: dict):
    import psycopg2
    return psycopg2.connect(connect_timeout=5,
                            options="-c default_transaction_read_only=on",
                            **cfg)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--session-id", type=int, required=True)
    ap.add_argument("--csv", type=Path,
                    help="published classification, to check the status against")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5433)
    ap.add_argument("--dbname", default="openwec")
    ap.add_argument("--user", default="openwec")
    ap.add_argument("--password", default="")
    ap.add_argument("--json", type=Path,
                    help="write the report here; a scratch path outside the "
                         "repository and outside raw/ is the recommended "
                         "destination")
    args = ap.parse_args()

    rows = read_csv(args.csv) if args.csv else None
    cfg = {"host": args.host, "port": args.port, "dbname": args.dbname,
           "user": args.user, "password": args.password}
    conn = read_only_connection(cfg)
    try:
        with conn.cursor() as cur:
            s = assess_session(cur, args.session_id, rows)
    finally:
        conn.rollback()
        conn.close()
    # From here on there is no database. Whatever the report does next, the
    # instance has already been left as it was found.

    contagens = s.counts()
    print(f"sessao {s.session_id}: {s.verdict}")
    print(f"  entradas: {len(s.entries)}  "
          f"({contagens[ELIGIBLE]} elegiveis, {contagens[SPECIAL]} especiais, "
          f"{contagens[BLOCKED]} bloqueadas)")
    for motivo in s.reasons:
        print(f"  motivo: {motivo}")
    for e in s.entries:
        if e.verdict != ELIGIBLE:
            print(f"    #{e.number:<5} {e.verdict:<9} {','.join(e.reasons)}"
                  f"  canonico={e.canonical} descartar={e.discarded}"
                  f"  complementar={e.complementary or ''}")

    return write_report(s, args.json)


def write_report(s: SessionAssessment, destino: Path | None) -> int:
    """
    The one write this module does, and the only thing it can cost.

    A destination that does not exist, or cannot be written, is reported and
    changes the exit code. It cannot change the database: the connection was
    closed before this is reached.
    """
    if destino is None:
        return 0
    try:
        destino.write_text(
            json.dumps(s.as_dict(), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8")
    except OSError as exc:
        print(f"o relatorio nao pode ser escrito em {destino}: {exc}")
        print("o banco nao foi tocado: a conexao ja estava fechada")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
