#!/usr/bin/env python3
"""
Reconciliation report for the competition population.

Read-only. Compares what the discovery found in the raw exports against what
was actually persisted, and prints every residue rather than summarising it
away. Run it after populate_competitions.py against the same database.

Usage:
    python scripts/reconcile_population.py --dsn "..." --raw raw
"""
from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "database"))
from competition_discovery import analyse, iter_events  # noqa: E402


def q1(cur, sql, *a):
    # None rather than an empty tuple: with any parameter sequence psycopg2 runs
    # %-interpolation over the statement, and a LIKE 'zz!_%' would be read as a
    # placeholder.
    cur.execute(sql, a or None)
    row = cur.fetchone()
    return row[0] if row else 0


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--raw", default="raw", type=Path)
    args = ap.parse_args(argv)

    conn = psycopg2.connect(args.dsn)
    cur = conn.cursor()

    section("1. EXAMINADO")
    print(f"  eventos                          : {q1(cur, 'SELECT count(*) FROM events')}")
    print(f"  sessoes                          : {q1(cur, 'SELECT count(*) FROM sessions')}")
    print(f"  equipes distintas                : {q1(cur, 'SELECT count(*) FROM teams')}")

    section("2. COMPETICOES")
    cur.execute("SELECT resolution, count(*) FROM competitions GROUP BY 1 ORDER BY 1")
    for r, n in cur.fetchall():
        print(f"  {r:12s}                     : {n}")
    print(f"  total                            : {q1(cur, 'SELECT count(*) FROM competitions')}")
    print(f"  com identidade persistente       : "
          f"{q1(cur, 'SELECT count(DISTINCT identity_uid) FROM competitions')}")

    section("3. INSCRICOES")
    det = q1(cur, "SELECT count(*) FROM entries WHERE competition_id IS NOT NULL")
    und = q1(cur, "SELECT count(*) FROM entries WHERE competition_id IS NULL")
    print(f"  determinadas                     : {det}")
    print(f"  indeterminadas (competicao NULL) : {und}")
    print(f"  total                            : {det + und}")
    cur.execute("SELECT identity_status, count(*) FROM entries GROUP BY 1 ORDER BY 1")
    for s, n in cur.fetchall():
        print(f"    {s:12s}                   : {n}")

    section("4. ASSOCIACOES DE SESSAO")
    cur.execute("SELECT status, count(*) FROM session_competitions GROUP BY 1 ORDER BY 1")
    for s, n in cur.fetchall():
        print(f"  {s:12s}                     : {n}")
    print(f"  sessoes com MAIS DE UMA competicao: "
          f"{q1(cur, 'SELECT count(*) FROM (SELECT session_id FROM session_competitions WHERE competition_id IS NOT NULL GROUP BY session_id HAVING count(*) > 1) t')}")
    print(f"  sessoes sem nenhuma associacao   : "
          f"{q1(cur, 'SELECT count(*) FROM sessions s WHERE NOT EXISTS (SELECT 1 FROM session_competitions sc WHERE sc.session_id = s.id AND sc.competition_id IS NOT NULL)')}")

    section("5. CANDIDATOS")
    cur.execute("SELECT evaluation, count(*) FROM session_competition_candidates GROUP BY 1 ORDER BY 1")
    for e, n in cur.fetchall():
        print(f"  {e:14s}                   : {n}")
    print(f"  associacoes inferidas fundamentadas: "
          f"{q1(cur, 'SELECT count(*) FROM session_competitions WHERE justified_by IS NOT NULL')}")

    section("6. ANCORAS")
    cur.execute("SELECT exclusivity, state, count(*) FROM competition_anchors GROUP BY 1,2 ORDER BY 1,2")
    for ex, st, n in cur.fetchall():
        print(f"  {ex:10s} {st:11s}           : {n}")

    section("7. PENDENCIAS DE REVISAO")
    cur.execute("SELECT kind, count(*) FILTER (WHERE resolved_at IS NULL), "
                "count(*) FILTER (WHERE resolved_at IS NOT NULL) "
                "FROM competition_match_reviews GROUP BY 1 ORDER BY 1")
    for k, aberto, fechado in cur.fetchall():
        print(f"  {k:18s} abertas: {aberto:4d}  decididas: {fechado}")
    print(f"  hipoteses aplicadas automaticamente: 0 (por construcao)")

    section("8. DESCOBERTA versus PERSISTIDO")
    disc = collections.Counter()
    for key, ev in iter_events(args.raw):
        a = analyse(ev)
        if not a:
            continue
        disc["eventos"] += 1
        disc["sessoes"] += len(a["sess"])
        for c in a["comps"]:
            disc[c["kind"]] += 1
        disc["hipoteses"] += len(a["hyp"])
        disc["sem_associacao"] += len(a["unresolved"])
        disc["insc_determinadas"] += a["placed"]
        disc["insc_indeterminadas"] += len(a["undetermined"])

    # Scratch events written by the test suites are excluded: they have no
    # counterpart in raw/, so counting them would show a false divergence.
    REAL = "e.raw_id NOT LIKE 'zz!_%' ESCAPE '!'"
    pairs = [
        ("eventos", disc["eventos"],
         q1(cur, f"SELECT count(*) FROM events e WHERE {REAL}")),
        ("sessoes", disc["sessoes"], q1(cur, "SELECT count(*) FROM sessions")),
        ("competicoes resolved", disc["resolved"],
         q1(cur, "SELECT count(*) FROM competitions c JOIN events e ON e.id=c.event_id "
                 f"WHERE c.resolution='resolved' AND {REAL}")),
        ("competicoes provisional", disc["provisional"],
         q1(cur, "SELECT count(*) FROM competitions c JOIN events e ON e.id=c.event_id "
                 f"WHERE c.resolution='provisional' AND {REAL}")),
        ("hipoteses", disc["hipoteses"],
         q1(cur, "SELECT count(*) FROM competition_match_reviews r "
                 f"JOIN events e ON e.id=r.event_id WHERE r.kind='merge_hypothesis' AND {REAL}")),
        ("sessoes sem associacao", disc["sem_associacao"],
         q1(cur, "SELECT count(*) FROM session_competitions WHERE status='unresolved'")),
        ("inscricoes determinadas", disc["insc_determinadas"],
         q1(cur, "SELECT count(*) FROM entries WHERE competition_id IS NOT NULL")),
        ("inscricoes indeterminadas", disc["insc_indeterminadas"],
         q1(cur, "SELECT count(*) FROM entries WHERE competition_id IS NULL")),
    ]
    worst = 0
    for name, d, p in pairs:
        flag = "" if d == p else f"   <-- DIVERGE ({p - d:+d})"
        worst = max(worst, abs(p - d))
        print(f"  {name:28s} descoberta={d:6d}  persistido={p:6d}{flag}")

    section("9. REGISTROS DA FONTE SEM CORRESPONDENCIA")
    print(f"  inscricoes sem competicao        : {und}")
    cur.execute("""
        SELECT sr.key || '/' || se.raw_id || '/' || e.raw_id, count(*)
        FROM entries en JOIN events e ON e.id = en.event_id
        JOIN seasons se ON se.id = e.season_id JOIN series sr ON sr.id = se.series_id
        WHERE en.competition_id IS NULL GROUP BY 1 ORDER BY 2 DESC, 1""")
    for raw_id, n in cur.fetchall():
        print(f"      {n:4d}  {raw_id}")
    print(f"  sessoes sem associacao           : "
          f"{q1(cur, chr(83) + 'ELECT count(*) FROM session_competitions WHERE status=' + chr(39) + 'unresolved' + chr(39))}")
    cur.execute("""
        SELECT sr.key || '/' || se.raw_id || '/' || e.raw_id, count(*)
        FROM session_competitions sc
        JOIN sessions s ON s.id = sc.session_id JOIN events e ON e.id = s.event_id
        JOIN seasons se ON se.id = e.season_id JOIN series sr ON sr.id = se.series_id
        WHERE sc.status = 'unresolved' GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 12""")
    for raw_id, n in cur.fetchall():
        print(f"      {n:4d}  {raw_id}")

    section("10. INTEGRIDADE HISTORICA")
    for t in ("results", "laps", "result_drivers", "cars"):
        print(f"  {t:16s} intacto           : {q1(cur, f'SELECT count(*) FROM {t}')} linhas")

    print(f"\n{'=' * 62}")
    print("DIVERGENCIA MAXIMA descoberta/persistido: "
          f"{worst}" + ("  (nenhuma)" if worst == 0 else "  <-- INVESTIGAR"))
    return 0 if worst == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
