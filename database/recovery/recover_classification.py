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
    car_identity,
    extract_drivers,
    field,
    insert_result,
    insert_result_drivers,
    read_csv,
    upsert_driver,
)
from row_identity import DriverOutcome  # noqa: E402

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
    for numero in numeros_fonte:
        ids = das_voltas.get(numero)
        if ids:
            plano.matched[numero] = ids[0]
        else:
            plano.source_without_laps.append(numero)

    plano.laps_without_source = {
        numero: ids[0] for numero, ids in das_voltas.items()
        if numero not in plano.matched
    }
    return plano


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


def team_divergence(cur, car_id: int, published: str) -> tuple[str, str] | None:
    """
    The two spellings, when the stored team is not the one the source gives.

    `teams` is left untouched either way. The lap log and the classification
    name the same teams differently — "RLR M Sport" against "RLR MSport" —
    and treating that as a correction would rewrite an identity on nothing
    more than a difference in spacing. It is reported and left alone.
    """
    published = (published or "").strip()
    if not published:
        return None
    cur.execute("""
        SELECT t.name FROM cars c LEFT JOIN teams t ON t.id = c.team_id
         WHERE c.id = %s
    """, (car_id,))
    linha = cur.fetchone()
    armazenado = (linha[0] if linha else None) or ""
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
                    plan: Plan) -> dict:
    """
    Applies the plan. The caller owns the transaction.

    The completeness check is here, on the write path, rather than in the
    caller: a plan with an unmatched number on either side never reaches a
    DELETE, whoever calls this.
    """
    assert_complete_match(plan)
    keep = sorted(plan.matched.values())

    results_removidos, cars_removidos = drop_lap_shaped_results(
        cur, session_id, keep)

    gravados = 0
    colunas_preenchidas = 0
    divergencias: dict[str, list[str]] = {}
    equipes_divergentes: dict[str, tuple[str, str]] = {}
    pilotos_ligados = 0
    pilotos_ambiguos = 0
    pilotos_na_fonte = 0

    por_numero = {r["NUMBER"].strip(): r for r in rows}
    for numero in sorted(plan.matched, key=lambda n: int(n) if n.isdigit() else 0):
        row = por_numero[numero]
        car_id = plan.matched[numero]

        equipe = team_divergence(cur, car_id, field(row, "TEAM"))
        if equipe:
            equipes_divergentes[numero] = equipe

        preenchidas, divergem = fill_car_gaps(cur, car_id, row)
        colunas_preenchidas += preenchidas
        if divergem:
            divergencias[numero] = divergem

        result_id = insert_result(cur, session_id, car_id, row)
        if result_id is None:
            continue
        gravados += 1

        pilotos = extract_drivers(row, series_key)
        pilotos_na_fonte += len(pilotos)
        ligados = []
        for piloto in pilotos:
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
        "colunas_de_car_preenchidas": colunas_preenchidas,
        "cars_com_divergencia": divergencias,
        "equipes_com_grafia_divergente": equipes_divergentes,
        "pilotos_na_fonte": pilotos_na_fonte,
        "pilotos_ligados": pilotos_ligados,
        "pilotos_ambiguos": pilotos_ambiguos,
    }


# ── CLI ──────────────────────────────────────────────────────

def run(session_id: int, csv_path: Path, series_key: str,
        dry_run: bool = False) -> dict:
    import psycopg2

    rows = read_csv(csv_path)
    if not rows:
        raise RecoveryBlocked(f"{csv_path} nao tem linhas")

    cfg = target_config()
    conn = psycopg2.connect(connect_timeout=5, **cfg)
    try:
        with conn.cursor() as cur:
            assert_writable(cfg, cur)
            rotulo = session_label(cur, session_id)
            antes = session_state(cur, session_id)
            plano = plan_recovery(cur, session_id, rows)

            relatorio = {
                "alvo": f"{cfg['host']}:{cfg['port']}/{cfg['dbname']}",
                "sessao": f"{session_id} — {rotulo}",
                "fonte": str(csv_path),
                "linhas_na_fonte": len(rows),
                "antes": antes.as_dict(),
                "pareados": len(plano.matched),
                "fonte_sem_voltas": plano.source_without_laps,
                "voltas_sem_fonte": sorted(plano.laps_without_source),
            }

            if dry_run:
                relatorio["modo"] = "dry-run — nada foi escrito"
                try:
                    assert_complete_match(plano)
                except RecoveryBlocked as exc:
                    relatorio["recusaria_a_escrita"] = str(exc)
                conn.rollback()
                return relatorio

            relatorio.update(recover_session(
                cur, session_id, rows, series_key, plano))
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
    ap.add_argument("--print-marker-sql", action="store_true",
                    help="print the statement that declares a copy, and exit")
    args = ap.parse_args()

    if args.print_marker_sql:
        print(marker_sql())
        return 0

    if args.session_id is None or args.csv is None:
        ap.error("--session-id e --csv sao obrigatorios")

    try:
        relatorio = run(args.session_id, args.csv, args.series, args.dry_run)
    except RecoveryBlocked as exc:
        print(f"BLOQUEADO: {exc}")
        return 2

    for chave, valor in relatorio.items():
        print(f"  {chave}: {valor}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
