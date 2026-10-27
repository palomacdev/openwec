"""
OpenWEC — which IMSA results are copies of each other, and which may go.

Read-only. Every statement is a SELECT, the connection is opened with
`default_transaction_read_only=on`, and the plan is written after the
connection is closed. **Nothing is deleted, moved or merged.**

## What licenses a removal

A session's results are read as full content signatures — the car as the
loader wrote it (number, team, vehicle, class, group, tyres, the two IMSA
ids) and the result as the loader parsed it (position, status, laps, the raw
times and their parsed seconds). `results.id` and `car_id` are left out:
each re-ingestion minted a new `cars` row, so the copies differ there by
construction and nowhere else.

A session qualifies when **every** signature in it repeats the same number
of times. 27 signatures seven times each is not a classification — it is one
classification loaded seven times. `UNIQUE (session_id, car_id)` means those
seven rows are seven distinct `cars` rows identical in all eight columns the
loader writes, which is the duplication and not a race.

A session whose multiplicities disagree is blocked whole. The one in the
corpus is 2721, where 39 signatures repeat between 38 and 394 times: that is
a lap log in the shape of a classification, not a multiplied one.

## What blocks a group

`result_drivers` has `ON DELETE CASCADE` on `results`. Removing a copy takes
its driver links with it, so a copy may only go when its links are already
on the row that stays. Measured: 1.238 groups fail that, and the reason is
always the same — the copies point at driver rows that differ only in case,
`Tom Kimber-smith` and `Tom Kimber-Smith`. Deciding those are one person is
not this module's to make, so the group stays.

## The file is corroboration, not the licence

With `--raw`, each session's distinct content is matched against the
published files, reconstructed with the parsing of 6e7f610 — the revision
that loaded them. Where it matches, the plan carries the path and its
SHA-256. Where it does not, the field is null and the session is planned
anyway: the evidence that licenses a removal is the repetition inside the
session, which needs no file. Coverage is reported rather than assumed.

Team names are compared case-folded, and only for this match: the corpus
holds 1.111 team rows under 1.031 distinct case-folded names, and
`upsert_team` conflicts on the exact string, so the spelling stored is the
one that got there first rather than the one in this file.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from database.recovery.session_association import championship_of  # noqa: E402

# The revision whose parsing produced the rows now in the table. Pinned, not
# tracked: reconstructing with today's loader would not reproduce them — it
# reads POS where this one read only POSITION, so every IMSA position here is
# NULL and a current reconstruction would disagree on every row.
HISTORICAL_REVISION = "6e7f610"

SEPARATOR = "\x1e"

# Why a session or a group is not planned for removal.
NO_DUPLICATION = "SEM_DUPLICACAO"
FACTOR_NOT_UNIFORM = "FATOR_NAO_UNIFORME"
DIVERGENT_LINKS = "VINCULOS_DIVERGENTES"

BLOCK_MEANING = {
    FACTOR_NOT_UNIFORM: ("as assinaturas da sessao nao repetem todas o mesmo "
                         "numero de vezes; nao e uma carga multiplicada"),
    DIVERGENT_LINKS: ("uma copia removivel carrega vinculo de piloto que a "
                      "copia preservada nao tem; remove-la apagaria o vinculo "
                      "por ON DELETE CASCADE"),
}

# The columns of one result row, in the order `result_signature` reads them.
SIGNATURE_FIELDS = (
    "number", "team", "vehicle", "car_class", "car_group", "tires",
    "imsa_car_id", "imsa_team_id",
    "position", "status", "laps_completed",
    "total_time_raw", "total_time_s",
    "gap_to_first_raw", "gap_to_first_s",
    "gap_to_prev_raw", "gap_to_prev_s",
    "fl_lap_number", "fl_time_raw", "fl_time_s", "fl_kph",
)


# ── Signatures ───────────────────────────────────────────────

def _text(valor) -> str:
    return "" if valor is None else str(valor)


def _fixed(valor, casas: int) -> str:
    """
    A numeric column as its scale writes it.

    The database holds DECIMAL and the reconstruction produces float; without
    a common scale the two never compare equal. A value that rounds
    differently on the two sides produces a mismatch, which blocks the file
    match and never a removal.
    """
    return "" if valor is None else f"{float(valor):.{casas}f}"


def result_signature(campos) -> str:
    """One result row as a single string. `id` and `car_id` are not in it."""
    c = list(campos)
    if len(c) != len(SIGNATURE_FIELDS):
        raise ValueError(f"esperava {len(SIGNATURE_FIELDS)} campos, recebi {len(c)}")
    return SEPARATOR.join([
        _text(c[0]), _text(c[1]), _text(c[2]), _text(c[3]), _text(c[4]),
        _text(c[5]), _text(c[6]), _text(c[7]),
        _text(c[8]), _text(c[9]), _text(c[10]),
        _text(c[11]), _fixed(c[12], 3),
        _text(c[13]), _fixed(c[14], 3),
        _text(c[15]), _fixed(c[16], 3),
        _text(c[17]), _text(c[18]), _fixed(c[19], 3), _fixed(c[20], 2),
    ])


def fold_team(assinatura: str) -> str:
    """The same signature with the team name case-folded. File match only."""
    partes = assinatura.split(SEPARATOR)
    partes[1] = partes[1].casefold()
    return SEPARATOR.join(partes)


def digest(assinaturas) -> str:
    h = hashlib.sha256()
    for a in sorted(assinaturas):
        h.update(a.encode("utf-8"))
        h.update(b"\x1d")
    return h.hexdigest()


def group_results(linhas) -> dict[str, list[int]]:
    """
    {signature: [result_id, ...]} with the ids ascending.

    `linhas` is (result_id, *SIGNATURE_FIELDS) per row, in any order.
    """
    grupos: dict[str, list[int]] = {}
    for linha in linhas:
        grupos.setdefault(result_signature(linha[1:]), []).append(linha[0])
    for ids in grupos.values():
        ids.sort()
    return grupos


def uniform_factor(grupos: dict[str, list[int]]) -> int | None:
    """How many times each signature repeats, or None when they disagree."""
    if not grupos:
        return None
    multiplicidades = {len(ids) for ids in grupos.values()}
    if len(multiplicidades) != 1:
        return None
    return multiplicidades.pop()


# ── The plan for one session ─────────────────────────────────

def plan_session(meta: dict, grupos: dict[str, list[int]],
                 vinculos: dict[int, set], corroboracao: dict | None = None) -> dict:
    """
    What may be removed from one session, and what may not.

    `vinculos` maps a result id to its set of (slot, driver_id). A result
    with no links maps to the empty set, which is a subset of anything.
    """
    total_atual = sum(len(ids) for ids in grupos.values())
    entrada = {
        "session_id": meta["session_id"],
        "session_name": meta.get("session_name"),
        "series": meta.get("series"),
        "season": meta.get("season"),
        "event": meta.get("event"),
        "championship": meta.get("championship"),
        "arquivo_candidato": None,
        "sha256": None,
        "arquivos_candidatos": [],
        "fator": None,
        "total_atual": total_atual,
        "total_esperado": total_atual,
        "ids_preservados": [],
        "ids_removiveis": [],
        "result_drivers_preservados": 0,
        "result_drivers_removidos": 0,
        "bloqueios": [],
    }
    if corroboracao:
        entrada["arquivos_candidatos"] = corroboracao["arquivos"]
        if len(corroboracao["arquivos"]) == 1:
            entrada["arquivo_candidato"] = corroboracao["arquivos"][0]["caminho"]
            entrada["sha256"] = corroboracao["arquivos"][0]["sha256"]

    fator = uniform_factor(grupos)
    entrada["fator"] = fator

    if fator is None:
        entrada["bloqueios"].append({
            "motivo": FACTOR_NOT_UNIFORM,
            "detalhe": BLOCK_MEANING[FACTOR_NOT_UNIFORM],
            "multiplicidades": sorted({len(i) for i in grupos.values()}),
            "assinaturas": len(grupos),
        })
        # Blocked whole, so every row stays. Listing them as preserved keeps
        # `atuais == preservados + removiveis` true of the plan as a whole.
        entrada["ids_preservados"] = sorted(i for ids in grupos.values() for i in ids)
        entrada["result_drivers_preservados"] = sum(
            len(vinculos.get(i, ())) for i in entrada["ids_preservados"])
        return entrada

    if fator == 1:
        # Requisito 7: nada a remover, e isso nao e um bloqueio.
        entrada["bloqueios"].append({
            "motivo": NO_DUPLICATION,
            "detalhe": "cada assinatura aparece uma vez; a sessao fica como esta",
            "assinaturas": len(grupos),
        })
        entrada["ids_preservados"] = sorted(i for ids in grupos.values() for i in ids)
        entrada["result_drivers_preservados"] = sum(
            len(vinculos.get(i, ())) for i in entrada["ids_preservados"])
        return entrada

    preservados, removiveis = [], []
    for assinatura in sorted(grupos):
        ids = grupos[assinatura]
        mantido, resto = ids[0], ids[1:]
        do_mantido = vinculos.get(mantido, frozenset())
        divergentes = [i for i in resto
                       if not vinculos.get(i, frozenset()) <= do_mantido]
        if divergentes:
            entrada["bloqueios"].append({
                "motivo": DIVERGENT_LINKS,
                "detalhe": BLOCK_MEANING[DIVERGENT_LINKS],
                "assinatura_sha256": digest([assinatura]),
                "ids": ids,
                "id_preservado": mantido,
                "ids_divergentes": divergentes,
                "vinculos_preservados": sorted(do_mantido),
                "vinculos_ausentes": sorted(
                    {v for i in divergentes for v in vinculos.get(i, ())}
                    - set(do_mantido)),
            })
            preservados.extend(ids)
            continue
        preservados.append(mantido)
        removiveis.extend(resto)

    entrada["ids_preservados"] = sorted(preservados)
    entrada["ids_removiveis"] = sorted(removiveis)
    entrada["total_esperado"] = len(preservados)
    entrada["result_drivers_preservados"] = sum(
        len(vinculos.get(i, ())) for i in preservados)
    entrada["result_drivers_removidos"] = sum(
        len(vinculos.get(i, ())) for i in removiveis)
    return entrada


def summarise(sessoes: list[dict]) -> dict:
    """Counts, and a digest that changes when any of the plan changes."""
    com_remocao = [s for s in sessoes if s["ids_removiveis"]]
    bloqueadas = [s for s in sessoes
                  if any(b["motivo"] != NO_DUPLICATION for b in s["bloqueios"])]
    return {
        "sessoes": len(sessoes),
        "sessoes_com_remocao": len(com_remocao),
        "sessoes_sem_duplicacao": sum(
            1 for s in sessoes if s["fator"] == 1),
        "sessoes_com_bloqueio": len(bloqueadas),
        "sessoes_bloqueadas_inteiras": sum(
            1 for s in sessoes if s["fator"] is None),
        "sessoes_parcialmente_removiveis": sum(
            1 for s in sessoes if s["ids_removiveis"] and any(
                b["motivo"] == DIVERGENT_LINKS for b in s["bloqueios"])),
        "grupos_bloqueados": sum(
            1 for s in sessoes for b in s["bloqueios"]
            if b["motivo"] == DIVERGENT_LINKS),
        "results_atuais": sum(s["total_atual"] for s in sessoes),
        "results_preservados": sum(len(s["ids_preservados"]) for s in sessoes),
        "results_removiveis": sum(len(s["ids_removiveis"]) for s in sessoes),
        "result_drivers_removidos": sum(
            s["result_drivers_removidos"] for s in sessoes),
        "sessoes_corroboradas_por_arquivo": sum(
            1 for s in sessoes if s["arquivo_candidato"]),
        "sessoes_com_varios_arquivos": sum(
            1 for s in sessoes if len(s["arquivos_candidatos"]) > 1),
        "fatores": {str(k): v for k, v in sorted(
            {f: sum(1 for s in sessoes if s["fator"] == f)
             for f in {s["fator"] for s in sessoes}}.items(),
            key=lambda kv: (kv[0] is None, kv[0]))},
    }


def plan_digest(sessoes: list[dict]) -> str:
    """Over the ids only: the plan is the list of rows to keep and to remove."""
    h = hashlib.sha256()
    for s in sorted(sessoes, key=lambda x: x["session_id"]):
        h.update(f"{s['session_id']}|".encode())
        h.update(",".join(map(str, s["ids_preservados"])).encode())
        h.update(b"|")
        h.update(",".join(map(str, s["ids_removiveis"])).encode())
        h.update(b"\x1d")
    return h.hexdigest()


# ── The published files, as 6e7f610 read them ────────────────

VALID_STATUSES = {"Classified", "Not Classified", "DNF",
                  "DNS", "DSQ", "Retired", "Other"}


def normalize_status(s: str) -> str:
    if not s:
        return "Other"
    s = s.strip()
    if s in VALID_STATUSES:
        return s
    low = s.lower()
    if "classify" in low or "classified" in low:
        return "Classified"
    if "dnf" in low or "not finish" in low:
        return "DNF"
    if "dns" in low or "not start" in low:
        return "DNS"
    if "dsq" in low or "disq" in low:
        return "DSQ"
    if "retired" in low or "retir" in low:
        return "Retired"
    return "Other"


def parse_race_time(s: str) -> float | None:
    if not s or s.strip() in ("-", "", "0"):
        return None
    s = s.strip()
    m = re.match(r"(\d+):(\d+)'([\d.]+)", s)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    m = re.match(r"(\d+)'([\d.]+)", s)
    if m:
        return int(m.group(1)) * 60 + float(m.group(2))
    m = re.match(r"(\d+):([\d.]+)", s)
    if m:
        return int(m.group(1)) * 60 + float(m.group(2))
    try:
        return float(s)
    except ValueError:
        return None


def parse_lap_time(s: str) -> float | None:
    if not s or s.strip() in ("-", "", "0"):
        return None
    s = s.strip()
    m = re.match(r"(\d+)'([\d.]+)", s)
    if m:
        return int(m.group(1)) * 60 + float(m.group(2))
    m = re.match(r"(\d+):([\d.]+)", s)
    if m:
        return int(m.group(1)) * 60 + float(m.group(2))
    try:
        return float(s)
    except ValueError:
        return None


def parse_gap(s: str) -> float | None:
    if not s or s.strip() in ("-", "", "0"):
        return None
    s = s.strip()
    if "lap" in s.lower():
        return None
    return parse_lap_time(s) or parse_race_time(s)


def read_csv(path: Path) -> list[dict]:
    raw = path.read_bytes()
    content = None
    for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            content = raw.decode(enc)
            break
        except UnicodeDecodeError:
            pass
    if content is None:
        return []
    linhas = []
    for row in csv.DictReader(io.StringIO(content), delimiter=";"):
        limpa = {k.strip(): (v.strip() if v else "") for k, v in row.items() if k}
        if not any(limpa.values()):
            continue
        linhas.append(limpa)
    return linhas


def _inteiro(valor) -> int | None:
    try:
        return int(valor or 0) or None
    except (ValueError, TypeError):
        return None


def file_signature(row: dict) -> str:
    """One published row, as 6e7f610 would have stored it.

    `IMSA_CarId` is read with the underscore the historical code used. The
    files spell it `IMSACarId`, so both sides are None — reproducing the
    miss is the point, not correcting it.
    """
    total = row.get("TOTAL_TIME", "").strip()
    g1 = row.get("GAP_FIRST", "").strip()
    gp = row.get("GAP_PREVIOUS", "").strip()
    fl = row.get("FL_TIME", "").strip()
    try:
        kph = float(row.get("FL_KPH", "") or 0) or None
    except ValueError:
        kph = None
    return result_signature((
        row.get("NUMBER", "").strip(),
        row.get("TEAM", "").strip() or "Unknown",
        row.get("VEHICLE", "").strip() or None,
        row.get("CLASS", "").strip() or None,
        row.get("GROUP", "").strip() or None,
        (row.get("TYRES") or row.get("TIRES") or "").strip() or None,
        _inteiro(row.get("IMSA_CarId", "")),
        _inteiro(row.get("IMSA_TeamId", "")),
        _inteiro(row.get("POSITION", "")),
        normalize_status(row.get("STATUS", "")),
        _inteiro(row.get("LAPS", "")),
        total or None, parse_race_time(total),
        g1 or None, parse_gap(g1),
        gp or None, parse_gap(gp),
        _inteiro(row.get("FL_LAPNUM", "")),
        fl or None, parse_lap_time(fl), kph,
    ))


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_file_index(raiz: Path) -> dict[str, list[dict]]:
    """
    {digest of the file's distinct content: [{caminho, sha256}, ...]}

    A file with no readable row is left out rather than indexed as empty,
    which would make every empty file a candidate for the others.
    """
    indice: dict[str, list[dict]] = {}
    caminhos = sorted({p.as_posix() for p in raiz.rglob("*")
                       if p.is_file() and p.suffix.lower() == ".csv"})
    for caminho in caminhos:
        p = Path(caminho)
        try:
            linhas = read_csv(p)
        except OSError:
            continue
        assinaturas = {fold_team(file_signature(r)) for r in linhas
                       if r.get("NUMBER", "").strip()}
        if not assinaturas:
            continue
        indice.setdefault(digest(assinaturas), []).append(
            {"caminho": caminho, "sha256": sha256_of(p)})
    return indice


def corroboration(grupos: dict[str, list[int]],
                  indice: dict[str, list[dict]]) -> dict | None:
    achados = indice.get(digest(fold_team(a) for a in grupos))
    return {"arquivos": achados} if achados else None


# ── Reading, and nothing else ────────────────────────────────

RESULTS_SQL = """
SELECT s.id, r.id,
       c.number, t.name, c.vehicle, c.car_class, c.car_group, c.tires,
       c.imsa_car_id, c.imsa_team_id,
       r.position, r.status, r.laps_completed,
       r.total_time_raw, r.total_time_s,
       r.gap_to_first_raw, r.gap_to_first_s,
       r.gap_to_prev_raw,  r.gap_to_prev_s,
       r.fl_lap_number, r.fl_time_raw, r.fl_time_s, r.fl_kph
FROM results r
JOIN sessions s  ON s.id  = r.session_id
JOIN events   e  ON e.id  = s.event_id
JOIN seasons  se ON se.id = e.season_id
JOIN series   sr ON sr.id = se.series_id
JOIN cars     c  ON c.id  = r.car_id
LEFT JOIN teams t ON t.id = c.team_id
WHERE sr.key = %s
"""

LINKS_SQL = """
SELECT rd.result_id, rd.slot, rd.driver_id
FROM result_drivers rd
JOIN results  r  ON r.id  = rd.result_id
JOIN sessions s  ON s.id  = r.session_id
JOIN events   e  ON e.id  = s.event_id
JOIN seasons  se ON se.id = e.season_id
JOIN series   sr ON sr.id = se.series_id
WHERE sr.key = %s
"""

SESSIONS_SQL = """
SELECT s.id, s.name, sr.key, se.label, e.name, s.source_url
FROM sessions s
JOIN events   e  ON e.id  = s.event_id
JOIN seasons  se ON se.id = e.season_id
JOIN series   sr ON sr.id = se.series_id
WHERE sr.key = %s
"""


def read_corpus(cur, series: str = "IMSA") -> tuple[dict, dict, dict]:
    """The three reads, in full. Returns (grupos_por_sessao, vinculos, meta)."""
    cur.execute(RESULTS_SQL, (series,))
    linhas_por_sessao: dict[int, list] = {}
    for linha in cur.fetchall():
        linhas_por_sessao.setdefault(linha[0], []).append(linha[1:])
    grupos = {sid: group_results(linhas)
              for sid, linhas in linhas_por_sessao.items()}

    cur.execute(LINKS_SQL, (series,))
    vinculos: dict[int, set] = {}
    for result_id, slot, driver_id in cur.fetchall():
        vinculos.setdefault(result_id, set()).add((slot, driver_id))

    cur.execute(SESSIONS_SQL, (series,))
    meta = {}
    for sid, nome, serie, temporada, evento, source_url in cur.fetchall():
        meta[sid] = {"session_id": sid, "session_name": nome, "series": serie,
                     "season": temporada, "event": evento,
                     "championship": championship_of(source_url)}
    return grupos, vinculos, meta


def build_plan(cur, series: str = "IMSA", indice: dict | None = None) -> dict:
    grupos, vinculos, meta = read_corpus(cur, series)
    sessoes = [
        plan_session(meta.get(sid, {"session_id": sid}), grupos[sid], vinculos,
                     corroboration(grupos[sid], indice) if indice else None)
        for sid in sorted(grupos)
    ]
    return {
        "contrato": "openwec-dedup-plan-v1",
        "series": series,
        "revisao_historica": HISTORICAL_REVISION,
        "campos_da_assinatura": list(SIGNATURE_FIELDS),
        "motivos": BLOCK_MEANING,
        "corroboracao_por_arquivo": bool(indice),
        "resumo": summarise(sessoes),
        "plan_digest": plan_digest(sessoes),
        "sessions": sessoes,
    }


# ── CLI ──────────────────────────────────────────────────────

def read_only_connection(cfg: dict):
    import psycopg2
    return psycopg2.connect(connect_timeout=5,
                            options="-c default_transaction_read_only=on",
                            **cfg)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--series", default="IMSA")
    ap.add_argument("--raw", type=Path,
                    help="raiz dos arquivos publicados (ex.: raw/imsa); sem "
                         "ela o plano sai sem corroboracao por arquivo")
    ap.add_argument("--json", type=Path,
                    help="grava o plano aqui; um caminho fora do repositorio "
                         "e fora de raw/ e a recomendacao")
    ap.add_argument("--session-id", type=int, help="imprime uma sessao")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5433)
    ap.add_argument("--dbname", default="openwec")
    ap.add_argument("--user", default="openwec")
    ap.add_argument("--password", default="")
    args = ap.parse_args()

    indice = None
    if args.raw:
        if not args.raw.is_dir():
            print(f"RAW INEXISTENTE: {args.raw}")
            return 2
        indice = build_file_index(args.raw)
        print(f"arquivos indexados: {sum(len(v) for v in indice.values())} "
              f"em {len(indice)} conjuntos distintos")

    cfg = {"host": args.host, "port": args.port, "dbname": args.dbname,
           "user": args.user, "password": args.password}
    conn = read_only_connection(cfg)
    try:
        with conn.cursor() as cur:
            plano = build_plan(cur, args.series, indice)
    finally:
        if not conn.closed:
            conn.rollback()
            conn.close()
    # No database from here on.

    r = plano["resumo"]
    print(f"\n{args.series}: {r['sessoes']} sessoes com resultados")
    print(f"  fatores: {r['fatores']}")
    print(f"  results atuais:      {r['results_atuais']}")
    print(f"  results preservados: {r['results_preservados']}")
    print(f"  results removiveis:  {r['results_removiveis']}")
    print(f"  result_drivers que caem junto: {r['result_drivers_removidos']}")
    print(f"  sessoes sem duplicacao (fator 1): {r['sessoes_sem_duplicacao']}")
    print(f"  sessoes bloqueadas inteiras:     {r['sessoes_bloqueadas_inteiras']}")
    print(f"  grupos bloqueados:               {r['grupos_bloqueados']}")
    print(f"  sessoes parcialmente removiveis: {r['sessoes_parcialmente_removiveis']}")
    print(f"  corroboradas por arquivo:        {r['sessoes_corroboradas_por_arquivo']}")
    print(f"  plan_digest: {plano['plan_digest']}")

    if args.session_id is not None:
        for s in plano["sessions"]:
            if s["session_id"] == args.session_id:
                print("\n" + json.dumps(s, ensure_ascii=False, indent=2, default=str))

    if args.json:
        args.json.write_text(
            json.dumps(plano, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8")
        print(f"\nplano gravado em {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
