"""
OpenWEC — the authorised path for recovering session 2226 in a real database.

The rehearsal recoverer in `recover_classification` writes only where a
mistake can be thrown away: it refuses any target that is not a declared
disposable copy, and that refusal is not weakened here. This is a separate
path, for the one recovery that has been rehearsed four times, and it carries
its own gates rather than relaxing those.

Nothing in this module is authorisation. No flag, environment variable or
default grants it: the operator has to be at a keyboard and type, twice, what
they intend — the session and the cluster — and both have to match what the
target actually reports. A run with no one answering those prompts ends
without opening a write.

## What it will do, and only that

A closed allowlist of sessions, written out one by one in `SESSOES`, each
carrying its own source URL and hash, its own identity, its own before and
after states, its own lock key and its own answer to whether creating a
driver is permitted. There is no batch mode, no `--all`, no range and no way
to name a session that is not in that table: the identifier is looked up, and
a miss is a refusal before any query runs.

Today it holds two: **2226**, Le Mans Cup 2018, Paul Ricard, and **2217**,
Le Mans Cup 2017, Algarve. Adding a third is a reviewed change to this file,
not an argument.

## The gates

  0. no one else is writing to the tables this touches — a `LOCK TABLE ...
     IN SHARE ROW EXCLUSIVE MODE NOWAIT` taken before any snapshot, which
     readers do not conflict with, so the API keeps serving;
  1. the session asked for is one of the allowlisted ones;
  2. a backup file exists, is a PostgreSQL custom-format archive, and its
     attestation proves it: the hash describes that file, it was captured
     from this cluster within the allowed window, its restore was verified
     in a different cluster, and its counts still match the target;
  3. the cluster the operator says they are writing to is the one
     `pg_control_system()` reports — not a host, not a port, not a name;
  4. the session's identity in the database is the one that was rehearsed:
     series, season, event and session key;
  5. the source file hashes to the bytes that session was rehearsed against;
  6. the current state of the session matches the baseline measured before
     the rehearsals — if anything moved since, the run stops rather than
     adapt to it;
  7. the plan the recoverer produces is a complete match of every car the
     source publishes;
  8. nothing outside this session refers to a car the plan would remove;
  9. creating a driver happens only where the allowlist says it may, only
     with `--allow-new-drivers` typed, and only in the number declared;
 10. the advisory lock is free, so two runs cannot overlap;
 11. the operator confirms the session and the cluster, interactively.

Everything happens in one transaction, at REPEATABLE READ, so the checks,
the write and the checks afterwards all see one snapshot. After the write
and before the commit the invariants are checked again against the database
itself; a single failure rolls the whole thing back. A `ROLLBACK` is not a
recovery plan after a `COMMIT`, which is why gate 2 exists and why the
report names the backup.

Gate 0 is not decoration. The advisory lock in `preflight` coordinates only
runs that take it, which an ingestion, an analytics job or a `psql` session
does not — and two of the tables here carry a `car_id` with no foreign key
behind it, so a row written into them after the pre-validation would outlive
the car it names and nothing in the database would say so.

## Provenance

Each allowlisted session names the publication it is recovered from, and the
sha256 of that publication's bytes. The file is refused unless it hashes to
that value, and only then is the URL written onto every result row, where a
later reader can find it. Nothing invents a URL for a row whose source is
unknown: a session with no declared publication is not in this table.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "loader"))

from load_classification import read_csv  # noqa: E402
from database.recovery.backup_attestation import (  # noqa: E402
    DEFAULT_MAX_AGE_HOURS,
    AttestationInvalid,
    assert_matches_target,
    validate as validate_attestation,
)
from database.recovery.recover_classification import (  # noqa: E402
    RecoveryBlocked,
    assert_complete_match,
    plan_recovery,
    recover_session,
    session_state,
)

@dataclass(frozen=True)
class SessaoAutorizada:
    """
    One allowlisted session, with everything this path needs to refuse.

    Every field is a measurement someone took and a reviewer can check, not
    a default: the identity as the database spells it, the publication and
    its hash, the state before and the state after, the lock key, and
    whether this session is allowed to put a row in the global `drivers`
    table and how many.
    """
    session_id: int
    series: str
    source_url: str
    source_sha256: str
    cars_in_source: int
    identity: tuple[str, str, str, str]
    baseline: dict
    already_recovered: dict
    lock_key: int
    new_drivers_expected: int

    @property
    def expected_after(self) -> dict:
        return dict(self.already_recovered)


# The sessions this path knows how to recover, and only these. A dict, not a
# range and not a predicate: membership is written down, and adding one is a
# diff a reviewer reads.
#
# `already_recovered` is accepted as a starting point as well as an ending
# one, so running the same recovery twice is a no-op rather than a refusal —
# the underlying routine is idempotent and was rehearsed. Accepting it is not
# adapting to an unexpected state: it is the only other state this path has
# ever produced. `result_drivers` is left out of that comparison because
# driver linking depends on what `drivers` already holds, which the
# rehearsals showed varying between instances.
SESSOES: dict[int, SessaoAutorizada] = {
    2226: SessaoAutorizada(
        session_id=2226,
        series="LEMANSCUP",
        source_url=(
            "https://lemanscup.alkamelsystems.com/Results/02_2018/"
            "01_PAUL%20RICARD/218_Michelin%20Le%20Mans%20Cup/"
            "201804141605_Race/Hour%202/05_Classification_Race.CSV"
        ),
        source_sha256=("034e73c53cea3fdc5f67f636eeade3dabb689437"
                       "2fc1409fd5839973f472e29c"),
        cars_in_source=28,
        identity=("LEMANSCUP", "02_2018", "01_PAUL RICARD",
                  "201804141605_Race"),
        baseline={"results": 3108, "cars_in_results": 3108,
                  "result_drivers": 0, "laps": 1554, "cars_in_laps": 28},
        already_recovered={"results": 28, "cars_in_results": 28,
                           "laps": 1554, "cars_in_laps": 28},
        lock_key=2226_0001,
        # Every driver this session publishes already has a row. The
        # rehearsals created none, so creating one here would mean the
        # source or the database is not what was rehearsed.
        new_drivers_expected=0,
    ),
    # `MEASURED` on the R3 copy of production, 2026-11-01: the recovery ran,
    # paired 19 of 19, created exactly 12 drivers, linked 38 slots, left the
    # 1.155 laps untouched and was idempotent on re-run.
    2217: SessaoAutorizada(
        session_id=2217,
        series="LEMANSCUP",
        source_url=(
            "https://lemanscup.alkamelsystems.com/Results/01_2017/"
            "06_AUTODROMO%20DO%20ALGARVE/207_Michelin%20Le%20Mans%20Cup/"
            "201710220900_Race/Hour%202/05_Classification_Race_Hour%202.CSV"
        ),
        source_sha256=("b6ac6628e470fe5cb4138bea4f003a0e864f93fe"
                       "996e4dd3bb67b5e92c08ead7"),
        cars_in_source=19,
        identity=("LEMANSCUP", "01_2017", "06_AUTODROMO DO ALGARVE",
                  "201710220900_Race"),
        baseline={"results": 2310, "cars_in_results": 2310,
                  "result_drivers": 0, "laps": 1155, "cars_in_laps": 19},
        already_recovered={"results": 19, "cars_in_results": 19,
                           "laps": 1155, "cars_in_laps": 19},
        lock_key=2217_0001,
        # Twelve of the 38 published slots name someone `drivers` does not
        # hold under any spelling — checked for collisions, none found. The
        # number is pinned so that thirteen is a refusal, not a surprise.
        new_drivers_expected=12,
    ),
}

ALLOWED_SESSIONS = frozenset(SESSOES)

WRITE_BLOCKING_LOCK = "SHARE ROW EXCLUSIVE"
LOCK_NOT_AVAILABLE = "55P03"

# Every table a run writes, or reads and then depends on, with the reason it
# is in this list. The advisory lock below coordinates only executors that
# take it; this is what stops an ingestion, an analytics job or a `psql`
# session.
#
# `MEASURED` on a disposable copy, before this list existed: an
# `analytics_car_session` row inserted by another connection *after*
# `external_references` had checked, naming a car the plan was about to
# remove, survived the delete as a dangling reference — and the run reported
# COMMIT. Nothing complained, because that column carries no foreign key,
# and the API joins it to `cars` with an inner join, so the car simply
# disappears from the response.
LOCKED_TABLES = (
    "results",                # reescrita inteira da sessao
    "result_drivers",         # apagada por cascata e reinserida
    "cars",                   # removidos e preenchidos
    "drivers",                # pode ganhar linha
    "laps",                   # nunca escrita, mas o plano e duas invariantes dependem dela
    "analytics_car_session",  # car_id sem FK
    "analytics_stints",       # car_id sem FK
)

WRITERS_SQL = """
SELECT DISTINCT a.pid, a.usename, a.application_name, a.state,
       l.mode, c.relname
FROM pg_locks l
JOIN pg_class c ON c.oid = l.relation
LEFT JOIN pg_stat_activity a ON a.pid = l.pid
WHERE c.relname = ANY(%s)
  AND l.pid <> pg_backend_pid()
  AND l.mode NOT IN ('AccessShareLock', 'RowShareLock')
ORDER BY 1, 6
"""


def autorizada(session_id: int) -> SessaoAutorizada:
    """The allowlist entry, or the refusal. No lookup falls through."""
    plano = SESSOES.get(session_id)
    if plano is None:
        permitidas = ", ".join(str(s) for s in sorted(ALLOWED_SESSIONS))
        raise HistoricalBlocked(
            f"este caminho recupera apenas as sessoes {permitidas}; "
            f"{session_id} nao esta previsto")
    return plano


PGDMP_MAGIC = b"PGDMP"


class HistoricalBlocked(Exception):
    """Raised instead of writing when any gate is shut."""


class HistoricalRecoveryFailed(Exception):
    """Carries the report of a run that rolled back."""

    def __init__(self, relatorio: dict):
        super().__init__(relatorio.get("motivo", "rollback"))
        self.relatorio = relatorio


# ── Evidence about the target and the backup ─────────────────

def cluster_identifier(cur) -> str:
    cur.execute("SELECT system_identifier::text FROM pg_control_system()")
    linha = cur.fetchone()
    if not linha or not linha[0]:
        raise HistoricalBlocked("o cluster do alvo nao informa system_identifier")
    return str(linha[0])


def backup_evidence(caminho: Path, agora=None,
                    max_age_hours: int = DEFAULT_MAX_AGE_HOURS) -> dict:
    """
    What the report will say about the backup, and the refusal if there isn't one.

    The file has to be a PostgreSQL custom-format archive, which is what
    `pg_restore` reads and what the revert procedure assumes. A plain SQL
    dump, a truncated download or an empty file would all pass a bare
    existence check and none of them would restore.

    Being a well-formed archive is where the checking used to stop, and it
    was not enough: a hash says nothing about which database the file came
    from, when, or whether it restores. The sidecar attestation answers those
    three, and is refused if it does not describe this exact file, records no
    successful restore into a different cluster, or is older than the window.
    Whether it is a pre-image of *this* target is checked later, in
    `preflight`, where there is a connection to compare against.
    """
    if not caminho.exists() or not caminho.is_file():
        raise HistoricalBlocked(f"backup nao encontrado: {caminho}")

    tamanho = caminho.stat().st_size
    if tamanho == 0:
        raise HistoricalBlocked(f"backup vazio: {caminho}")

    with caminho.open("rb") as arquivo:
        inicio = arquivo.read(len(PGDMP_MAGIC))
    if inicio != PGDMP_MAGIC:
        raise HistoricalBlocked(
            f"{caminho} nao e um arquivo pg_dump de formato custom (-Fc)")

    try:
        prova = validate_attestation(caminho, agora, max_age_hours)
    except AttestationInvalid as exc:
        raise HistoricalBlocked(f"backup sem evidencia utilizavel: {exc}") from exc

    return {
        "caminho": str(caminho),
        "bytes": tamanho,
        "sha256": prova["sha256"],
        "cluster_de_origem": prova["cluster_de_origem"],
        "capturado_em": prova["capturado_em"],
        "idade_horas": prova["idade_horas"],
        "contagens": prova["contagens"],
        "sessao": prova["sessao"],
        "restauracao": prova["restauracao"],
        "_prova": prova,
    }


def source_evidence(caminho: Path, plano: SessaoAutorizada) -> dict:
    """
    The bytes, hashed, against the publication this session was rehearsed on.

    The URL is returned beside the hash and not independently of it: what is
    later written into `results.source_url` is only ever the URL of a file
    that hashed to the declared value.
    """
    dados = caminho.read_bytes()
    sha = hashlib.sha256(dados).hexdigest()
    if sha != plano.source_sha256:
        raise HistoricalBlocked(
            f"a fonte nao e a que foi ensaiada para a sessao "
            f"{plano.session_id}: sha256 {sha}")
    return {"caminho": str(caminho), "bytes": len(dados), "sha256": sha,
            "url": plano.source_url}


# ── Pre-validation ───────────────────────────────────────────

def session_identity(cur, session_id: int) -> tuple:
    cur.execute("""
        SELECT sr.key::text, se.raw_id, e.raw_id, s.raw_id
          FROM sessions s
          JOIN events  e  ON e.id  = s.event_id
          JOIN seasons se ON se.id = e.season_id
          JOIN series  sr ON sr.id = se.series_id
         WHERE s.id = %s
    """, (session_id,))
    linha = cur.fetchone()
    if not linha:
        raise HistoricalBlocked(f"a sessao {session_id} nao existe no alvo")
    return tuple(linha)


def removable_cars(cur, session_id: int, keep: list[int]) -> list[int]:
    cur.execute("""
        SELECT DISTINCT car_id FROM results
         WHERE session_id = %s AND NOT (car_id = ANY(%s))
      ORDER BY car_id
    """, (session_id, keep))
    return [linha[0] for linha in cur.fetchall()]


def external_references(cur, session_id: int, cars: list[int]) -> dict:
    """
    Who else points at the cars the plan would remove.

    `drop_lap_shaped_results` already refuses to delete a car that any
    result or lap still references, so those two would survive silently.
    `analytics_car_session` and `analytics_stints` carry a `car_id` with no
    foreign key behind it: a row there pointing at a removed car would be
    left dangling, and nothing in the database would complain. `MEASURED` in
    the rehearsal: zero. This checks it again, on the real instance, and
    refuses if it is not.
    """
    if not cars:
        return {"results_de_outras_sessoes": 0, "laps": 0,
                "analytics_car_session": 0, "analytics_stints": 0}

    cur.execute("""
        SELECT
          (SELECT count(*) FROM results r
            WHERE r.car_id = ANY(%(c)s) AND r.session_id <> %(s)s),
          (SELECT count(*) FROM laps l WHERE l.car_id = ANY(%(c)s)),
          (SELECT count(*) FROM analytics_car_session a WHERE a.car_id = ANY(%(c)s)),
          (SELECT count(*) FROM analytics_stints a WHERE a.car_id = ANY(%(c)s))
    """, {"c": cars, "s": session_id})
    outros, voltas, acs, stints = cur.fetchone()
    return {"results_de_outras_sessoes": outros, "laps": voltas,
            "analytics_car_session": acs, "analytics_stints": stints}


def other_sessions_digest(cur, session_id: int) -> tuple[str, int]:
    cur.execute("""
        SELECT md5(string_agg(x, '|' ORDER BY x)), count(*)
        FROM (
          SELECT s.id || ':' ||
                 (SELECT count(*) FROM results r WHERE r.session_id = s.id) || ':' ||
                 (SELECT count(*) FROM laps l WHERE l.session_id = s.id) AS x
            FROM sessions s WHERE s.id <> %s
        ) t
    """, (session_id,))
    digest, quantas = cur.fetchone()
    return digest, quantas


def drivers_total(cur) -> int:
    cur.execute("SELECT count(*) FROM drivers")
    return cur.fetchone()[0]


def writer_sessions(cur) -> list[dict]:
    """
    Who is holding a lock on these tables that a writer would hold.

    ACCESS SHARE and ROW SHARE are left out: those are readers, and readers
    are not what this has to stop — the API keeps serving throughout.
    """
    cur.execute(WRITERS_SQL, (list(LOCKED_TABLES),))
    return [{"pid": pid, "usuario": usuario, "aplicacao": app,
             "estado": estado, "modo": modo, "tabela": tabela}
            for pid, usuario, app, estado, modo, tabela in cur.fetchall()]


def lock_out_writers(conn, cur) -> None:
    """
    Keep every other writer off these tables for the rest of the transaction.

    NOWAIT on purpose: a queued lock request blocks the readers that arrive
    behind it, so waiting for a busy table would take the API down with it.
    When the lock is not free the run refuses and names who had it.
    """
    try:
        cur.execute(f"LOCK TABLE {', '.join(LOCKED_TABLES)} "
                    f"IN {WRITE_BLOCKING_LOCK} MODE NOWAIT")
        return
    except Exception as exc:                                   # noqa: BLE001
        if getattr(exc, "pgcode", None) != LOCK_NOT_AVAILABLE:
            raise
    # The failed LOCK aborted the transaction, so who holds it has to be
    # asked in a new one. Nothing was written either way.
    conn.rollback()
    with conn.cursor() as outro:
        escritores = writer_sessions(outro)
    conn.rollback()
    raise HistoricalBlocked(
        f"ha escritor ativo nas tabelas da recuperacao; nada foi feito. "
        f"Sessoes: {escritores or 'o lock nao estava livre e ninguem aparece '
                                  'em pg_locks agora'}")


def preflight(cur, session_id: int, rows: list[dict], cluster_declarado: str,
              backup: dict | None = None,
              allow_new_drivers: bool = False) -> dict:
    """Every check that must pass before a single row is touched."""
    plano_autorizado = autorizada(session_id)

    cluster = cluster_identifier(cur)
    if cluster != cluster_declarado:
        raise HistoricalBlocked(
            "o cluster do alvo nao e o declarado em --expect-cluster")

    # The backup has to be a pre-image of the database about to be written
    # to, not merely a valid archive of some database at some time.
    if backup is not None:
        prova = backup["_prova"]
        if prova["sessao"].get("session_id") != session_id:
            raise HistoricalBlocked(
                "a evidencia do backup foi capturada para a sessao "
                f"{prova['sessao'].get('session_id')}, nao {session_id}")
        try:
            assert_matches_target(prova, cur, session_id)
        except AttestationInvalid as exc:
            raise HistoricalBlocked(
                f"o backup nao corresponde ao alvo: {exc}") from exc

    identidade = session_identity(cur, session_id)
    if identidade != plano_autorizado.identity:
        raise HistoricalBlocked(
            f"a sessao {session_id} e {identidade}, nao "
            f"{plano_autorizado.identity}")

    if len(rows) != plano_autorizado.cars_in_source:
        raise HistoricalBlocked(
            f"a fonte tem {len(rows)} carros, nao "
            f"{plano_autorizado.cars_in_source}")

    # Creating a row in `drivers` is a write to a global table, so it takes
    # two independent yeses: this session declared in the allowlist that it
    # needs them, and the operator typed the flag. Either alone is a refusal.
    if allow_new_drivers and not plano_autorizado.new_drivers_expected:
        raise HistoricalBlocked(
            f"a sessao {session_id} nao preve criar piloto; "
            "--allow-new-drivers nao se aplica a ela")
    if plano_autorizado.new_drivers_expected and not allow_new_drivers:
        raise HistoricalBlocked(
            f"a sessao {session_id} preve criar "
            f"{plano_autorizado.new_drivers_expected} linhas em `drivers`, "
            "que e uma tabela global; use --allow-new-drivers para autorizar")

    antes = session_state(cur, session_id).as_dict()
    if antes == plano_autorizado.baseline:
        estado = "contaminada"
    elif all(antes[k] == v
             for k, v in plano_autorizado.already_recovered.items()):
        estado = "ja recuperada"
    else:
        raise HistoricalBlocked(
            f"o estado da sessao nao e nenhum dos dois previstos: {antes}; "
            f"contaminada {plano_autorizado.baseline}, ja recuperada "
            f"{plano_autorizado.already_recovered}")

    plano = plan_recovery(cur, session_id, rows)
    assert_complete_match(plano)
    if len(plano.matched) != plano_autorizado.cars_in_source or plano.zero_lap:
        raise HistoricalBlocked(
            f"o plano nao e o pareamento completo de "
            f"{plano_autorizado.cars_in_source} carros: "
            f"{len(plano.matched)} pareados, {len(plano.zero_lap)} sem voltas")

    remover = removable_cars(cur, session_id, sorted(plano.matched.values()))
    referencias = external_references(cur, session_id, remover)
    pendentes = {k: v for k, v in referencias.items() if v}
    if pendentes:
        raise HistoricalBlocked(
            f"carros a remover ainda sao referenciados: {pendentes}")

    digest, quantas = other_sessions_digest(cur, session_id)
    pilotos_antes = drivers_total(cur)

    # How many rows `drivers` is expected to gain depends on where this run
    # starts, not only on the session. A re-run over an already recovered
    # session finds every published name already there and must create
    # none — `MEASURED`: the first version of this invariant compared
    # against the session's declared twelve and rolled back a correct
    # second run. Fail closed is right; failing closed on the right number
    # is the point.
    previstos = (plano_autorizado.new_drivers_expected
                 if estado == "contaminada" else 0)

    cur.execute("SELECT pg_try_advisory_xact_lock(%s)",
                (plano_autorizado.lock_key,))
    if not cur.fetchone()[0]:
        raise HistoricalBlocked(
            "outra recuperacao desta sessao esta em andamento")

    return {
        "cluster": cluster,
        "drivers_antes": pilotos_antes,
        "pilotos_novos_previstos": previstos,
        "identidade_da_sessao": list(identidade),
        "estado_inicial": estado,
        "antes": antes,
        "pareados": len(plano.matched),
        "cars_a_remover": len(remover),
        "cars_a_preservar": sorted(plano.matched.values()),
        "ids_dos_cars_a_remover": remover,
        "referencias_externas": referencias,
        "outras_sessoes": {"quantas": quantas, "digest": digest},
        "plano": plano,
    }


# ── Post-write invariants ────────────────────────────────────

def post_invariants(cur, session_id: int, pre: dict,
                    pilotos_criados: int) -> list[str]:
    """
    What must be true before the commit. An empty list is the only pass.

    `pilotos_criados` is what the write path reports having created, not the
    difference between two counts of `drivers`. `MEASURED` on a disposable
    copy: a single unrelated `INSERT INTO drivers` from another connection,
    during the run, made the difference read thirteen and rolled back a
    correct recovery, blaming a driver that had nothing to do with this
    session. The table is global; its size is not a measurement of what this
    transaction did.
    """
    plano_autorizado = autorizada(session_id)
    falhas: list[str] = []

    depois = session_state(cur, session_id).as_dict()
    for chave, esperado in plano_autorizado.expected_after.items():
        if depois[chave] != esperado:
            falhas.append(f"{chave} = {depois[chave]}, esperado {esperado}")

    if depois["result_drivers"] <= 0:
        falhas.append("nenhum piloto vinculado")

    cur.execute("""
        SELECT count(*) FROM (
          SELECT DISTINCT car_id FROM laps WHERE session_id = %(s)s
        ) c WHERE NOT EXISTS (
          SELECT 1 FROM results r
           WHERE r.session_id = %(s)s AND r.car_id = c.car_id)
    """, {"s": session_id})
    sem_resultado = cur.fetchone()[0]
    if sem_resultado:
        falhas.append(f"{sem_resultado} carros com voltas ficaram sem resultado")

    cur.execute("""
        SELECT count(*) FROM results r
         WHERE r.session_id = %s AND r.position IS NULL AND r.status = 'Classified'
    """, (session_id,))
    classificado_sem_posicao = cur.fetchone()[0]
    if classificado_sem_posicao:
        falhas.append(
            f"{classificado_sem_posicao} linhas Classified sem posicao")

    # Provenance is an invariant, not a side effect: a row written without
    # the URL of the publication it came from is the state this path exists
    # to leave behind, and the check is against the allowlist's URL rather
    # than against "not null".
    cur.execute("""
        SELECT count(*) FILTER (WHERE source_url IS DISTINCT FROM %s),
               count(*)
          FROM results WHERE session_id = %s
    """, (plano_autorizado.source_url, session_id))
    sem_proveniencia, total = cur.fetchone()
    if sem_proveniencia:
        falhas.append(
            f"{sem_proveniencia} de {total} linhas sem a URL da publicacao "
            "em source_url")

    # Exactly the declared number, in either direction. One more means the
    # source or the database is not what was rehearsed; one fewer means a
    # slot silently went unlinked.
    if pilotos_criados != pre["pilotos_novos_previstos"]:
        falhas.append(
            f"{pilotos_criados} pilotos criados, esperado "
            f"{pre['pilotos_novos_previstos']}")

    digest, quantas = other_sessions_digest(cur, session_id)
    if digest != pre["outras_sessoes"]["digest"]:
        falhas.append("outra sessao foi alterada")
    if quantas != pre["outras_sessoes"]["quantas"]:
        falhas.append(f"o numero de sessoes mudou: {quantas}")

    return falhas


def compare_with_source(cur, session_id: int, rows: list[dict]) -> dict:
    """Field by field, what was written against what the source publishes."""
    from load_classification import (
        as_float, as_int, field, normalize_status, parse_gap, parse_lap_time,
        parse_race_time,
    )

    cur.execute("""
        SELECT ca.number, r.position, r.status::text, r.laps_completed,
               r.total_time_raw, r.total_time_s, r.gap_to_first_raw,
               r.gap_to_first_s, r.gap_to_prev_s, r.fl_lap_number,
               r.fl_time_raw, r.fl_time_s, r.fl_kph,
               ca.vehicle, ca.car_class, ca.tires
          FROM results r JOIN cars ca ON ca.id = r.car_id
         WHERE r.session_id = %s
    """, (session_id,))
    banco = {linha[0]: linha[1:] for linha in cur.fetchall()}

    colunas = ["position", "status", "laps_completed", "total_time_raw",
               "total_time_s", "gap_to_first_raw", "gap_to_first_s",
               "gap_to_prev_s", "fl_lap_number", "fl_time_raw", "fl_time_s",
               "fl_kph", "vehicle", "car_class", "tires"]

    divergencias = []
    campos = 0
    for row in rows:
        numero = row.get("NUMBER", "").strip()
        if numero not in banco:
            divergencias.append([numero, "ausente no banco", None, None])
            continue
        esperado = [
            as_int(field(row, "POSITION")),
            normalize_status(field(row, "STATUS")),
            as_int(field(row, "LAPS")),
            field(row, "TOTAL_TIME") or None,
            parse_race_time(field(row, "TOTAL_TIME")),
            field(row, "GAP_FIRST") or None,
            parse_gap(field(row, "GAP_FIRST")),
            parse_gap(field(row, "GAP_PREVIOUS")),
            as_int(field(row, "FL_LAPNUM")),
            field(row, "FL_TIME") or None,
            parse_lap_time(field(row, "FL_TIME")),
            as_float(field(row, "FL_KPH")),
            field(row, "VEHICLE") or None,
            field(row, "CLASS") or None,
            (row.get("TIRES") or row.get("TYRES") or "").strip() or None,
        ]
        for nome, quero, tenho in zip(colunas, esperado, banco[numero]):
            campos += 1
            if quero is not None and hasattr(tenho, "quantize"):
                igual = abs(float(tenho) - float(quero)) < 0.0005
            else:
                igual = quero == tenho
            if not igual:
                divergencias.append([numero, nome, quero, repr(tenho)])

    sobrando = sorted(set(banco) - {r.get("NUMBER", "").strip() for r in rows})
    return {"campos": campos, "divergencias": divergencias,
            "numeros_fora_da_fonte": sobrando}


# ── The operator ─────────────────────────────────────────────

def confirm(pre: dict, session_id: int, entrada=None, saida=None) -> None:
    """
    Two answers, typed, matching what the target reports.

    This is the gate no flag can open ahead of time. It is asked after the
    plan is known and printed, so what the operator confirms is a plan they
    have just read, and before anything is written.
    """
    entrada = entrada or sys.stdin
    saida = saida or sys.stdout

    def perguntar(rotulo: str, esperado: str) -> None:
        print(rotulo, file=saida, flush=True)
        resposta = entrada.readline()
        if not resposta:
            raise HistoricalBlocked(f"confirmacao ausente: {rotulo}")
        if resposta.strip() != esperado:
            raise HistoricalBlocked(f"confirmacao invalida: {rotulo}")

    perguntar("confirme o numero da sessao a recuperar:", str(session_id))
    perguntar("confirme o system_identifier do cluster de destino:",
              pre["cluster"])


# ── The run ──────────────────────────────────────────────────

def describe(pre: dict, backup: dict, fonte: dict, saida,
             session_id: int) -> None:
    print("", file=saida)
    print("PLANO", file=saida)
    print(f"  alvo            cluster {pre['cluster']}", file=saida)
    print(f"  sessao          {session_id} — "
          f"{' / '.join(pre['identidade_da_sessao'])}", file=saida)
    print(f"  fonte           {fonte['sha256'][:16]}… "
          f"({fonte['bytes']} bytes)", file=saida)
    print(f"  proveniencia    {fonte['url']}", file=saida)
    print(f"  backup          {backup['caminho']} "
          f"({backup['bytes']} bytes, {backup['sha256'][:16]}…)", file=saida)
    print(f"  evidencia       capturado {backup['capturado_em']} "
          f"({backup['idade_horas']} h), cluster "
          f"{backup['cluster_de_origem']}, restauracao verificada em "
          f"{backup['restauracao']['cluster_de_destino']}", file=saida)
    print(f"  estado          {pre['estado_inicial']}", file=saida)
    print(f"  antes           {pre['antes']}", file=saida)
    print(f"  pareados        {pre['pareados']} carros, preservados por id",
          file=saida)
    print(f"  a remover       {pre['cars_a_remover']} cars e os results deles",
          file=saida)
    print(f"  pilotos novos   {pre['pilotos_novos_previstos']} previstos",
          file=saida)
    print(f"  referencias     {pre['referencias_externas']}", file=saida)
    print(f"  outras sessoes  {pre['outras_sessoes']['quantas']}, digest "
          f"{pre['outras_sessoes']['digest']}", file=saida)
    print("", file=saida)


def execute(cfg: dict, session_id: int, csv_path: Path, backup_path: Path,
            cluster_declarado: str, entrada=None, saida=None,
            falha_simulada: str | None = None,
            max_age_hours: int = DEFAULT_MAX_AGE_HOURS,
            allow_new_drivers: bool = False, dry_run: bool = False) -> dict:
    """
    One transaction: validate, write, validate again, commit or roll back.

    `dry_run` stops after the plan is known and printed, before the operator
    is asked anything and before a single row is touched, and rolls back the
    read-only transaction it opened. It is the same code path up to that
    point — not a second implementation of the checks.

    `falha_simulada` exists for the integrated test, which has to exercise
    the rollback with the same machinery the real run uses. It never makes
    the run write more than it otherwise would; it only makes it fail.
    """
    import psycopg2

    saida = saida or sys.stdout
    plano_autorizado = autorizada(session_id)
    relatorio: dict = {
        "sessao": session_id,
        "comecou_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "transacao": "nao iniciada",
        "modo": "dry-run" if dry_run else "escrita",
    }

    backup = backup_evidence(backup_path, max_age_hours=max_age_hours)
    fonte = source_evidence(csv_path, plano_autorizado)
    rows = read_csv(csv_path)
    relatorio["backup"] = {k: v for k, v in backup.items() if k != "_prova"}
    relatorio["fonte"] = fonte

    conn = psycopg2.connect(connect_timeout=5, **cfg)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            # One snapshot for the whole run. At READ COMMITTED each
            # statement takes a new one, so the state `preflight` validated
            # would not be the state the write operates on, and the
            # invariants afterwards would be reading a third.
            cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")

            # And before that snapshot is taken, the door. A dry-run holds
            # nothing and instead says who would have to stop.
            if dry_run:
                relatorio["escritores_concorrentes"] = writer_sessions(cur)
            else:
                lock_out_writers(conn, cur)

            pre = preflight(cur, session_id, rows, cluster_declarado, backup,
                            allow_new_drivers)
            plano = pre.pop("plano")
            relatorio["pre_validacao"] = {
                k: v for k, v in pre.items() if k != "ids_dos_cars_a_remover"}
            relatorio["ids_dos_cars_removidos"] = pre["ids_dos_cars_a_remover"]

            describe(pre, backup, fonte, saida, session_id)

            if dry_run:
                conn.rollback()
                relatorio["transacao"] = "rollback — dry-run, nada escrito"
                return relatorio

            confirm(pre, session_id, entrada, saida)

            relatorio["transacao"] = "aberta"
            relatorio["recuperacao"] = recover_session(
                cur, session_id, rows, plano_autorizado.series, plano,
                allow_new_drivers=allow_new_drivers,
                source_url=plano_autorizado.source_url)

            if falha_simulada:
                raise HistoricalBlocked(f"falha simulada: {falha_simulada}")

            falhas = post_invariants(
                cur, session_id, pre,
                relatorio["recuperacao"]["pilotos_criados"])
            relatorio["invariantes"] = falhas or ["todas satisfeitas"]
            if falhas:
                raise HistoricalBlocked(
                    "invariantes nao satisfeitas: " + "; ".join(falhas))

            relatorio["depois"] = session_state(cur, session_id).as_dict()
            relatorio["comparacao_com_a_fonte"] = compare_with_source(
                cur, session_id, rows)
            if relatorio["comparacao_com_a_fonte"]["divergencias"]:
                raise HistoricalBlocked(
                    "a comparacao com a fonte encontrou divergencias")

        conn.commit()
        relatorio["transacao"] = "commit"
    except BaseException as exc:
        conn.rollback()
        # A refusal before the write is not a rollback, and calling it one
        # tells the operator something was attempted. Only a run that had
        # already opened the write reports as rolled back.
        if relatorio["transacao"] != "aberta":
            relatorio["transacao"] = "recusada antes da escrita"
            relatorio["motivo"] = str(exc)
            raise
        relatorio["transacao"] = "rollback"
        relatorio["motivo"] = str(exc)
        relatorio.setdefault("depois", None)
        raise HistoricalRecoveryFailed(relatorio) from exc
    finally:
        conn.close()
        relatorio["terminou_em"] = datetime.now(timezone.utc).isoformat(
            timespec="seconds")

    return relatorio


# ── CLI ──────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--session-id", type=int, required=True)
    ap.add_argument("--csv", type=Path, required=True)
    ap.add_argument("--backup", type=Path, required=True,
                    help="pg_dump -Fc taken immediately before this run")
    ap.add_argument("--expect-cluster", required=True,
                    help="system_identifier of the cluster being written to")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--dbname", default="openwec")
    ap.add_argument("--user", default="openwec")
    ap.add_argument("--password", default="")
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--max-backup-age-hours", type=int,
                    default=DEFAULT_MAX_AGE_HOURS,
                    help="how old the attestation may be (default: 6)")
    ap.add_argument("--dry-run", action="store_true",
                    help="run every check and print the plan; write nothing "
                         "and ask nothing")
    ap.add_argument("--allow-new-drivers", action="store_true",
                    help="autoriza criar as linhas em `drivers` que a "
                         "allowlist declara para esta sessao; recusado onde "
                         "a sessao nao preve nenhuma")
    ap.add_argument("--simulate-failure")
    args = ap.parse_args()

    cfg = {"host": args.host, "port": args.port, "dbname": args.dbname,
           "user": args.user, "password": args.password}

    try:
        relatorio = execute(cfg, args.session_id, args.csv, args.backup,
                            args.expect_cluster,
                            falha_simulada=args.simulate_failure,
                            max_age_hours=args.max_backup_age_hours,
                            allow_new_drivers=args.allow_new_drivers,
                            dry_run=args.dry_run)
    except HistoricalRecoveryFailed as exc:
        args.report.write_text(
            json.dumps(exc.relatorio, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8")
        print(f"ROLLBACK: {exc}")
        return 2
    except HistoricalBlocked as exc:
        print(f"BLOQUEADO: {exc}")
        return 2
    except RecoveryBlocked as exc:
        print(f"BLOQUEADO: {exc}")
        return 2

    args.report.write_text(
        json.dumps(relatorio, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8")

    if args.dry_run:
        print(f"DRY-RUN — nada foi escrito; relatorio em {args.report}")
        return 0

    print(f"COMMIT — relatorio em {args.report}")
    print(f"  depois: {relatorio['depois']}")
    print(f"  campos comparados: "
          f"{relatorio['comparacao_com_a_fonte']['campos']}, divergencias: "
          f"{len(relatorio['comparacao_com_a_fonte']['divergencias'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
