"""
OpenWEC — recovery of one session's classification, in a disposable copy.

51 sessions hold a lap log where their classification should be: `results`
carries one row per lap crossing instead of one row per car, 324.720 rows for
1.665 real entries. PR #48 found the published classification for all 51; PR
#49 verified 182 of its URLs; PR #50 graded their finality; PR #52 ran the
parser over the awkward ones. This module is the first write: it replaces the
lap-shaped rows of ONE session with the classification the source publishes,
and it does so only where a mistake can be thrown away.

What it does not do, and will not be extended to do without its own decision:
it does not reingest, it does not resolve the 35.045 lap collisions, it does
not merge drivers by name similarity, and it does not touch any session but
the one it is given.

## The cars

The contaminated rows minted one `cars` row per lap crossing. A few of those
cars are not disposable: they are the ones `laps` points at. Recovery
therefore matches the published rows to the cars that already carry the laps,
**by car number**, and refuses when that match is not a bijection. A new car
for a number whose laps already hang off another car would split one entry in
two — the condition ADR 0001 forbids deciding here.

The matched car keeps its identity. Columns the classification fills and the
lap log left empty — vehicle, class, group, tyres — are written only where
the column is NULL. A column that disagrees is left alone and reported: a
spelling difference between two sources is not evidence that one is wrong.

One published car may stand without a lap, and only one kind: one the source
credits with `LAPS = 0` under a status saying it did not take part. A car
that completed no lap cannot appear in a lap log, so its absence there is
what the two sources agreeing looks like. It gets an identity of its own and
a result, and no lap is invented for it. Every other unmatched number is
still a refusal — this is an exception for one measured shape, not a general
tolerance for partial matching.

## The guard

Five gates, and all five have to open. There is no flag that skips one.

  1. `OPENWEC_RECOVERY_IS_DISPOSABLE` declared in the environment;
  2. the target is not the host and port the project reads as historical;
  3. the target carries the marker table a rehearsal copy is given by hand;
  4. that table holds exactly one row, and its `rehearsal_id` is the one the
     environment names in `RECOVERY_REHEARSAL_ID`;
  5. the `system_identifier` recorded in that row is the one the cluster
     reports now.

The first three can all be true of a database that merely looks like the
rehearsal: a name, a variable and a table are things anyone can arrange. The
last two bind the write to **one** cluster. `system_identifier` comes from
`pg_control_system()`; it is minted by `initdb` and is not carried by a dump,
so restoring this rehearsal into the historical cluster — or into production
— brings the row along and the identifier still disagrees. The identity is
recorded, not derived from any name, port or variable.

Every check fails closed. A missing table, a missing variable, more than one
row, a NULL column or a value that cannot be read is a refusal, never a pass.
Connection settings come from `RECOVERY_DB_*` only — never from
`database.db`, so no default can point this at the real instance.
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "loader"))

from load_classification import (  # noqa: E402
    CAR_COLUMNS,
    as_int,
    car_identity,
    car_session_scope,
    extract_drivers,
    field,
    insert_result,
    insert_result_drivers,
    normalize_status,
    read_csv,
    upsert_car,
    upsert_driver,
    upsert_team,
)
from row_identity import (  # noqa: E402
    DriverOutcome, comparable, full_name_key, resolve_driver_id,
)

from database.recovery.driver_manifest import (  # noqa: E402
    Manifest, ManifestRefused, SlotContext,
)

DISPOSABLE_ENV = "OPENWEC_RECOVERY_IS_DISPOSABLE"
REHEARSAL_ID_ENV = "RECOVERY_REHEARSAL_ID"
MARKER_TABLE = "recovery_rehearsal"

# The table the operator creates inside the copy, by hand, once. It records
# the cluster it was created in, reading `pg_control_system()` at that moment,
# so the identity cannot be written from outside without being inside. Nothing
# in this module ever creates or writes it: `--print-marker-sql` hands the
# statement to a person, and a person runs it where they mean to.
MARKER_DDL = f"""CREATE TABLE {MARKER_TABLE} (
    rehearsal_id      text        NOT NULL,
    system_identifier text        NOT NULL,
    declared_at       timestamptz NOT NULL DEFAULT now(),
    note              text        NOT NULL
);
INSERT INTO {MARKER_TABLE} (rehearsal_id, system_identifier, note)
SELECT '{{rehearsal_id}}', system_identifier::text, '{{note}}'
  FROM pg_control_system();"""


def marker_sql(rehearsal_id: str | None = None, note: str = "copia descartavel") -> str:
    """
    The statement that declares a copy, with an id that is only hex.

    The id is generated here rather than chosen, and it is checked before it
    is interpolated: a value that is not hexadecimal never reaches the SQL.
    """
    import uuid
    rehearsal_id = rehearsal_id or uuid.uuid4().hex
    if not all(c in "0123456789abcdef" for c in rehearsal_id.lower()):
        raise RecoveryBlocked("o identificador do ensaio tem de ser hexadecimal")
    if "'" in note or "\\" in note:
        raise RecoveryBlocked("a nota nao pode conter aspas nem barras")
    return MARKER_DDL.format(rehearsal_id=rehearsal_id, note=note)


class RecoveryBlocked(Exception):
    """Raised instead of writing when any gate is shut."""


def carregar_manifesto(caminho):
    """
    O manifesto, ou None. Um caminho ilegivel e recusa, nunca ausencia.

    `ManifestRefused` vira `RecoveryBlocked` aqui para que um manifesto
    quebrado pare a recuperacao pelo mesmo caminho que as outras comportas,
    em vez de passar como "sem manifesto".
    """
    from database.recovery import driver_manifest
    try:
        return driver_manifest.load(caminho)
    except ManifestRefused as exc:
        raise RecoveryBlocked(f"manifesto invalido: {exc}") from exc


# ── The guard ────────────────────────────────────────────────

def target_config() -> dict:
    """
    Where to write, read from `RECOVERY_DB_*` and nowhere else.

    `database.db.DB_CONFIG` is deliberately not consulted: a default that
    falls back to the project's own settings is a default that points at the
    historical instance.
    """
    missing = [name for name in ("RECOVERY_DB_HOST", "RECOVERY_DB_PORT",
                                 "RECOVERY_DB_NAME", "RECOVERY_DB_USER")
               if not os.environ.get(name, "").strip()]
    if missing:
        raise RecoveryBlocked(
            "faltam variaveis de conexao do alvo: " + ", ".join(missing))

    return {
        "host":     os.environ["RECOVERY_DB_HOST"].strip(),
        "port":     int(os.environ["RECOVERY_DB_PORT"].strip()),
        "dbname":   os.environ["RECOVERY_DB_NAME"].strip(),
        "user":     os.environ["RECOVERY_DB_USER"].strip(),
        "password": os.environ.get("RECOVERY_DB_PASSWORD", ""),
    }


def declared_disposable() -> bool:
    return os.environ.get(DISPOSABLE_ENV, "").strip().lower() in ("1", "true", "yes")


def historical_endpoint() -> tuple[str, int]:
    """The host and port the project reads as the historical database."""
    from database.db import DB_CONFIG
    return (str(DB_CONFIG["host"]), int(DB_CONFIG["port"]))


def refusal(cfg: dict, cur) -> str | None:
    """The reason this target must not be written to, or None."""
    if not declared_disposable():
        return f"{DISPOSABLE_ENV} nao declarado"

    try:
        historical = historical_endpoint()
    except Exception:                                  # pragma: no cover
        historical = ("127.0.0.1", 5433)
    if (cfg["host"], cfg["port"]) == historical:
        return (f"o alvo {cfg['host']}:{cfg['port']} e o endereco historico "
                "do projeto")

    cur.execute("SELECT to_regclass(%s)", (f"public.{MARKER_TABLE}",))
    linha = cur.fetchone()
    if not linha or linha[0] is None:
        return (f"o alvo nao tem a tabela {MARKER_TABLE}, que so uma copia "
                "descartavel recebe")

    return identity_refusal(cur)


def identity_refusal(cur) -> str | None:
    """
    Whether this is **the** rehearsal copy, and not merely one that looks it.

    Two values have to agree, and neither is a name, a port or a flag. The
    first is the id the environment names and the copy stores: a database
    that was never declared for this rehearsal does not hold it. The second
    is the cluster's `system_identifier`, minted by `initdb` and not carried
    by a dump — so a copy of this marker restored into the historical
    cluster, or into production, arrives with an identifier that no longer
    matches what it recorded.

    Anything unreadable is a refusal. There is no argument that skips this.
    """
    declarado = os.environ.get(REHEARSAL_ID_ENV, "").strip()
    if not declarado:
        return f"{REHEARSAL_ID_ENV} nao declarado"

    try:
        cur.execute(f"""
            SELECT m.rehearsal_id, m.system_identifier, c.system_identifier::text
              FROM {MARKER_TABLE} m, pg_control_system() c
        """)
        linhas = cur.fetchall()
    except Exception as exc:                               # pragma: no cover
        return f"a identidade do alvo nao pode ser lida: {exc}"

    if not linhas:
        return f"{MARKER_TABLE} esta vazia: o alvo nao declarou identidade"
    if len(linhas) > 1:
        return (f"{MARKER_TABLE} tem {len(linhas)} linhas; a identidade do "
                "alvo tem de ser uma so")

    gravado_id, gravado_cluster, cluster_atual = linhas[0]
    if not gravado_id or not gravado_cluster:
        return f"{MARKER_TABLE} tem identidade incompleta"

    if str(gravado_id) != declarado:
        return (f"a identidade declarada em {REHEARSAL_ID_ENV} nao e a que o "
                "alvo guarda")

    if not cluster_atual or str(gravado_cluster) != str(cluster_atual):
        return ("o cluster do alvo nao e aquele em que esta identidade foi "
                "criada")

    return None


def assert_writable(cfg: dict, cur) -> None:
    motivo = refusal(cfg, cur)
    if motivo:
        raise RecoveryBlocked(motivo)


# ── State, before and after ──────────────────────────────────

@dataclass
class SessionState:
    results: int
    cars_in_results: int
    result_drivers: int
    laps: int
    cars_in_laps: int

    def as_dict(self) -> dict:
        return {
            "results": self.results,
            "cars_in_results": self.cars_in_results,
            "result_drivers": self.result_drivers,
            "laps": self.laps,
            "cars_in_laps": self.cars_in_laps,
        }


def session_state(cur, session_id: int) -> SessionState:
    cur.execute("""
        SELECT
          (SELECT count(*) FROM results WHERE session_id = %(s)s),
          (SELECT count(DISTINCT car_id) FROM results WHERE session_id = %(s)s),
          (SELECT count(*) FROM result_drivers rd
             JOIN results r ON r.id = rd.result_id
            WHERE r.session_id = %(s)s),
          (SELECT count(*) FROM laps WHERE session_id = %(s)s),
          (SELECT count(DISTINCT car_id) FROM laps WHERE session_id = %(s)s)
    """, {"s": session_id})
    return SessionState(*cur.fetchone())


def session_label(cur, session_id: int) -> str:
    cur.execute("""
        SELECT sr.key, se.raw_id, e.raw_id, s.raw_id
          FROM sessions s
          JOIN events  e  ON e.id  = s.event_id
          JOIN seasons se ON se.id = e.season_id
          JOIN series  sr ON sr.id = se.series_id
         WHERE s.id = %s
    """, (session_id,))
    linha = cur.fetchone()
    if not linha:
        raise RecoveryBlocked(f"sessao {session_id} nao existe no alvo")
    return " / ".join(str(p) for p in linha)


# ── Matching the published rows to the cars that carry laps ──

@dataclass
class Plan:
    matched: dict[str, int] = dataclass_field(default_factory=dict)
    source_without_laps: list[str] = dataclass_field(default_factory=list)
    laps_without_source: dict[str, int] = dataclass_field(default_factory=dict)
    zero_lap: list[str] = dataclass_field(default_factory=list)


# The normalised statuses a car may carry and still be admitted with no lap.
# `VALID_STATUSES` has no "Not Started": the source spells it "Not started"
# and `normalize_status` maps that to `DNS`, which is the same fact under the
# name the enum uses.
ZERO_LAP_STATUSES = frozenset({"Not Classified", "DNS"})


def zero_lap_exception(row: dict) -> bool:
    """
    Whether this published car may stand without a lap of its own.

    A car that completed no lap cannot appear in a lap log, so its absence
    there is not a disagreement between the sources — it is what the sources
    agreeing looks like. The exception is deliberately narrow: `LAPS` has to
    be the number zero, which `as_int` returns only for a value the source
    actually wrote, never for an empty field; and the status has to be one
    the source uses to say the car did not take part.

    `MEASURED` (PR #54): the ten sessions blocked only by an unmatched
    published car were blocked by eleven cars, and every one of them has
    `LAPS = 0` with `Not classified` or `Not started`. Nothing wider than
    that is admitted here: a car with laps the log does not know, or one
    whose status says it raced, is still a refusal.
    """
    return (as_int(field(row, "LAPS")) == 0
            and normalize_status(field(row, "STATUS")) in ZERO_LAP_STATUSES)


def lap_cars(cur, session_id: int) -> dict[str, list[int]]:
    cur.execute("""
        SELECT c.number, array_agg(DISTINCT l.car_id)
          FROM laps l JOIN cars c ON c.id = l.car_id
         WHERE l.session_id = %s
      GROUP BY c.number
    """, (session_id,))
    return {numero: list(ids) for numero, ids in cur.fetchall()}


def plan_recovery(cur, session_id: int, rows: list[dict]) -> Plan:
    """
    Which published row belongs to which existing car, proven by number.

    Refuses rather than guesses: a number the lap log spreads over two cars,
    or a number the source publishes twice, is an ambiguity this routine has
    no evidence to settle, and settling it wrongly would attach a session's
    laps to someone else's result.
    """
    numeros_fonte = [r.get("NUMBER", "").strip() for r in rows]
    vazios = sum(1 for n in numeros_fonte if not n)
    if vazios:
        raise RecoveryBlocked(f"{vazios} linhas da fonte sem NUMBER")

    repetidos = sorted({n for n in numeros_fonte
                        if numeros_fonte.count(n) > 1})
    if repetidos:
        raise RecoveryBlocked(
            "a fonte publica o mesmo numero mais de uma vez: "
            + ", ".join(repetidos))

    das_voltas = lap_cars(cur, session_id)
    ambiguos = sorted(n for n, ids in das_voltas.items() if len(ids) > 1)
    if ambiguos:
        raise RecoveryBlocked(
            "o numero aparece em mais de um car_id nas voltas: "
            + ", ".join(ambiguos))

    plano = Plan()
    por_numero = {r.get("NUMBER", "").strip(): r for r in rows}
    for numero in numeros_fonte:
        ids = das_voltas.get(numero)
        if ids:
            plano.matched[numero] = ids[0]
        elif (numero not in das_voltas
              and zero_lap_exception(por_numero[numero])):
            # Admitted without a lap, and only because it says it ran none.
            # The `not in das_voltas` is belt and braces: a number with laps
            # took the branch above, and one whose laps are spread over two
            # cars never reaches here, because that is refused up front.
            plano.zero_lap.append(numero)
        else:
            plano.source_without_laps.append(numero)

    plano.laps_without_source = {
        numero: ids[0] for numero, ids in das_voltas.items()
        if numero not in plano.matched
    }
    return plano


def assert_zero_lap_identity(cur, plan: Plan, por_numero: dict[str, dict],
                             series_key: str, session_id: int) -> dict[str, int]:
    """
    A car for each admitted zero-lap number, or a refusal.

    `upsert_car` looks the identity up before inserting, on every column the
    loader writes **and within this session**, so a second run of the same
    recovery finds the row the first one made and no run can land on another
    session's car. What this adds is the check that the identity it lands on
    is not already in use by this session: a zero-lap car that resolved to a
    car already carrying one of the session's matched results would be two
    published rows on one `car_id`, and `results` is keyed on that.

    The session scope is read **before** the delete, when the contaminated
    rows are still there. It changes nothing: those rows carry no `vehicle`
    and no `tires`, and the published row does, so the eight-column match
    never reaches them.
    """
    ocupados = dict(plan.matched)
    resolvidos: dict[str, int] = {}

    for numero in plan.zero_lap:
        row = por_numero[numero]
        equipe = field(row, "TEAM") or "Unknown"
        team_id = upsert_team(cur, equipe)
        car_id = upsert_car(cur, numero, team_id, row, series_key, session_id)
        if car_id is None:
            raise RecoveryBlocked(
                f"o carro {numero}, sem voltas, nao pode ter identidade criada")

        ja = next((n for n, i in ocupados.items() if i == car_id), None)
        if ja is not None:
            raise RecoveryBlocked(
                f"o carro {numero}, sem voltas, resolveu para a mesma "
                f"identidade do carro {ja}")

        ocupados[numero] = car_id
        resolvidos[numero] = car_id

    return resolvidos


def assert_complete_match(plan: Plan) -> None:
    """
    A partial match is not a smaller recovery; it is a different session.

    If the source publishes a car the lap log does not know, writing the rest
    would leave that car out of a classification presented as complete. If
    the lap log knows a car the source does not publish, the delete would
    take away its result and put nothing back. Either way the two sides are
    not describing the same session, and which one is wrong is not something
    this routine can tell. It names the numbers and stops.

    `--dry-run` reports the same lists without raising: finding out is
    exactly what it is for.
    """
    problemas = []
    if plan.source_without_laps:
        problemas.append(
            "publicados sem voltas no banco: "
            + ", ".join(sorted(plan.source_without_laps)))
    if plan.laps_without_source:
        problemas.append(
            "com voltas no banco e sem linha publicada: "
            + ", ".join(sorted(plan.laps_without_source)))
    if problemas:
        raise RecoveryBlocked("pareamento incompleto — " + "; ".join(problemas))


# ── The drivers, resolved before anything is written ─────────

@dataclass
class DriverResolution:
    """
    Every published driver, resolved against `drivers` and written nowhere.

    The four outcomes are kept apart because they are four different facts.
    `exact` and `normalized` are links this run can make. `missing` is a
    driver the table has never seen, which can only be served by creating a
    row in a **global** table. `ambiguous` is several rows carrying one name,
    where choosing would be choosing by id.
    """
    total: int = 0
    exact: int = 0
    normalized: int = 0
    unnamed: int = 0
    missing: list = dataclass_field(default_factory=list)
    ambiguous: list = dataclass_field(default_factory=list)
    # Slots que uma decisao aprovada resolveu: {(numero, slot): driver_id}.
    by_manifest: dict = dataclass_field(default_factory=dict)
    # Por que o manifesto nao resolveu, quando havia ambiguidade.
    manifest_refusals: list = dataclass_field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "declarados": self.total,
            "correspondencia_exata": self.exact,
            "correspondencia_normalizada": self.normalized,
            "sem_nome": self.unnamed,
            "sem_candidato": [f"#{n} slot {s}: {p} {u}"
                              for n, s, p, u in self.missing],
            "multiplos_candidatos": [f"#{n} slot {s}: {p} {u}"
                                     for n, s, p, u in self.ambiguous],
            "resolvidos_por_manifesto": [f"#{n} slot {s} -> {i}"
                                         for (n, s), i in
                                         sorted(self.by_manifest.items())],
            "manifesto_recusou": self.manifest_refusals,
        }


def candidate_ids(cur, first_name: str, last_name: str) -> list[int]:
    """
    Every `drivers` row carrying this name in normalised form, ordered.

    It is the set `choose_driver` saw when it refused, and the set a decision
    was taken against. A row added or removed changes it, and that is what
    makes a stale decision stop applying.
    """
    cur.execute("""
        SELECT id FROM drivers
         WHERE lower(regexp_replace(coalesce(first_name,'') || coalesce(last_name,''),
                                    '[^A-Za-z0-9]', '', 'g')) = %s
         ORDER BY id
    """, (full_name_key(first_name, last_name),))
    return [linha[0] for linha in cur.fetchall()]


def resolve_drivers(cur, rows: list[dict], series_key: str,
                    manifest: "Manifest | None" = None,
                    slot_context=None) -> DriverResolution:
    """
    What every published driver would resolve to, **before** any write.

    Reads only. It calls the same `resolve_driver_id` the loader calls, so
    the answer here is the answer there: a second resolver would be a second
    opinion, and the point is to have one.

    **This is the single place a manifest is consulted.** Only where
    `choose_driver` refused — several rows for one name — and only to use the
    id an approved decision declares. Everything else is untouched: an exact
    match stays an exact match, an unknown driver stays unknown, and
    `resolve_driver_id` itself is not changed, so no other ingestion routine
    behaves differently.

    `slot_context` carries what the decision is checked against: the cluster,
    the session, and the publication's URL and digest. Without it a manifest
    is not consulted at all.
    """
    res = DriverResolution()
    for row in rows:
        numero = (field(row, "NUMBER") or "").strip()
        for piloto in extract_drivers(row, series_key):
            res.total += 1
            _id, desfecho = resolve_driver_id(
                cur, piloto["first_name"], piloto["last_name"])
            entrada = (numero, piloto["slot"],
                       piloto["first_name"], piloto["last_name"])
            if desfecho is DriverOutcome.EXACT_NAME:
                res.exact += 1
            elif desfecho is DriverOutcome.BY_NORMALIZED_NAME:
                res.normalized += 1
            elif desfecho is DriverOutcome.MULTIPLE_CANDIDATES:
                escolhido = _from_manifest(cur, manifest, slot_context,
                                           numero, piloto, res)
                if escolhido is None:
                    res.ambiguous.append(entrada)
                else:
                    res.by_manifest[(numero, piloto["slot"])] = escolhido
            elif desfecho is DriverOutcome.NOT_FOUND:
                res.missing.append(entrada)
            else:
                res.unnamed += 1
    return res


def _from_manifest(cur, manifest, slot_context, numero: str, piloto: dict,
                   res: DriverResolution) -> int | None:
    """
    The id an approved decision declares for this slot, or None.

    Every refusal is recorded with its reason. None of them falls back to a
    choice: a refusal here leaves the slot ambiguous, and the gate blocks as
    it did before the manifest existed.
    """
    if manifest is None or slot_context is None:
        return None
    ctx = SlotContext(
        cluster=slot_context["cluster"],
        session_id=slot_context["session_id"],
        publication_url=slot_context["publication_url"],
        publication_sha256=slot_context["publication_sha256"],
        car_number=numero,
        driver_slot=piloto["slot"],
        first_name=piloto["first_name"],
        last_name=piloto["last_name"],
        candidates=tuple(candidate_ids(cur, piloto["first_name"],
                                       piloto["last_name"])),
    )
    try:
        return manifest.resolve(ctx)
    except ManifestRefused as exc:
        res.manifest_refusals.append(
            f"#{numero} slot {piloto['slot']}: {exc}")
        return None


def assert_drivers_resolvable(res: DriverResolution,
                              allow_new_drivers: bool = False) -> None:
    """
    The sixth gate: a link the source states is written, or nothing is.

    The first rehearsal of session 1243 is why this exists. Every other gate
    opened, the plan paired 40 of 40, and the write would have gone through
    leaving **107 of 109** stated drivers linked — two entries quietly short
    of a driver the source names. The routine counted the ambiguity and
    carried on; nothing refused.

    Two refusals, and they refuse different things:

      ambiguous    several `drivers` rows carry one name. `choose_driver`
                   already declines to pick, and picking here would be
                   picking by id. The archive's own duplication is the
                   cause — 1.086 name groups collide under case folding in
                   2.862 rows — and resolving it is a decision with a named
                   owner, not a step of a recovery.

      missing      the driver is new to the table, so serving the link means
                   creating a row in `drivers`, which is **global**. A
                   session-scoped recovery that silently grows a global
                   table is a side effect nobody authorised; it is allowed
                   only when the caller says so.

    Neither is a tolerance to be widened. A partial set of links is a
    classification that disagrees with its own source.
    """
    for motivo in driver_refusals(res, allow_new_drivers):
        raise RecoveryBlocked(motivo)


def driver_refusals(res: DriverResolution,
                    allow_new_drivers: bool = False) -> list[str]:
    """
    Every reason this set of drivers cannot be written, not just the first.

    `--dry-run` exists to find out what is wrong, and finding out one thing
    at a time wastes a round: an operator who clears the ambiguity only then
    discovers the nineteen unknown drivers. The write path raises on the
    first of these; the report prints all of them.
    """
    motivos = []
    if res.ambiguous:
        detalhe = "; ".join(f"#{n} slot {s}: {p} {u}"
                            for n, s, p, u in res.ambiguous)
        motivos.append(
            f"{len(res.ambiguous)} piloto(s) com multiplos candidatos em "
            f"`drivers` — {detalhe}. Escolher um seria escolher por id; a "
            "duplicacao tem de ser decidida antes, com responsavel")

    if res.missing and not allow_new_drivers:
        detalhe = "; ".join(f"#{n} slot {s}: {p} {u}"
                            for n, s, p, u in res.missing)
        motivos.append(
            f"{len(res.missing)} piloto(s) exigiriam criar linha nova em "
            f"`drivers`, que e global — {detalhe}. Use --allow-new-drivers "
            "para autorizar explicitamente")
    return motivos


# ── The write ────────────────────────────────────────────────

CAR_FILLABLE = ("vehicle", "car_class", "car_group", "tires")


def fill_car_gaps(cur, car_id: int, row: dict) -> tuple[int, list[str]]:
    """
    Writes the classification's car columns only where the column is NULL.

    Returns how many columns were filled and which ones disagreed with a
    value already stored. A disagreement is reported, never overwritten: two
    sources spelling a team or a model differently is not evidence that the
    stored one is wrong, and deciding it is not this routine's job.
    """
    _, _, vehicle, car_class, car_group, tires, _, _ = car_identity("", 0, row)
    vindo = {"vehicle": vehicle, "car_class": car_class,
             "car_group": car_group, "tires": tires}

    cur.execute(
        f"SELECT {', '.join(CAR_FILLABLE)} FROM cars WHERE id = %s", (car_id,))
    atual = dict(zip(CAR_FILLABLE, cur.fetchone()))

    preencher = {c: vindo[c] for c in CAR_FILLABLE
                 if atual[c] is None and vindo[c] is not None}
    divergem = [c for c in CAR_FILLABLE
                if atual[c] is not None and vindo[c] is not None
                and atual[c] != vindo[c]]

    if preencher:
        atribuicoes = ", ".join(f"{c} = %s" for c in preencher)
        cur.execute(f"UPDATE cars SET {atribuicoes} WHERE id = %s",
                    (*preencher.values(), car_id))
    return len(preencher), divergem


# The name the loader writes when the source states no team at all:
#
#     team_name = row.get("TEAM", "").strip() or "Unknown"
#
# It is not a team. It is the absence of one, wearing a name — and the row
# cannot tell you which absence it is. `MEASURED` on the archive: 67.846 cars
# carry it, 66.608 of them in 11 sessions whose source is a lap log with no
# TEAM column at all, and **1.238 in 13 ordinary sessions** where the
# published TEAM cell was simply empty. Any rule keyed on the value alone
# would reach both.
ABSENT_TEAM = "Unknown"


def stored_team(cur, car_id: int) -> str:
    cur.execute("""
        SELECT t.name FROM cars c LEFT JOIN teams t ON t.id = c.team_id
         WHERE c.id = %s
    """, (car_id,))
    linha = cur.fetchone()
    return (linha[0] if linha else None) or ""


def car_is_exclusive(cur, car_id: int, session_id: int) -> bool:
    """Whether this car belongs to this session and to no other."""
    cur.execute("""
        SELECT (SELECT count(*) FROM results WHERE car_id = %s AND session_id <> %s)
             + (SELECT count(*) FROM laps    WHERE car_id = %s AND session_id <> %s)
    """, (car_id, session_id, car_id, session_id))
    return cur.fetchone()[0] == 0


def team_to_fill(cur, car_id: int, session_id: int, published: str,
                 allow: bool) -> int | None:
    """
    The team this car would be given, or None — **decided, not written**.

    The write happens in `recover_session`, after the one identity check that
    covers the whole change set. Deciding here and writing there is what
    makes that check possible: a column written before the check would have
    to be undone by it.

    Six conditions, and none of them is inferable from the stored value:

      0. the caller asked for it. Filling is **off by default**: a recovery
         that only rebuilds results does not touch the car's team;
      1. the stored team is **exactly** the sentinel. A real spelling that
         merely differs — "RLR M Sport" against "RLR MSport" — is reported
         and left alone (`team_divergence`);
      2. the source states a team. An empty TEAM leaves the sentinel where it
         is, which is the 1.238-row population of §ABSENT_TEAM;
      3. the car belongs to this session and to no other;
      4. the published name does not fold onto a team already stored
         (`team_fold_twin`);
      5. — and the identity check in the caller, which sees this team
         together with every other column the fill would write.
    """
    published = (published or "").strip()
    if not allow or not published or published == ABSENT_TEAM:
        return None
    if stored_team(cur, car_id) != ABSENT_TEAM:
        return None
    if not car_is_exclusive(cur, car_id, session_id):
        return None
    if team_fold_twin(cur, published) is not None:
        return None
    return upsert_team(cur, published)


def planned_identity(cur, car_id: int, row: dict,
                     team_id: int | None = None) -> tuple:
    """
    The eight columns this car would carry **after the whole fill**.

    Not the published tuple and not the stored one: the mixture the fill
    actually produces. `fill_car_gaps` writes only where the stored column is
    NULL and keeps the stored value where the two disagree, so checking the
    published tuple would test an identity the car will never have — and
    checking one column at a time would never test the identity at all,
    which is the shape of this that was wrong before.
    """
    cur.execute(
        f"SELECT {', '.join(CAR_COLUMNS)} FROM cars WHERE id = %s", (car_id,))
    guardado = dict(zip(CAR_COLUMNS, cur.fetchone()))

    publicado = dict(zip(CAR_COLUMNS, car_identity("", 0, row)))
    final = dict(guardado)
    for coluna in CAR_FILLABLE:
        if guardado[coluna] is None and publicado[coluna] is not None:
            final[coluna] = publicado[coluna]
    if team_id is not None:
        final["team_id"] = team_id
    return tuple(final[c] for c in CAR_COLUMNS)


def team_fold_twin(cur, published: str) -> str | None:
    """
    A team already stored under the same name but a different spelling.

    `teams` is `UNIQUE (name)` and that uniqueness is case-sensitive, so
    `upsert_team` would insert `Thiriet by Tds Racing` beside the three rows
    that already read `Thiriet By TDS Racing`, `Thiriet by TDS Racing` and
    `Thiriet By Tds Racing`. `MEASURED`: **76 team groups already collide**
    under folding in 1.111 rows, and `normalize_teams.py` merges them later
    leaving no record of what it merged.

    Growing that population to fill in a placeholder is a bad trade. The fill
    stops, the car keeps the marker, and the collision is reported instead of
    created — which is the whole difference between this and
    `normalize_teams`.
    """
    cur.execute("SELECT name FROM teams WHERE name <> %s", (published,))
    chave = comparable(published)
    for (nome,) in cur.fetchall():
        if comparable(nome) == chave:
            return nome
    return None


def identity_twin(cur, car_id: int, attrs: tuple, session_id: int) -> int | None:
    """
    Another car **of this session** that would carry this identity once the
    fill is written.

    This is the guard the first version of `fill_absent_team` did not have,
    and it was wrong without it. `MEASURED` on the recovered session 1007:
    writing the published team would give **25 of the 26 cars an identity
    that an older car already carries** — cars of other sessions of the same
    season, same number, same team, same model, same class, same tyre. The
    twenty-sixth would carry one that no car has.

    Why that mattered, and it was not a style point. `upsert_car` **used
    to** look a car up by the eight columns of `CAR_COLUMNS` alone, taking
    `ORDER BY id LIMIT 1`. A later ingestion of this very classification
    would therefore resolve the number to the **older** row, write its
    result against that car, and leave `results` and `laps` of one session
    pointing at two different cars for one entry — the split this module
    refuses to make when it matches by number, reintroduced from the other
    side. R2-B closed that path; the next section says what follows from
    it.

    So the fill stops where it would create a twin. It reports nothing and
    writes nothing; the caller counts it as not filled.

    Why the session, and only the session
    -------------------------------------
    The paragraph above was written when `upsert_car` looked a car up across
    the whole archive. Since R2-B it resolves within the session, and the
    "later ingestion resolves to the older row" cannot happen: a car of
    another session is no longer reachable, whatever its attributes.

    `MEASURED` on a copy restored from `65229eb5…`: of the 41 cars session
    1300 preserves, **41** share their filled identity with a car outside the
    session — Silverstone 2015, Spa 2016, Monza 2017, Paul Ricard 2018 — and
    **0** share it with another car of the session. Refusing those 41 was
    refusing a path that is closed, at the price of leaving 82 published
    columns unwritten.

    What stays dangerous is a collision **inside** the session, and it is
    real: two cars of one session carrying one identity are two candidates
    for one published row, and `results` is keyed on (session, car). That is
    what this now looks for.

    The scope predicate is `car_session_scope`, the same function
    `_CAR_IN_SESSION` uses — one definition, so the guard cannot come to mean
    something different from the lookup it guards.

    This is not a weakening, and there is no flag to switch it off: the check
    is still unconditional, still runs on every car, and a missing or
    nonsensical session is a refusal rather than a pass.
    """
    if not isinstance(session_id, int) or isinstance(session_id, bool) \
            or session_id <= 0:
        raise RecoveryBlocked(
            f"o guarda de identidade exige a sessao e recebeu {session_id!r}")

    condicao = " AND ".join(f"{c} IS NOT DISTINCT FROM %s" for c in CAR_COLUMNS)
    cur.execute(
        f"SELECT id FROM cars WHERE {condicao} AND id <> %s "
        f"AND {car_session_scope('cars', '%s')} ORDER BY id LIMIT 1",
        (*attrs, car_id, session_id, session_id))
    linha = cur.fetchone()
    return linha[0] if linha else None


def team_divergence(cur, car_id: int, published: str) -> tuple[str, str] | None:
    """
    The two spellings, when the stored team is not the one the source gives.

    `teams` is left untouched either way. The lap log and the classification
    name the same teams differently — "RLR M Sport" against "RLR MSport" —
    and treating that as a correction would rewrite an identity on nothing
    more than a difference in spacing. It is reported and left alone.

    The sentinel is **not** a divergence and is not reported as one: there is
    no second spelling there, only an absence. `fill_absent_team` handles it.
    """
    published = (published or "").strip()
    if not published:
        return None
    armazenado = stored_team(cur, car_id)
    if armazenado == ABSENT_TEAM:
        return None
    if armazenado and armazenado != published:
        return (armazenado, published)
    return None


def drop_lap_shaped_results(cur, session_id: int, keep: list[int]) -> tuple[int, int]:
    """
    Removes the session's results that no lap points at, and their orphan cars.

    `result_drivers` follows by cascade. A car is removed only when nothing
    at all refers to it any more — not a result, not a lap — so a car shared
    with another session survives whatever this session does.
    """
    cur.execute("""
        DELETE FROM results
         WHERE session_id = %s AND NOT (car_id = ANY(%s))
     RETURNING car_id
    """, (session_id, keep))
    candidatos = [linha[0] for linha in cur.fetchall()]
    removidos = len(candidatos)

    if not candidatos:
        return 0, 0

    cur.execute("""
        DELETE FROM cars
         WHERE id = ANY(%s)
           AND NOT EXISTS (SELECT 1 FROM results r WHERE r.car_id = cars.id)
           AND NOT EXISTS (SELECT 1 FROM laps    l WHERE l.car_id = cars.id)
    """, (candidatos,))
    return removidos, cur.rowcount


def recover_session(cur, session_id: int, rows: list[dict], series_key: str,
                    plan: Plan, allow_new_drivers: bool = False,
                    allow_team_fill: bool = False,
                    manifest=None, slot_context=None,
                    source_url: str | None = None) -> dict:
    """
    Applies the plan. The caller owns the transaction.

    The completeness check is here, on the write path, rather than in the
    caller: a plan with an unmatched number on either side never reaches a
    DELETE, whoever calls this. The driver gate sits beside it for the same
    reason, and before the DELETE for a sharper one: a run that stops here
    has removed nothing.

    `source_url` is written onto every result this routine writes, and is
    `None` unless the caller proved which publication the bytes came from.
    The row is the only place a reader of the database can learn that, and
    a URL asserted without that proof would be worse than a null: it would
    look like provenance.
    """
    assert_complete_match(plan)

    resolucao = resolve_drivers(cur, rows, series_key, manifest, slot_context)
    assert_drivers_resolvable(resolucao, allow_new_drivers)

    por_numero = {r["NUMBER"].strip(): r for r in rows}

    # Resolved before the delete, and kept with the matched ones. A zero-lap
    # car has no lap to protect it: if its id were not in `keep`, the second
    # run would delete the result the first one wrote and mint a new car for
    # it, which is the churn this routine exists to avoid.
    sem_voltas = assert_zero_lap_identity(cur, plan, por_numero, series_key,
                                          session_id)
    identidades = {**plan.matched, **sem_voltas}
    keep = sorted(identidades.values())

    results_removidos, cars_removidos = drop_lap_shaped_results(
        cur, session_id, keep)

    gravados = 0
    colunas_preenchidas = 0
    divergencias: dict[str, list[str]] = {}
    equipes_divergentes: dict[str, tuple[str, str]] = {}
    equipes_preenchidas: dict[str, str] = {}
    gemeos_recusados: dict[str, int] = {}
    pilotos_ligados = 0
    pilotos_ambiguos = 0
    pilotos_na_fonte = 0
    pilotos_por_manifesto = 0

    for numero in sorted(identidades, key=lambda n: int(n) if n.isdigit() else 0):
        row = por_numero[numero]
        car_id = identidades[numero]

        # Decidir, conferir a identidade resultante, e so entao escrever. O
        # conjunto inteiro passa por uma checagem so: equipe, veiculo,
        # classe, grupo e pneu produzem **uma** identidade, e e ela que pode
        # colidir com outro carro.
        equipe_nova = team_to_fill(cur, car_id, session_id,
                                   field(row, "TEAM"), allow_team_fill)
        final = planned_identity(cur, car_id, row, equipe_nova)
        gemeo = identity_twin(cur, car_id, final, session_id)

        if gemeo is not None:
            gemeos_recusados[numero] = gemeo
        else:
            if equipe_nova is not None:
                cur.execute("""
                    UPDATE cars SET team_id = %s
                     WHERE id = %s
                       AND team_id = (SELECT id FROM teams WHERE name = %s)
                """, (equipe_nova, car_id, ABSENT_TEAM))
                if cur.rowcount:
                    equipes_preenchidas[numero] = field(row, "TEAM").strip()

            preenchidas, divergem = fill_car_gaps(cur, car_id, row)
            colunas_preenchidas += preenchidas
            if divergem:
                divergencias[numero] = divergem

        equipe = team_divergence(cur, car_id, field(row, "TEAM"))
        if equipe:
            equipes_divergentes[numero] = equipe

        result_id = insert_result(cur, session_id, car_id, row, source_url)
        if result_id is None:
            continue
        gravados += 1

        pilotos = extract_drivers(row, series_key)
        pilotos_na_fonte += len(pilotos)
        ligados = []
        for piloto in pilotos:
            # A decisao aprovada, quando houver, e usada **exclusivamente**:
            # nao se consulta `upsert_driver` depois dela, para que nao haja
            # um segundo caminho capaz de criar linha ou escolher outra.
            declarado = resolucao.by_manifest.get((numero, piloto["slot"]))
            if declarado is not None:
                ligados.append((piloto["slot"], declarado))
                pilotos_por_manifesto += 1
                continue
            driver_id, desfecho = upsert_driver(cur, piloto)
            if driver_id:
                ligados.append((piloto["slot"], driver_id))
            elif desfecho is DriverOutcome.MULTIPLE_CANDIDATES:
                pilotos_ambiguos += 1
        if ligados:
            insert_result_drivers(cur, result_id, ligados)
            pilotos_ligados += len(ligados)

    return {
        "results_removidos": results_removidos,
        "cars_removidos": cars_removidos,
        "results_gravados": gravados,
        "cars_sem_voltas": sem_voltas,
        "colunas_de_car_preenchidas": colunas_preenchidas,
        "cars_com_divergencia": divergencias,
        "equipes_preenchidas_sobre_o_marcador": equipes_preenchidas,
        "cars_nao_atualizados_por_gemeo_de_identidade": gemeos_recusados,
        "equipes_com_grafia_divergente": equipes_divergentes,
        "pilotos_na_fonte": pilotos_na_fonte,
        "pilotos_ligados": pilotos_ligados,
        "pilotos_ambiguos": pilotos_ambiguos,
        "pilotos_por_manifesto": pilotos_por_manifesto,
    }


# ── CLI ──────────────────────────────────────────────────────

def run(session_id: int, csv_path: Path, series_key: str,
        dry_run: bool = False, allow_new_drivers: bool = False,
        allow_team_fill: bool = False, manifest_path: Path | None = None,
        publication_url: str | None = None,
        publication_sha256: str | None = None) -> dict:
    import psycopg2

    import hashlib

    rows = read_csv(csv_path)
    if not rows:
        raise RecoveryBlocked(f"{csv_path} nao tem linhas")

    manifest = carregar_manifesto(manifest_path)
    if manifest is not None and not publication_url:
        raise RecoveryBlocked(
            "--manifest exige --publication-url: a decisao foi tomada sobre "
            "uma publicacao, e e contra ela que a entrada e conferida")
    digest = hashlib.sha256(csv_path.read_bytes()).hexdigest()

    # A URL so vira proveniencia gravada quando os bytes lidos sao os da
    # publicacao declarada. Sem o digest ao lado dela, a URL seria uma
    # afirmacao sobre um arquivo que ninguem conferiu.
    if publication_url and not publication_sha256:
        raise RecoveryBlocked(
            "--publication-url exige --publication-sha256: a URL so e "
            "gravada quando o arquivo lido confere com a publicacao")
    if publication_sha256:
        declarado = publication_sha256.strip().lower()
        if declarado != digest:
            raise RecoveryBlocked(
                f"a fonte nao e a publicacao declarada: sha256 {digest}, "
                f"declarado {declarado}")
        if not publication_url:
            raise RecoveryBlocked(
                "--publication-sha256 exige --publication-url: um digest "
                "sem a publicacao que ele identifica nao e proveniencia")

    cfg = target_config()
    conn = psycopg2.connect(connect_timeout=5, **cfg)
    try:
        with conn.cursor() as cur:
            assert_writable(cfg, cur)
            cur.execute("SELECT system_identifier::text FROM pg_control_system()")
            cluster = cur.fetchone()[0]
            slot_context = None if manifest is None else {
                "cluster": cluster, "session_id": session_id,
                "publication_url": publication_url,
                "publication_sha256": digest,
            }

            rotulo = session_label(cur, session_id)
            antes = session_state(cur, session_id)
            plano = plan_recovery(cur, session_id, rows)

            relatorio = {
                "alvo": f"{cfg['host']}:{cfg['port']}/{cfg['dbname']}",
                "sessao": f"{session_id} — {rotulo}",
                "fonte": str(csv_path),
                "fonte_sha256": digest,
                "manifesto": (f"{manifest_path} ({len(manifest)} entradas)"
                              if manifest is not None else "nao usado"),
                "proveniencia": (publication_url if publication_url
                                 else "nao declarada — source_url fica nula"),
                "linhas_na_fonte": len(rows),
                "antes": antes.as_dict(),
                "pareados": len(plano.matched),
                "sem_voltas_admitidos": plano.zero_lap,
                "fonte_sem_voltas": plano.source_without_laps,
                "voltas_sem_fonte": sorted(plano.laps_without_source),
            }

            pilotos = resolve_drivers(cur, rows, series_key, manifest,
                                      slot_context)
            relatorio["pilotos"] = pilotos.as_dict()

            if dry_run:
                relatorio["modo"] = "dry-run — nada foi escrito"
                recusas = []
                try:
                    assert_complete_match(plano)
                except RecoveryBlocked as exc:
                    recusas.append(str(exc))
                recusas.extend(driver_refusals(pilotos, allow_new_drivers))
                if recusas:
                    relatorio["recusaria_a_escrita"] = recusas
                conn.rollback()
                return relatorio

            relatorio.update(recover_session(
                cur, session_id, rows, series_key, plano, allow_new_drivers,
                allow_team_fill, manifest, slot_context,
                source_url=publication_url))
            relatorio["depois"] = session_state(cur, session_id).as_dict()
        conn.commit()
        return relatorio
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--session-id", type=int)
    ap.add_argument("--csv", type=Path,
                    help="classification published for this session")
    ap.add_argument("--series", default="LEMANSCUP")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--manifest", type=Path,
                    help="arquivo de resolucoes de piloto; sem ele, varios "
                         "candidatos continuam bloqueando")
    ap.add_argument("--publication-url", help="a URL oficial desta publicacao, "
                                              "exigida com --manifest; "
                                              "gravada em results.source_url")
    ap.add_argument("--publication-sha256",
                    help="o sha256 que essa URL publica; exigido junto de "
                         "--publication-url e conferido contra o arquivo")
    ap.add_argument("--fill-absent-team", action="store_true",
                    help="autoriza escrever a equipe publicada sobre o "
                         "marcador `Unknown`; desligado por padrao")
    ap.add_argument("--allow-new-drivers", action="store_true",
                    help="autoriza criar linha nova em `drivers`, que e uma "
                         "tabela global; sem ela, um piloto desconhecido "
                         "bloqueia a recuperacao")
    ap.add_argument("--print-marker-sql", action="store_true",
                    help="print the statement that declares a copy, and exit")
    args = ap.parse_args()

    if args.print_marker_sql:
        print(marker_sql())
        return 0

    if args.session_id is None or args.csv is None:
        ap.error("--session-id e --csv sao obrigatorios")

    try:
        relatorio = run(args.session_id, args.csv, args.series, args.dry_run,
                        args.allow_new_drivers, args.fill_absent_team,
                        args.manifest, args.publication_url,
                        args.publication_sha256)
    except RecoveryBlocked as exc:
        print(f"BLOQUEADO: {exc}")
        return 2

    for chave, valor in relatorio.items():
        print(f"  {chave}: {valor}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
