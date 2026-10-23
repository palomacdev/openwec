"""
OpenWEC — what a backup has to prove about itself before a recovery runs.

A file that begins with `PGDMP` and hashes to something is not evidence. It
says nothing about **which** database it came from, **when**, or whether it
restores at all. A recovery that trusts it is trusting a filename.

So the dump carries a sidecar — `<dump>.attestation.json` — written in two
steps, each of which has to actually happen:

  `capture`         reads the source cluster's identifier, the moment, the
                    counts of the tables a recovery touches, and the hash of
                    the dump that was just taken;
  `verify-restore`  restores that dump somewhere disposable, counts the same
                    tables there, and records whether they matched.

The historical path then refuses a backup whose attestation is missing, does
not hash to the file beside it, records no successful restore, is older than
the window allowed, or describes counts that no longer match the database
about to be written to. That last one is the point: a backup is a pre-image
or it is nothing.

This is deliberately a sidecar and not a service. Two functions write it, one
reads it, and nothing here schedules, rotates or uploads anything.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# The tables a recovery of a contaminated session touches, plus the ones it
# reads identity from. `api_key_requests` is deliberately absent: it moves on
# its own while the API serves, and a backup is not stale because of it.
ESSENTIAL_TABLES = ("sessions", "cars", "results", "result_drivers",
                    "laps", "drivers", "teams")

ATTESTATION_SUFFIX = ".attestation.json"
DEFAULT_MAX_AGE_HOURS = 6
SCHEMA_VERSION = 1


class AttestationInvalid(Exception):
    """Raised when the evidence is missing, unverifiable or stale."""


def attestation_path(dump: Path) -> Path:
    return dump.with_name(dump.name + ATTESTATION_SUFFIX)


def sha256_of(caminho: Path) -> str:
    digest = hashlib.sha256()
    with caminho.open("rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(1 << 20), b""):
            digest.update(bloco)
    return digest.hexdigest()


def cluster_of(cur) -> str:
    cur.execute("SELECT system_identifier::text FROM pg_control_system()")
    linha = cur.fetchone()
    if not linha or not linha[0]:
        raise AttestationInvalid("o cluster nao informa system_identifier")
    return str(linha[0])


def counts_of(cur) -> dict:
    contagens = {}
    for tabela in ESSENTIAL_TABLES:
        cur.execute(f"SELECT count(*) FROM {tabela}")
        contagens[tabela] = cur.fetchone()[0]
    return contagens


def cluster_and_counts(cur) -> tuple[str, dict]:
    return cluster_of(cur), counts_of(cur)


def session_counts(cur, session_id: int) -> dict:
    cur.execute("""
        SELECT (SELECT count(*) FROM results WHERE session_id = %(s)s),
               (SELECT count(*) FROM laps    WHERE session_id = %(s)s)
    """, {"s": session_id})
    results, laps = cur.fetchone()
    return {"session_id": session_id, "results": results, "laps": laps}


# ── Writing it ───────────────────────────────────────────────

def capture(cur, dump: Path, session_id: int, agora=None) -> dict:
    """
    Step one, run against the source right after the dump is taken.

    The hash is of the file as it is on disk now, so an attestation can never
    describe a dump that was replaced afterwards.
    """
    if not dump.exists() or dump.stat().st_size == 0:
        raise AttestationInvalid(f"dump ausente ou vazio: {dump}")

    cluster, contagens = cluster_and_counts(cur)
    agora = agora or datetime.now(timezone.utc)

    prova = {
        "schema": SCHEMA_VERSION,
        "dump": dump.name,
        "sha256": sha256_of(dump),
        "bytes": dump.stat().st_size,
        "cluster_de_origem": cluster,
        "capturado_em": agora.astimezone(timezone.utc).isoformat(
            timespec="seconds"),
        "contagens": contagens,
        "sessao": session_counts(cur, session_id),
        "restauracao": None,
    }
    attestation_path(dump).write_text(
        json.dumps(prova, ensure_ascii=False, indent=2), encoding="utf-8")
    return prova


def record_restore(cur, dump: Path, agora=None) -> dict:
    """
    Step two, run against the disposable instance the dump was restored into.

    It does not take anyone's word for the restore: it counts the same tables
    there and writes down whether they matched, with the cluster it was
    restored into so the two can never be confused.
    """
    prova = load(dump)
    cluster, contagens = cluster_and_counts(cur)

    if cluster == prova["cluster_de_origem"]:
        raise AttestationInvalid(
            "a restauracao foi verificada no proprio cluster de origem")

    divergem = {t: (prova["contagens"][t], contagens[t])
                for t in ESSENTIAL_TABLES
                if prova["contagens"][t] != contagens[t]}

    agora = agora or datetime.now(timezone.utc)
    prova["restauracao"] = {
        "ok": not divergem,
        "cluster_de_destino": cluster,
        "verificada_em": agora.astimezone(timezone.utc).isoformat(
            timespec="seconds"),
        "contagens": contagens,
        "divergencias": divergem,
    }
    attestation_path(dump).write_text(
        json.dumps(prova, ensure_ascii=False, indent=2), encoding="utf-8")
    return prova


# ── Reading it ───────────────────────────────────────────────

def load(dump: Path) -> dict:
    caminho = attestation_path(dump)
    if not caminho.exists():
        raise AttestationInvalid(
            f"o backup nao tem evidencia: {caminho.name} nao existe")
    try:
        prova = json.loads(caminho.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise AttestationInvalid(f"evidencia ilegivel: {exc}") from exc
    if not isinstance(prova, dict):
        raise AttestationInvalid("evidencia nao e um objeto")
    return prova


def validate(dump: Path, agora=None,
             max_age_hours: int = DEFAULT_MAX_AGE_HOURS) -> dict:
    """
    Everything that can be checked without touching the target database.

    Order matters only in that the hash comes early: an attestation that does
    not describe this file has nothing useful to say about anything else.
    """
    prova = load(dump)

    if prova.get("schema") != SCHEMA_VERSION:
        raise AttestationInvalid(
            f"evidencia de schema {prova.get('schema')}, esperado "
            f"{SCHEMA_VERSION}")

    real = sha256_of(dump)
    if prova.get("sha256") != real:
        raise AttestationInvalid(
            "a evidencia nao descreve este arquivo: o sha256 difere")

    for campo in ("cluster_de_origem", "capturado_em", "contagens", "sessao"):
        if not prova.get(campo):
            raise AttestationInvalid(f"evidencia sem {campo}")

    faltando = [t for t in ESSENTIAL_TABLES if t not in prova["contagens"]]
    if faltando:
        raise AttestationInvalid(
            "evidencia sem contagem de: " + ", ".join(faltando))

    restauracao = prova.get("restauracao")
    if not restauracao:
        raise AttestationInvalid(
            "a restauracao do backup nao foi verificada")
    if not restauracao.get("ok"):
        raise AttestationInvalid(
            f"a restauracao falhou: {restauracao.get('divergencias')}")
    if restauracao.get("cluster_de_destino") == prova["cluster_de_origem"]:
        raise AttestationInvalid(
            "a restauracao foi verificada no proprio cluster de origem")

    agora = (agora or datetime.now(timezone.utc)).astimezone(timezone.utc)
    capturado = datetime.fromisoformat(prova["capturado_em"])
    idade = agora - capturado
    if idade > timedelta(hours=max_age_hours):
        raise AttestationInvalid(
            f"o backup tem {idade}, acima do limite de {max_age_hours} h")
    if idade < timedelta(seconds=-60):
        raise AttestationInvalid("o backup foi capturado no futuro")

    prova["idade_horas"] = round(idade.total_seconds() / 3600, 3)
    return prova


def assert_matches_target(prova: dict, cur, session_id: int) -> None:
    """
    The check the hash cannot make: is this a pre-image of what we will write?

    A backup of a different cluster, or of the same cluster before something
    else changed it, is not a way back from the recovery about to happen.
    """
    # The cluster first: counting seven tables to then say the backup is of
    # another database would be work done to reach a conclusion already
    # available.
    cluster = cluster_of(cur)
    if cluster != prova["cluster_de_origem"]:
        raise AttestationInvalid(
            f"o backup e do cluster {prova['cluster_de_origem']}, e o alvo "
            f"e {cluster}")

    contagens = counts_of(cur)
    divergem = {t: (prova["contagens"][t], contagens[t])
                for t in ESSENTIAL_TABLES
                if prova["contagens"][t] != contagens[t]}
    if divergem:
        raise AttestationInvalid(
            f"o banco mudou desde o backup: {divergem}")

    sessao = session_counts(cur, session_id)
    if sessao != prova["sessao"]:
        raise AttestationInvalid(
            f"a sessao mudou desde o backup: {sessao} != {prova['sessao']}")


# ── CLI ──────────────────────────────────────────────────────

def _connect(args):
    import psycopg2
    return psycopg2.connect(
        connect_timeout=5, host=args.host, port=args.port, dbname=args.dbname,
        user=args.user, password=args.password,
        options="-c default_transaction_read_only=on")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("acao", choices=["capture", "verify-restore", "show"])
    ap.add_argument("--dump", type=Path, required=True)
    ap.add_argument("--session-id", type=int, default=2226)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int)
    ap.add_argument("--dbname", default="openwec")
    ap.add_argument("--user", default="openwec")
    ap.add_argument("--password", default="")
    args = ap.parse_args()

    try:
        if args.acao == "show":
            prova = validate(args.dump)
        else:
            conn = _connect(args)
            try:
                with conn.cursor() as cur:
                    prova = (capture(cur, args.dump, args.session_id)
                             if args.acao == "capture"
                             else record_restore(cur, args.dump))
            finally:
                conn.rollback()
                conn.close()
    except AttestationInvalid as exc:
        print(f"EVIDENCIA INVALIDA: {exc}")
        return 2

    print(json.dumps(prova, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
