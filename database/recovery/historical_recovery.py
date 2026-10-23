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

One session: **2226**, Le Mans Cup 2018, Paul Ricard. There is no batch mode,
no `--all`, and no way to name another session; the identifier is compared
against a constant.

## The gates

  1. the session asked for is 2226;
  2. a backup file exists, is a PostgreSQL custom-format archive, and its
     hash is recorded in the report;
  3. the cluster the operator says they are writing to is the one
     `pg_control_system()` reports — not a host, not a port, not a name;
  4. the session's identity in the database is the one that was rehearsed:
     series, season, event and session key;
  5. the source file hashes to the bytes that were rehearsed against;
  6. the current state of the session matches the baseline measured before
     the rehearsals — if anything moved since, the run stops rather than
     adapt to it;
  7. the plan the recoverer produces is a complete 28-car match;
  8. nothing outside this session refers to a car the plan would remove;
  9. the advisory lock is free, so two runs cannot overlap;
 10. the operator confirms the session and the cluster, interactively.

Everything happens in one transaction. After the write and before the commit
the invariants are checked again against the database itself; a single
failure rolls the whole thing back. A `ROLLBACK` is not a recovery plan after
a `COMMIT`, which is why gate 2 exists and why the report names the backup.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "loader"))

from load_classification import read_csv  # noqa: E402
from database.recovery.recover_classification import (  # noqa: E402
    RecoveryBlocked,
    assert_complete_match,
    plan_recovery,
    recover_session,
    session_state,
)

# The one session this path knows how to recover.
ALLOWED_SESSION = 2226
SERIES = "LEMANSCUP"

SOURCE_URL = (
    "https://lemanscup.alkamelsystems.com/Results/02_2018/01_PAUL%20RICARD/"
    "218_Michelin%20Le%20Mans%20Cup/201804141605_Race/Hour%202/"
    "05_Classification_Race.CSV"
)
SOURCE_SHA256 = "034e73c53cea3fdc5f67f636eeade3dabb6894372fc1409fd5839973f472e29c"
CARS_IN_SOURCE = 28

# The session as the catalogue and the database name it. `MEASURED` on the
# historical instance; a row that answers to a different identity is not the
# session this path was rehearsed against.
SESSION_IDENTITY = ("LEMANSCUP", "02_2018", "01_PAUL RICARD", "201804141605_Race")

# The state measured before the rehearsals, on the historical instance.
# Anything else means the database moved since, and the run stops.
BASELINE = {
    "results": 3108,
    "cars_in_results": 3108,
    "result_drivers": 0,
    "laps": 1554,
    "cars_in_laps": 28,
}

# The state a successful run leaves behind. It is accepted as a starting
# point too, so that running the same recovery twice is a no-op rather than a
# refusal — the underlying routine is idempotent and was rehearsed four
# times. Accepting it is not adapting to an unexpected state: it is the only
# other state this path has ever produced. `result_drivers` is left out of
# the comparison because driver linking depends on what `drivers` already
# holds, which the rehearsals showed varying between instances.
ALREADY_RECOVERED = {
    "results": 28,
    "cars_in_results": 28,
    "laps": 1554,
    "cars_in_laps": 28,
}

EXPECTED_AFTER = dict(ALREADY_RECOVERED)

# One key, one recovery. `pg_try_advisory_xact_lock` is released when the
# transaction ends, so a crashed run does not leave the lock behind.
LOCK_KEY = 2226_0001

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


def backup_evidence(caminho: Path) -> dict:
    """
    What the report will say about the backup, and the refusal if there isn't one.

    The file has to be a PostgreSQL custom-format archive, which is what
    `pg_restore` reads and what the revert procedure assumes. A plain SQL
    dump, a truncated download or an empty file would all pass a bare
    existence check and none of them would restore.
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

    digest = hashlib.sha256()
    with caminho.open("rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(1 << 20), b""):
            digest.update(bloco)

    return {"caminho": str(caminho), "bytes": tamanho,
            "sha256": digest.hexdigest()}


def source_evidence(caminho: Path) -> dict:
    dados = caminho.read_bytes()
    sha = hashlib.sha256(dados).hexdigest()
    if sha != SOURCE_SHA256:
        raise HistoricalBlocked(
            f"a fonte nao e a que foi ensaiada: sha256 {sha}")
    return {"caminho": str(caminho), "bytes": len(dados), "sha256": sha,
            "url": SOURCE_URL}


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


def preflight(cur, session_id: int, rows: list[dict], cluster_declarado: str) -> dict:
    """Every check that must pass before a single row is touched."""
    if session_id != ALLOWED_SESSION:
        raise HistoricalBlocked(
            f"este caminho recupera apenas a sessao {ALLOWED_SESSION}; "
            f"{session_id} nao esta previsto")

    cluster = cluster_identifier(cur)
    if cluster != cluster_declarado:
        raise HistoricalBlocked(
            "o cluster do alvo nao e o declarado em --expect-cluster")

    identidade = session_identity(cur, session_id)
    if identidade != SESSION_IDENTITY:
        raise HistoricalBlocked(
            f"a sessao {session_id} e {identidade}, nao {SESSION_IDENTITY}")

    if len(rows) != CARS_IN_SOURCE:
        raise HistoricalBlocked(
            f"a fonte tem {len(rows)} carros, nao {CARS_IN_SOURCE}")

    antes = session_state(cur, session_id).as_dict()
    if antes == BASELINE:
        estado = "contaminada"
    elif all(antes[k] == v for k, v in ALREADY_RECOVERED.items()):
        estado = "ja recuperada"
    else:
        raise HistoricalBlocked(
            f"o estado da sessao nao e nenhum dos dois previstos: {antes}; "
            f"contaminada {BASELINE}, ja recuperada {ALREADY_RECOVERED}")

    plano = plan_recovery(cur, session_id, rows)
    assert_complete_match(plano)
    if len(plano.matched) != CARS_IN_SOURCE or plano.zero_lap:
        raise HistoricalBlocked(
            f"o plano nao e o pareamento completo de {CARS_IN_SOURCE} carros: "
            f"{len(plano.matched)} pareados, {len(plano.zero_lap)} sem voltas")

    remover = removable_cars(cur, session_id, sorted(plano.matched.values()))
    referencias = external_references(cur, session_id, remover)
    pendentes = {k: v for k, v in referencias.items() if v}
    if pendentes:
        raise HistoricalBlocked(
            f"carros a remover ainda sao referenciados: {pendentes}")

    digest, quantas = other_sessions_digest(cur, session_id)

    cur.execute("SELECT pg_try_advisory_xact_lock(%s)", (LOCK_KEY,))
    if not cur.fetchone()[0]:
        raise HistoricalBlocked(
            "outra recuperacao desta sessao esta em andamento")

    return {
        "cluster": cluster,
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

def post_invariants(cur, session_id: int, pre: dict) -> list[str]:
    """What must be true before the commit. An empty list is the only pass."""
    falhas: list[str] = []

    depois = session_state(cur, session_id).as_dict()
    for chave, esperado in EXPECTED_AFTER.items():
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

def describe(pre: dict, backup: dict, fonte: dict, saida) -> None:
    print("", file=saida)
    print("PLANO", file=saida)
    print(f"  alvo            cluster {pre['cluster']}", file=saida)
    print(f"  sessao          {ALLOWED_SESSION} — "
          f"{' / '.join(pre['identidade_da_sessao'])}", file=saida)
    print(f"  fonte           {fonte['sha256'][:16]}… "
          f"({fonte['bytes']} bytes)", file=saida)
    print(f"  backup          {backup['caminho']} "
          f"({backup['bytes']} bytes, {backup['sha256'][:16]}…)", file=saida)
    print(f"  estado          {pre['estado_inicial']}", file=saida)
    print(f"  antes           {pre['antes']}", file=saida)
    print(f"  pareados        {pre['pareados']} carros, preservados por id",
          file=saida)
    print(f"  a remover       {pre['cars_a_remover']} cars e os results deles",
          file=saida)
    print(f"  referencias     {pre['referencias_externas']}", file=saida)
    print(f"  outras sessoes  {pre['outras_sessoes']['quantas']}, digest "
          f"{pre['outras_sessoes']['digest']}", file=saida)
    print("", file=saida)


def execute(cfg: dict, session_id: int, csv_path: Path, backup_path: Path,
            cluster_declarado: str, entrada=None, saida=None,
            falha_simulada: str | None = None) -> dict:
    """
    One transaction: validate, write, validate again, commit or roll back.

    `falha_simulada` exists for the integrated test, which has to exercise
    the rollback with the same machinery the real run uses. It never makes
    the run write more than it otherwise would; it only makes it fail.
    """
    import psycopg2

    saida = saida or sys.stdout
    relatorio: dict = {
        "sessao": session_id,
        "comecou_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "transacao": "nao iniciada",
    }

    backup = backup_evidence(backup_path)
    fonte = source_evidence(csv_path)
    rows = read_csv(csv_path)
    relatorio["backup"] = backup
    relatorio["fonte"] = fonte

    conn = psycopg2.connect(connect_timeout=5, **cfg)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            pre = preflight(cur, session_id, rows, cluster_declarado)
            plano = pre.pop("plano")
            relatorio["pre_validacao"] = {
                k: v for k, v in pre.items() if k != "ids_dos_cars_a_remover"}
            relatorio["ids_dos_cars_removidos"] = pre["ids_dos_cars_a_remover"]

            describe(pre, backup, fonte, saida)
            confirm(pre, session_id, entrada, saida)

            relatorio["transacao"] = "aberta"
            relatorio["recuperacao"] = recover_session(
                cur, session_id, rows, SERIES, plano)

            if falha_simulada:
                raise HistoricalBlocked(f"falha simulada: {falha_simulada}")

            falhas = post_invariants(cur, session_id, pre)
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
    ap.add_argument("--simulate-failure")
    args = ap.parse_args()

    cfg = {"host": args.host, "port": args.port, "dbname": args.dbname,
           "user": args.user, "password": args.password}

    try:
        relatorio = execute(cfg, args.session_id, args.csv, args.backup,
                            args.expect_cluster,
                            falha_simulada=args.simulate_failure)
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
    print(f"COMMIT — relatorio em {args.report}")
    print(f"  depois: {relatorio['depois']}")
    print(f"  campos comparados: "
          f"{relatorio['comparacao_com_a_fonte']['campos']}, divergencias: "
          f"{len(relatorio['comparacao_com_a_fonte']['divergencias'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
