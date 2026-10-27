"""
OpenWEC — apply an approved deduplication plan, or refuse to.

The decision is not taken here. `dedup_plan` decides which results are
copies and which copy stays; this module reads that plan, proves the
database still matches it, backs up what is about to go, deletes exactly
those ids, checks the result, and commits — or rolls back.

Dry-run is the default. `--apply` needs `--confirm <plan_digest>`, so the
authorisation names the plan it authorises.

## Refusing is the normal outcome

Before anything is deleted the plan is rebuilt from the live database and
compared field by field: the digest, the session set, and for each session
the factor, the totals, the preserved and removable ids, the content digest
and the driver-link digest. Counts are not enough — two different sessions
can hold the same number of rows — so the ids and the digests are compared
as sets and strings. Any difference refuses the run.

The foreign keys onto `results.id` are read again at execution time. A key
this module does not model, or a removal candidate reached by one, refuses
the run rather than deleting around it.

A second run of the same plan refuses itself: the rows it names are gone,
so the rebuilt plan no longer matches.

## Nothing else writes while this runs

The advisory lock coordinates executors that take it, which an ingestion or
a `psql` session does not. What stops those is
`LOCK TABLE results, result_drivers IN SHARE ROW EXCLUSIVE MODE NOWAIT`,
taken before anything else and held to the end of the transaction. It
conflicts with the ROW EXCLUSIVE that every INSERT, UPDATE and DELETE takes,
and not with the ACCESS SHARE that readers take, so the API keeps serving.

NOWAIT rather than waiting: a queued lock request blocks the readers that
arrive behind it, so waiting for a busy table would take the API down with
it. When the lock is not free the run refuses and names the sessions holding
it, read from `pg_locks`. A dry-run takes no lock and reports those sessions
instead, which is the check to run before opening the window.

## Two backups, and both are conditions

The **cluster dump** is the operational pre-condition: a `pg_dump -Fc` with
the sidecar of `backup_attestation` — the cluster it came from, when it was
taken, the counts it holds, and a restore verified somewhere disposable.
`--apply` refuses without it, refuses one older than the window, and refuses
one whose `results` and `result_drivers` counts no longer match this
database, because a backup that is not a pre-image is not a way back.

The **row backup** is this module's own, and it is a restore test rather
than a file. The rows are written to a sibling path, fsynced, renamed over,
the directory fsynced, read back, and hashed; the bytes on disk have to hash
to what was written and to declare this plan. Then they are inserted into
temporary tables shaped `LIKE` the real ones and compared against the live
rows by a digest computed in the database. Only then is the DELETE issued.
An existing backup file is never overwritten — the name carries the plan
digest, so a file already there belongs to an earlier attempt at this same
plan, and replacing it would destroy the only copy of what that attempt
took.

## The transaction

REPEATABLE READ, so the rebuild and the delete see one snapshot of the
database. A transaction-scoped advisory lock keyed on the series keeps two
executions off the same population. Before the commit, the invariants are
checked against a snapshot taken before the delete: the preserved rows and
their links byte-identical, the blocked groups present, the laps untouched,
the sessions untouched, and the global row count down by exactly the number
of ids in the plan. Anything else rolls back.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from database.recovery.backup_attestation import (  # noqa: E402
    AttestationInvalid,
    DEFAULT_MAX_AGE_HOURS,
    counts_of,
)
from database.recovery.backup_attestation import validate as validate_dump  # noqa: E402
from database.recovery.dedup_plan import build_plan, plan_digest  # noqa: E402

PLAN_CONTRACT = "openwec-dedup-plan-v2"
LOCK_NAME = "openwec-dedup-execute"

# SHARE ROW EXCLUSIVE is the lightest mode that conflicts with ROW EXCLUSIVE,
# which is what INSERT, UPDATE and DELETE take. Readers hold ACCESS SHARE and
# are not blocked, so the API keeps serving while this runs.
WRITE_BLOCKING_LOCK = "SHARE ROW EXCLUSIVE"
LOCK_NOT_AVAILABLE = "55P03"

# The counts the dump has to still describe. A backup taken before other rows
# moved is not a pre-image of what is about to be deleted.
PREIMAGE_TABLES = ("results", "result_drivers")

PLAN_KEYS = ("contrato", "series", "resumo", "plan_digest", "sessions",
             "dependencias_de_results", "results_com_referencia_externa")
SESSION_KEYS = ("session_id", "fator", "total_atual", "total_esperado",
                "ids_preservados", "ids_removiveis", "conteudo_sha256",
                "vinculos_sha256", "bloqueios")

# Compared one by one; a count alone would let two different sessions pass
# for each other.
COMPARED_SESSION_FIELDS = ("fator", "total_atual", "total_esperado",
                           "conteudo_sha256", "vinculos_sha256")


class ExecutionRefused(Exception):
    """Raised with every reason found, never with the first one only."""

    def __init__(self, motivos: list[str]):
        self.motivos = motivos
        super().__init__("; ".join(motivos))


# ── The plan, before any database is opened ──────────────────

def load_plan(caminho: Path) -> dict:
    try:
        plano = json.loads(Path(caminho).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExecutionRefused([f"plano ilegivel: {exc}"]) from exc
    if not isinstance(plano, dict):
        raise ExecutionRefused(["plano nao e um objeto JSON"])
    return plano


def validate_plan(plano: dict) -> dict:
    """
    The plan read as a plan: shape, internal arithmetic, and its own digest.

    Returns the authorised sets. Everything the executor later touches comes
    from here, so an id absent from these is an id the run must refuse.
    """
    motivos: list[str] = []
    faltando = [k for k in PLAN_KEYS if k not in plano]
    if faltando:
        raise ExecutionRefused([f"plano sem os campos {faltando}"])
    if plano["contrato"] != PLAN_CONTRACT:
        raise ExecutionRefused(
            [f"contrato {plano['contrato']!r}; este executor aplica "
             f"{PLAN_CONTRACT!r}"])

    removiveis: set[int] = set()
    preservados: set[int] = set()
    bloqueados: set[int] = set()
    vistos: dict[int, int] = {}

    for s in plano["sessions"]:
        falta = [k for k in SESSION_KEYS if k not in s]
        if falta:
            motivos.append(f"sessao {s.get('session_id')} sem os campos {falta}")
            continue
        sid = s["session_id"]
        rem, pres = list(s["ids_removiveis"]), list(s["ids_preservados"])
        if set(rem) & set(pres):
            motivos.append(f"sessao {sid}: ids em preservados e removiveis")
        if len(rem) != len(set(rem)) or len(pres) != len(set(pres)):
            motivos.append(f"sessao {sid}: id repetido nas listas")
        if len(pres) != s["total_esperado"]:
            motivos.append(f"sessao {sid}: preservados {len(pres)} != "
                           f"total_esperado {s['total_esperado']}")
        if len(pres) + len(rem) != s["total_atual"]:
            motivos.append(f"sessao {sid}: preservados + removiveis != "
                           f"total_atual {s['total_atual']}")
        for i in rem + pres:
            if i in vistos and vistos[i] != sid:
                motivos.append(f"result {i} aparece nas sessoes "
                               f"{vistos[i]} e {sid}")
            vistos[i] = sid
        removiveis.update(rem)
        preservados.update(pres)
        for b in s["bloqueios"]:
            bloqueados.update(b.get("ids", []))

    if removiveis & bloqueados:
        motivos.append("ha id bloqueado na lista de removiveis")

    resumo = plano["resumo"]
    if resumo.get("results_removiveis") != len(removiveis):
        motivos.append(f"resumo diz {resumo.get('results_removiveis')} "
                       f"removiveis; as sessoes somam {len(removiveis)}")
    if resumo.get("results_preservados") != len(preservados):
        motivos.append(f"resumo diz {resumo.get('results_preservados')} "
                       f"preservados; as sessoes somam {len(preservados)}")

    recalculado = plan_digest(plano["sessions"])
    if recalculado != plano["plan_digest"]:
        motivos.append(f"plan_digest nao confere com as listas do arquivo: "
                       f"{plano['plan_digest']} vs {recalculado}")

    if motivos:
        raise ExecutionRefused(motivos)
    return {"removiveis": removiveis, "preservados": preservados,
            "bloqueados": bloqueados,
            "sessoes": [s["session_id"] for s in plano["sessions"]]}


def compare_plans(aprovado: dict, atual: dict) -> list[str]:
    """Every difference between the approved plan and the live one."""
    motivos: list[str] = []
    if aprovado["series"] != atual["series"]:
        motivos.append(f"serie {aprovado['series']} != {atual['series']}")
    if aprovado["plan_digest"] != atual["plan_digest"]:
        motivos.append(f"plan_digest {aprovado['plan_digest']} != "
                       f"{atual['plan_digest']}")

    de_la = {s["session_id"]: s for s in atual["sessions"]}
    de_ca = {s["session_id"]: s for s in aprovado["sessions"]}
    sumidas = sorted(set(de_ca) - set(de_la))
    novas = sorted(set(de_la) - set(de_ca))
    if sumidas:
        motivos.append(f"sessoes do plano ausentes no banco: {sumidas[:10]}")
    if novas:
        motivos.append(f"sessoes no banco fora do plano: {novas[:10]}")

    for sid in sorted(set(de_ca) & set(de_la)):
        a, b = de_ca[sid], de_la[sid]
        for campo in COMPARED_SESSION_FIELDS:
            if a[campo] != b[campo]:
                motivos.append(f"sessao {sid}: {campo} {a[campo]!r} != {b[campo]!r}")
        if list(a["ids_removiveis"]) != list(b["ids_removiveis"]):
            motivos.append(f"sessao {sid}: ids_removiveis mudaram")
        if list(a["ids_preservados"]) != list(b["ids_preservados"]):
            motivos.append(f"sessao {sid}: ids_preservados mudaram")
        if _bloqueios(a) != _bloqueios(b):
            motivos.append(f"sessao {sid}: bloqueios mudaram")
    return motivos


def _bloqueios(sessao: dict) -> list[tuple]:
    return sorted((b["motivo"], tuple(b.get("ids", [])))
                  for b in sessao["bloqueios"])


def compare_dependencies(aprovado: dict, atual: dict) -> list[str]:
    """Nothing new may point at a result since the plan was approved."""
    motivos = []
    def chave(c):
        return (c["tabela"], c["coluna"], c["on_delete"], c["modelada"])
    antes = sorted(map(chave, aprovado["dependencias_de_results"]))
    agora = sorted(map(chave, atual["dependencias_de_results"]))
    if antes != agora:
        motivos.append(f"chaves sobre results mudaram: {antes} -> {agora}")
    nao_modeladas = [c for c in atual["dependencias_de_results"]
                     if not c["modelada"]]
    if nao_modeladas:
        motivos.append(f"chave nao modelada aponta para results: "
                       f"{[(c['tabela'], c['coluna']) for c in nao_modeladas]}")
    if atual["results_com_referencia_externa"]:
        motivos.append(f"{atual['results_com_referencia_externa']} results sao "
                       f"apontados por tabela fora de result_drivers")
    return motivos


# ── Concurrent writers ───────────────────────────────────────

WRITERS_SQL = """
SELECT DISTINCT a.pid, a.usename, a.application_name, a.state,
       l.mode, c.relname
FROM pg_locks l
JOIN pg_class c ON c.oid = l.relation
LEFT JOIN pg_stat_activity a ON a.pid = l.pid
WHERE l.relation IN (to_regclass('results'), to_regclass('result_drivers'))
  AND l.pid <> pg_backend_pid()
  AND l.mode NOT IN ('AccessShareLock', 'RowShareLock')
ORDER BY 1, 6
"""


def writer_sessions(cur) -> list[dict]:
    """
    Who is holding a lock on these tables that a writer would hold.

    ACCESS SHARE and ROW SHARE are left out: those are readers, and readers
    are not what this has to stop. Observational — it says who was there when
    it was asked, and the table lock below is what actually keeps them out.
    """
    cur.execute(WRITERS_SQL)
    return [{"pid": pid, "usuario": usuario, "aplicacao": app,
             "estado": estado, "modo": modo, "tabela": tabela}
            for pid, usuario, app, estado, modo, tabela in cur.fetchall()]


def lock_out_writers(conn, cur) -> None:
    """
    Keep every other writer off these tables for the rest of the transaction.

    NOWAIT on purpose: waiting would queue this request ahead of the readers
    that arrive next, so a busy table would take the API down with it. If the
    lock is not free, the run refuses and says who had it.

    The advisory lock above only coordinates executors that take it. This is
    what stops an ingestion, a recovery or a psql session.
    """
    try:
        cur.execute(f"LOCK TABLE results, result_drivers "
                    f"IN {WRITE_BLOCKING_LOCK} MODE NOWAIT")
        return
    except Exception as exc:                                  # noqa: BLE001
        if getattr(exc, "pgcode", None) != LOCK_NOT_AVAILABLE:
            raise
    # The failed LOCK aborted the transaction, so the question of who holds it
    # has to be asked in a new one. Nothing was written either way.
    conn.rollback()
    with conn.cursor() as outro:
        escritores = writer_sessions(outro)
    conn.rollback()
    raise ExecutionRefused(
        [f"results/result_drivers tem escritor ativo; nada foi feito. "
         f"Sessoes: {escritores or 'o lock nao estava livre e ninguem aparece '
                                   'em pg_locks agora'}"])


# ── Backup ───────────────────────────────────────────────────

COLUMNS_SQL = """
SELECT attname FROM pg_attribute
WHERE attrelid = to_regclass(%s) AND attnum > 0 AND NOT attisdropped
ORDER BY attnum
"""


def table_columns(cur, tabela: str) -> list[str]:
    cur.execute(COLUMNS_SQL, (tabela,))
    return [nome for (nome,) in cur.fetchall()]


def row_digest(cur, tabela: str, ids: list[int], coluna: str = "id") -> str:
    """
    The rows as the database writes them, hashed by the database.

    `(t.*)::text` renders every column through its own output function, so a
    DECIMAL read back from JSON as a string and the DECIMAL it came from
    produce the same text. Comparing in Python would compare the round trip
    instead of the data.

    The aggregate is ordered by that same rendered row and not by the filter
    column, because the filter column is not always unique: `result_drivers`
    is selected by `result_id`, and a result carries one row per driver — in
    the corpus 119.661 of them carry more than one. Ordering by a key with
    ties leaves the order inside a tie undefined, so the same rows read from
    two tables hash differently and a sound backup is refused as unsound.
    The whole rendered row is a total order: equal rows are interchangeable,
    so no tie can change the result.
    """
    cur.execute(
        f'SELECT coalesce(md5(string_agg((t.*)::text, chr(29) '
        f'ORDER BY (t.*)::text)), %s) FROM "{tabela}" t '
        f'WHERE t."{coluna}" = ANY(%s)', ("", list(ids)))
    return cur.fetchone()[0]


def backup_payload(cur, ids: list[int]) -> dict:
    ids = sorted(ids)
    colunas_r = table_columns(cur, "results")
    colunas_v = table_columns(cur, "result_drivers")
    cur.execute(
        f'SELECT {", ".join(chr(34) + c + chr(34) for c in colunas_r)} '
        f'FROM results WHERE id = ANY(%s) ORDER BY id', (ids,))
    linhas_r = cur.fetchall()
    cur.execute(
        f'SELECT {", ".join(chr(34) + c + chr(34) for c in colunas_v)} '
        f'FROM result_drivers WHERE result_id = ANY(%s) ORDER BY id', (ids,))
    linhas_v = cur.fetchall()
    return {
        "contrato": "openwec-dedup-backup-v1",
        "results": {"colunas": colunas_r, "linhas": [list(l) for l in linhas_r]},
        "result_drivers": {"colunas": colunas_v,
                           "linhas": [list(l) for l in linhas_v]},
    }


def _fsync_dir(caminho: Path) -> None:
    """Windows has no directory handle to sync; the rename is durable there."""
    try:
        fd = os.open(caminho, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def write_backup(payload: dict, destino: Path, digest_do_plano: str) -> dict:
    """
    Write it, make it durable, read it back, and hash what came back.

    An existing file is never overwritten: the name carries the plan digest,
    so a file already there belongs to a run of this same plan, and replacing
    it would destroy the only copy of rows a previous attempt may have taken.

    The write goes to a sibling and is renamed over, with both the file and
    its directory synced, so the backup is on disk before the DELETE is
    issued rather than in a buffer that a crash would discard.
    """
    destino = Path(destino)
    if destino.exists():
        raise ExecutionRefused(
            [f"ja existe backup em {destino}; mova-o antes de repetir a "
             f"execucao — este executor nao sobrescreve backup"])
    destino.parent.mkdir(parents=True, exist_ok=True)

    payload = dict(payload, plan_digest=digest_do_plano)
    dados = json.dumps(payload, ensure_ascii=False, default=str,
                       indent=1).encode("utf-8")
    esperado = hashlib.sha256(dados).hexdigest()

    parcial = destino.with_name(destino.name + ".parcial")
    with parcial.open("wb") as arquivo:
        arquivo.write(dados)
        arquivo.flush()
        os.fsync(arquivo.fileno())
    os.replace(parcial, destino)
    _fsync_dir(destino.parent)

    relido = destino.read_bytes()
    obtido = hashlib.sha256(relido).hexdigest()
    if obtido != esperado:
        raise ExecutionRefused(
            [f"o backup gravado nao confere ao ser relido: {obtido} != "
             f"{esperado}"])
    return {
        "caminho": destino.as_posix(),
        "sha256": obtido,
        "bytes": len(relido),
        "plan_digest": digest_do_plano,
        "results": len(payload["results"]["linhas"]),
        "result_drivers": len(payload["result_drivers"]["linhas"]),
    }


def verify_backup(cur, payload: dict, ids: list[int]) -> list[str]:
    """
    Put the file back and see whether it is the same data.

    Temporary tables, so nothing is created in any schema and nothing has to
    be cleaned up. The live rows are still there — the delete has not run —
    so the comparison is against the rows the backup claims to hold.
    """
    motivos = []
    for tabela, chave in (("results", "id"), ("result_drivers", "result_id")):
        dados = payload[tabela]
        temp = f"backup_{tabela}"
        cur.execute(f'CREATE TEMP TABLE "{temp}" '
                    f'(LIKE "{tabela}" INCLUDING DEFAULTS) ON COMMIT DROP')
        if dados["linhas"]:
            from psycopg2.extras import execute_values
            colunas = ", ".join(f'"{c}"' for c in dados["colunas"])
            execute_values(
                cur, f'INSERT INTO "{temp}" ({colunas}) VALUES %s',
                [tuple(l) for l in dados["linhas"]], page_size=1000)

        vivo = row_digest(cur, tabela, ids, chave)
        restaurado = row_digest(cur, temp, ids, chave)
        if vivo != restaurado:
            motivos.append(
                f"backup de {tabela} nao reproduz as linhas vivas "
                f"({restaurado} != {vivo})")
    return motivos


# ── Snapshot and invariants ──────────────────────────────────

def snapshot(cur, sessoes: list[int], preservados: list[int],
             removiveis: list[int]) -> dict:
    cur.execute("SELECT count(*) FROM results")
    total = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM results WHERE id = ANY(%s)", (removiveis,))
    presentes_removiveis = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM results WHERE id = ANY(%s)", (preservados,))
    presentes_preservados = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM result_drivers")
    vinculos_total = cur.fetchone()[0]
    cur.execute(
        "SELECT coalesce(md5(string_agg(x, chr(29) ORDER BY x)), '') FROM ("
        "  SELECT result_id || ':' || slot || ':' || driver_id AS x"
        "  FROM result_drivers WHERE result_id = ANY(%s)) t", (preservados,))
    vinculos_preservados = cur.fetchone()[0]
    cur.execute(
        "SELECT count(*), coalesce(md5(string_agg(id::text, ',' ORDER BY id)), '')"
        " FROM laps WHERE session_id = ANY(%s)", (sessoes,))
    laps_n, laps_md5 = cur.fetchone()
    return {
        "results_total": total,
        "results_removiveis_presentes": presentes_removiveis,
        "results_preservados_presentes": presentes_preservados,
        "results_preservados_digest": row_digest(cur, "results", preservados),
        "result_drivers_total": vinculos_total,
        "vinculos_preservados_digest": vinculos_preservados,
        "laps": laps_n,
        "laps_digest": laps_md5,
        "sessions_digest": row_digest(cur, "sessions", sessoes),
    }


def check_invariants(antes: dict, depois: dict, removiveis: list[int],
                     bloqueados_presentes: int, bloqueados: list[int],
                     por_sessao: dict[int, int], esperado: dict[int, int]) -> list[str]:
    motivos = []
    if depois["results_removiveis_presentes"]:
        motivos.append(f"{depois['results_removiveis_presentes']} ids do plano "
                       f"continuam em results")
    esperado_total = antes["results_total"] - len(removiveis)
    if depois["results_total"] != esperado_total:
        motivos.append(f"results tem {depois['results_total']}; esperava "
                       f"{esperado_total} — algo fora do plano foi removido")
    if depois["results_preservados_presentes"] != antes["results_preservados_presentes"]:
        motivos.append("um id preservado desapareceu")
    if depois["results_preservados_digest"] != antes["results_preservados_digest"]:
        motivos.append("as linhas preservadas mudaram de conteudo")
    if depois["vinculos_preservados_digest"] != antes["vinculos_preservados_digest"]:
        motivos.append("os vinculos das linhas preservadas mudaram")
    if (depois["laps"], depois["laps_digest"]) != (antes["laps"], antes["laps_digest"]):
        motivos.append("laps foi alterada")
    if depois["sessions_digest"] != antes["sessions_digest"]:
        motivos.append("sessions foi alterada")
    if bloqueados_presentes != len(bloqueados):
        motivos.append(f"grupos bloqueados: {len(bloqueados)} ids esperados, "
                       f"{bloqueados_presentes} presentes")
    for sid, quantos in sorted(por_sessao.items()):
        if quantos != esperado[sid]:
            motivos.append(f"sessao {sid} ficou com {quantos} results; "
                           f"o plano previa {esperado[sid]}")
    return motivos


# ── The run ──────────────────────────────────────────────────

def _counts_by_session(cur, sessoes: list[int]) -> dict[int, int]:
    cur.execute("SELECT session_id, count(*) FROM results "
                "WHERE session_id = ANY(%s) GROUP BY 1", (sessoes,))
    contagem = {sid: n for sid, n in cur.fetchall()}
    return {sid: contagem.get(sid, 0) for sid in sessoes}


def preimage_check(cur, dump: Path, max_age_hours: int) -> dict:
    """
    The cluster dump, as evidence rather than as a filename.

    `backup_attestation` already answers which cluster it came from, when,
    and whether it restored somewhere disposable — this adds the one question
    it cannot answer on its own: whether it is still a pre-image of the tables
    about to be changed.
    """
    try:
        prova = validate_dump(Path(dump), max_age_hours=max_age_hours)
    except AttestationInvalid as exc:
        raise ExecutionRefused([f"dump sem evidencia utilizavel: {exc}"]) from exc

    agora = counts_of(cur)
    divergentes = [
        f"{t}: dump {prova['contagens'].get(t)} != banco {agora.get(t)}"
        for t in PREIMAGE_TABLES
        if prova["contagens"].get(t) != agora.get(t)]
    if divergentes:
        raise ExecutionRefused(
            ["o dump nao e mais uma pre-imagem deste banco: "
             + "; ".join(divergentes)])
    return {
        "caminho": Path(dump).as_posix(),
        "sha256": prova["sha256"],
        "cluster_de_origem": prova["cluster_de_origem"],
        "capturado_em": prova["capturado_em"],
        "idade_horas": prova["idade_horas"],
        "restauracao_verificada": bool(prova.get("restauracao")),
        "contagens": prova["contagens"],
    }


def run_plan(conn, plano: dict, backup_dir: Path | None,
             aplicar: bool = False, dump: Path | None = None,
             max_age_hours: int = DEFAULT_MAX_AGE_HOURS) -> dict:
    """
    Dry-run unless `aplicar`. Returns the report; raises ExecutionRefused.

    The caller owns the connection and the commit never happens here without
    every check above having passed.
    """
    autorizado = validate_plan(plano)
    sessoes = sorted(autorizado["sessoes"])
    removiveis = sorted(autorizado["removiveis"])
    preservados = sorted(autorizado["preservados"])
    bloqueados = sorted(autorizado["bloqueados"])
    esperado = {s["session_id"]: s["total_esperado"] for s in plano["sessions"]}

    relatorio = {
        "plan_digest": plano["plan_digest"],
        "series": plano["series"],
        "modo": "apply" if aplicar else "dry-run",
        "results_removiveis": len(removiveis),
        "results_preservados": len(preservados),
        "ids_bloqueados": len(bloqueados),
        "grupos_bloqueados": plano["resumo"].get("grupos_bloqueados"),
        "sessoes": len(sessoes),
        "sessoes_bloqueadas_inteiras": plano["resumo"].get(
            "sessoes_bloqueadas_inteiras"),
        "backup": None,
        "dump": None,
        "escritores_concorrentes": [],
        "divergencias": [],
        "aplicado": False,
    }

    with conn.cursor() as cur:
        cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        if aplicar:
            # First, and before anything takes a snapshot: from here on no
            # other transaction can write to either table until this one ends.
            lock_out_writers(conn, cur)

        cur.execute("SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"{LOCK_NAME}:{plano['series']}",))
        if not cur.fetchone()[0]:
            raise ExecutionRefused(
                ["outra execucao ja segura esta populacao; nada foi feito"])

        # In dry-run nothing is held, so this says who would have to stop.
        relatorio["escritores_concorrentes"] = writer_sessions(cur)

        atual = build_plan(cur, plano["series"])
        divergencias = (compare_plans(plano, atual)
                        + compare_dependencies(plano, atual))
        relatorio["divergencias"] = divergencias
        if divergencias:
            raise ExecutionRefused(divergencias)

        cur.execute("SELECT count(*) FROM results WHERE id = ANY(%s)",
                    (bloqueados,))
        bloqueados_antes = cur.fetchone()[0]
        antes = snapshot(cur, sessoes, preservados, removiveis)
        if antes["results_removiveis_presentes"] != len(removiveis):
            raise ExecutionRefused(
                [f"{len(removiveis)} ids no plano, "
                 f"{antes['results_removiveis_presentes']} em results"])

        if not aplicar:
            relatorio["backup"] = {"estado": "nao exigido em dry-run"}
            return relatorio

        if backup_dir is None:
            raise ExecutionRefused(["--apply exige --backup-dir"])
        if dump is None:
            raise ExecutionRefused(
                ["--apply exige --pg-dump: um pg_dump -Fc do cluster, com a "
                 "atestacao de backup_attestation e restauracao verificada"])

        relatorio["dump"] = preimage_check(cur, dump, max_age_hours)

        payload = backup_payload(cur, removiveis)
        destino = Path(backup_dir) / f"dedup-{plano['plan_digest'][:16]}.json"
        gravado = write_backup(payload, destino, plano["plan_digest"])

        # Parsed from the bytes that are on disk, and only after they hash to
        # what was written: the restore test has to be of the file, not of the
        # payload still in memory.
        bruto = destino.read_bytes()
        if hashlib.sha256(bruto).hexdigest() != gravado["sha256"]:
            raise ExecutionRefused(
                [f"o backup em {destino} mudou entre a gravacao e a leitura"])
        relido = json.loads(bruto.decode("utf-8"))
        if relido.get("plan_digest") != plano["plan_digest"]:
            raise ExecutionRefused(
                ["o backup relido nao declara este plano"])
        falhas = verify_backup(cur, relido, removiveis)
        gravado["estado"] = "restauracao conferida" if not falhas else "FALHOU"
        relatorio["backup"] = gravado
        if falhas:
            raise ExecutionRefused(falhas)

        cur.execute("DELETE FROM results WHERE id = ANY(%s)", (removiveis,))
        relatorio["results_removidos"] = cur.rowcount

        depois = snapshot(cur, sessoes, preservados, removiveis)
        cur.execute("SELECT count(*) FROM results WHERE id = ANY(%s)",
                    (bloqueados,))
        bloqueados_depois = cur.fetchone()[0]
        relatorio["vinculos_removidos"] = (
            antes["result_drivers_total"] - depois["result_drivers_total"])

        motivos = check_invariants(
            antes, depois, removiveis, bloqueados_depois, bloqueados,
            _counts_by_session(cur, sessoes), esperado)
        if bloqueados_depois != bloqueados_antes:
            motivos.append("um id de grupo bloqueado desapareceu")
        if relatorio["results_removidos"] != len(removiveis):
            motivos.append(
                f"o DELETE alcancou {relatorio['results_removidos']} linhas; "
                f"o plano tem {len(removiveis)}")
        if motivos:
            raise ExecutionRefused(motivos)

    conn.commit()
    relatorio["aplicado"] = True
    return relatorio


# ── CLI ──────────────────────────────────────────────────────

def connection(cfg: dict, somente_leitura: bool):
    import psycopg2
    opcoes = "-c default_transaction_read_only=on" if somente_leitura else None
    return psycopg2.connect(connect_timeout=5, options=opcoes, **cfg)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--plan", type=Path, required=True)
    ap.add_argument("--apply", action="store_true",
                    help="sem isto o modo e dry-run e nada e gravado")
    ap.add_argument("--confirm", help="o plan_digest do plano autorizado")
    ap.add_argument("--backup-dir", type=Path,
                    help="onde gravar o backup das linhas; nunca sobrescreve")
    ap.add_argument("--pg-dump", type=Path,
                    help="pg_dump -Fc do cluster, com a atestacao de "
                         "backup_attestation (capture + verify-restore)")
    ap.add_argument("--max-age-hours", type=int, default=DEFAULT_MAX_AGE_HOURS,
                    help=f"idade maxima da atestacao (padrao: "
                         f"{DEFAULT_MAX_AGE_HOURS})")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5433)
    ap.add_argument("--dbname", default="openwec")
    ap.add_argument("--user", default="openwec")
    ap.add_argument("--password", default="")
    args = ap.parse_args()

    try:
        plano = load_plan(args.plan)
    except ExecutionRefused as exc:
        print(f"RECUSADO: {exc}")
        return 2

    if args.apply:
        if args.confirm != plano.get("plan_digest"):
            print("RECUSADO: --apply exige --confirm com o plan_digest do "
                  f"plano:\n  plano:   {plano.get('plan_digest')}\n"
                  f"  confirm: {args.confirm}")
            return 2
        if not args.backup_dir:
            print("RECUSADO: --apply exige --backup-dir")
            return 2
        if not args.pg_dump:
            print("RECUSADO: --apply exige --pg-dump — um pg_dump -Fc do "
                  "cluster com atestacao e restauracao verificada")
            return 2

    cfg = {"host": args.host, "port": args.port, "dbname": args.dbname,
           "user": args.user, "password": args.password}
    # Dry-run opens the connection read-only, so the mode is enforced by the
    # server and not only by the branch below.
    conn = connection(cfg, somente_leitura=not args.apply)
    try:
        relatorio = run_plan(conn, plano, args.backup_dir, args.apply,
                             args.pg_dump, args.max_age_hours)
    except ExecutionRefused as exc:
        conn.rollback()
        conn.close()
        print("RECUSADO:")
        for motivo in exc.motivos:
            print(f"  - {motivo}")
        return 3
    except Exception:
        conn.rollback()
        conn.close()
        raise
    finally:
        if not conn.closed:
            conn.rollback()
            conn.close()

    print(json.dumps(relatorio, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
