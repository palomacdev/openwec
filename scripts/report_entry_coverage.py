#!/usr/bin/env python3
"""
How many classification rows can be placed on a competitive entry, and why the
rest cannot.

Read-only in every sense: it opens the database read-only, reads raw/, and
writes nothing but its own output files. It applies none of the 77 pending
merge hypotheses and changes no data.

The output is deterministic. Rows are sorted by a declared key, the format is
fixed, and nothing dated or random appears in it, so two runs over the same
inputs produce byte-identical files and a diff means the inputs changed.

Usage:
    python scripts/report_entry_coverage.py --dsn "..." --raw raw \\
        --out coverage.txt --pendencies pendencies.csv
"""
from __future__ import annotations

import argparse
import collections
import csv
import sys
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "database"))
sys.path.insert(0, str(ROOT / "database" / "loader"))

from competition_discovery import read_csv, sess_name, src_id  # noqa: E402
from entry_resolution import ClassificationRow, Outcome  # noqa: E402
from entry_resolution_db import (  # noqa: E402
    load_event_sessions, load_events, load_session_view, resolve,
)

PENDENCY_FIELDS = [
    "event", "session", "number", "car_class", "team", "source_id",
    "outcome", "candidate_competitions", "evidence",
]

# Declared once so the report and the tests agree on the order.
OUTCOME_ORDER = [
    Outcome.RESOLVED_BY_SOURCE_ID,
    Outcome.RESOLVED_BY_SOLE_COMPETITION,
    Outcome.MULTIPLE_CANDIDATES,
    Outcome.CONTRADICTORY_EVIDENCE,
    Outcome.ENTRY_NOT_FOUND,
    Outcome.NO_ASSOCIATION,
]


SHARED_IDENTIFIERS = """
WITH duplicated AS (
    SELECT e.competition_id, si.source_entry_id
    FROM entry_source_ids si
    JOIN entries e ON e.id = si.entry_id
    WHERE e.competition_id IS NOT NULL
    GROUP BY 1, 2
    HAVING count(DISTINCT si.entry_id) > 1
)
SELECT sr.key::text || '/' || se.raw_id || '/' || ev.raw_id,
       d.source_entry_id,
       count(DISTINCT regexp_replace(en.number, '^0+', ''))   AS normalised_numbers,
       count(DISTINCT en.number)                              AS literal_numbers,
       count(DISTINCT coalesce(en.car_class, '-'))            AS classes,
       string_agg(DISTINCT '#' || en.number || '/' ||
                  coalesce(en.car_class, 'sem classe'), ', '
                  ORDER BY '#' || en.number || '/' ||
                           coalesce(en.car_class, 'sem classe'))
FROM duplicated d
JOIN entries en ON en.competition_id = d.competition_id
JOIN entry_source_ids si ON si.entry_id = en.id
                        AND si.source_entry_id = d.source_entry_id
JOIN events ev   ON ev.id = en.event_id
JOIN seasons se  ON se.id = ev.season_id
JOIN series sr   ON sr.id = se.series_id
GROUP BY 1, 2, d.competition_id
ORDER BY 1, 2
"""


def shared_identifiers(cur) -> list[dict]:
    """
    Entry-list identifiers naming more than one entry of a competition.

    Classified by what the entries actually differ in, because they do not all
    differ in the same way and treating them as one problem would be wrong.
    Nothing here is corrected: the rows are listed so a person can decide.
    """
    cur.execute(SHARED_IDENTIFIERS)
    out = []
    for event, identifier, normalised, literal, classes, entries in cur.fetchall():
        if normalised > 1:
            categoria = "numeros realmente diferentes"
        elif literal > 1:
            categoria = "mesmo numero, grafia com zero a esquerda"
        elif classes > 1:
            categoria = "mesmo numero, classes diferentes"
        else:
            categoria = "mesmo numero e mesma classe"
        out.append({"evento": event, "identificador": identifier,
                    "categoria": categoria, "inscricoes": entries})
    return out


def classification_rows(event_dir: Path) -> dict[str, list[ClassificationRow]]:
    """Rows per session name, each session's rows in a stable order."""
    per_session: dict[str, list[ClassificationRow]] = collections.defaultdict(list)
    paths = sorted(
        (p for p in event_dir.rglob("*")
         if p.suffix.upper() == ".CSV" and "classification" in str(p).lower()),
        key=lambda p: str(p).lower(),
    )
    for path in paths:
        name = sess_name(path)
        for raw_row in read_csv(path):
            number = raw_row.get("NUMBER", "").strip()
            if not number:
                continue
            per_session[name].append(ClassificationRow(
                number=number,
                car_class=raw_row.get("CLASS", "").strip().upper() or None,
                team=raw_row.get("TEAM", "").strip(),
                source_id=src_id(raw_row) or None,
            ))
    for name in per_session:
        per_session[name].sort(
            key=lambda r: (r.number, r.car_class or "", r.team, r.source_id or ""))
    return per_session


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--raw", default="raw", type=Path)
    ap.add_argument("--out", default="entry_coverage.txt", type=Path)
    ap.add_argument("--pendencies", default="entry_pendencies.csv", type=Path)
    args = ap.parse_args(argv)

    conn = psycopg2.connect(args.dsn)
    conn.set_session(readonly=True, autocommit=True)
    cur = conn.cursor()

    counts: collections.Counter[Outcome] = collections.Counter()
    by_event: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    pendencies: list[dict] = []
    sessions_seen = events_seen = 0
    missing_in_db: list[str] = []

    for event_id, event_key in load_events(cur):
        series, season, event = event_key.split("/", 2)
        event_dir = args.raw / series.lower() / season / event
        if not event_dir.is_dir():
            missing_in_db.append(event_key)
            continue
        events_seen += 1
        rows_by_session = classification_rows(event_dir)
        db_sessions = {name: sid for sid, name in load_event_sessions(cur, event_id)}

        for session_name in sorted(rows_by_session):
            session_id = db_sessions.get(session_name)
            if session_id is None:
                missing_in_db.append(f"{event_key} | {session_name}")
                continue
            sessions_seen += 1
            view = load_session_view(cur, session_id)
            for row in rows_by_session[session_name]:
                decision = resolve(view, row)
                counts[decision.outcome] += 1
                by_event[event_key][decision.outcome] += 1
                if not decision.resolved:
                    pendencies.append({
                        "event": event_key,
                        "session": session_name,
                        "number": row.number,
                        "car_class": row.car_class or "",
                        "team": row.team,
                        "source_id": row.source_id or "",
                        "outcome": decision.outcome.value,
                        "candidate_competitions": " | ".join(decision.candidates),
                        "evidence": decision.evidence,
                    })

    total = sum(counts.values())
    lines = [
        "COBERTURA DE RESOLUCAO DE INSCRICAO",
        "===================================",
        "",
        f"eventos analisados                 : {events_seen}",
        f"sessoes analisadas                 : {sessions_seen}",
        f"linhas de classificacao            : {total}",
        "",
        "por desfecho",
        "------------",
    ]
    for outcome in OUTCOME_ORDER:
        n = counts[outcome]
        share = (100.0 * n / total) if total else 0.0
        lines.append(f"  {outcome.value:32s} {n:7d}  {share:5.1f}%")

    resolved = sum(counts[o] for o in OUTCOME_ORDER if o.resolved)
    lines += [
        "",
        f"  {'RESOLVIDAS':32s} {resolved:7d}  "
        f"{(100.0 * resolved / total) if total else 0.0:5.1f}%",
        f"  {'PENDENTES':32s} {total - resolved:7d}  "
        f"{(100.0 * (total - resolved) / total) if total else 0.0:5.1f}%",
        "",
        "eventos com pendencias",
        "----------------------",
    ]
    pending_events = sorted(
        ((key, sum(c[o] for o in OUTCOME_ORDER if not o.resolved))
         for key, c in by_event.items()),
        key=lambda kv: (-kv[1], kv[0]),
    )
    for key, n in pending_events:
        if n:
            lines.append(f"  {n:6d}  {key}")

    shared = shared_identifiers(cur)
    if shared:
        lines += [
            "",
            "identificadores ligados a mais de uma inscricao da mesma competicao",
            "------------------------------------------------------------------",
            "  Registrados para investigacao separada da populacao. Nenhum e",
            "  corrigido aqui, e nenhuma inscricao e escolhida entre eles.",
            "",
        ]
        by_category: collections.Counter[str] = collections.Counter(
            item["categoria"] for item in shared)
        for categoria, n in sorted(by_category.items()):
            lines.append(f"  {n:4d}  {categoria}")
        lines.append(f"  {sum(by_category.values()):4d}  total")
        lines.append("")
        for item in shared:
            lines.append(
                f"    {item['evento']} | id={item['identificador']} | "
                f"{item['categoria']} | {item['inscricoes']}")

    if missing_in_db:
        lines += ["", "sem correspondencia no banco", "---------------------------"]
        lines += [f"  {item}" for item in sorted(missing_in_db)]

    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    pendencies.sort(key=lambda r: (r["event"], r["session"], r["number"],
                                   r["car_class"], r["team"], r["outcome"]))
    with args.pendencies.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=PENDENCY_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(pendencies)

    print("\n".join(lines))
    print(f"\npendencias escritas em {args.pendencies}: {len(pendencies)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
