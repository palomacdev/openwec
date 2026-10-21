#!/usr/bin/env python3
"""
Export the merge hypotheses for human review.

A hypothesis is raised when one group of entry-list identifiers is never
observed outside another group's sessions and fields no class of its own. That
shape is what an entry list with absentees looks like — and also what a second
championship confined to shared sessions looks like. The discovery refuses to
decide between them, so each one is exported with its evidence.

The A–E grade is a PRIORITY OF INVESTIGATION, not a verdict on identity. Grade
A is where to look first, not a merge that has been established.

Decisions are carried forward between exports by hypothesis_id, but only while
the evidence behind them is unchanged: each row carries an evidence_digest, and
a decision recorded against a different digest comes back marked stale instead
of being reapplied.

Usage:
    python scripts/export_merge_hypotheses.py [--raw raw] [--out merge_hypotheses.csv]
                                              [--decisions previous.csv]

Exit status is 0 even when hypotheses are found: finding them is the point.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "database"))
from competition_discovery import analyse, iter_events  # noqa: E402

FIELDS = [
    "hypothesis_id", "priority", "series", "season", "event",
    "smaller_group_id", "larger_group_id",
    "smaller_identifiers", "larger_identifiers",
    "smaller_identifier_sample", "larger_identifier_sample",
    "shared_sessions", "smaller_only_sessions", "larger_only_sessions",
    "smaller_classes", "larger_classes",
    "smaller_numbers", "larger_numbers",
    "smaller_teams", "larger_teams",
    "evidence_for", "evidence_against", "evidence_absent",
    "criteria_raising_hypothesis",
    "evidence_digest", "decision", "decided_by", "decided_at", "decision_stale",
]


def group_id(event_key: str, ids: set[str]) -> str:
    """
    Stable name for an identifier group. Derived from the identifiers
    themselves, which is what makes a decision replayable across re-exports and
    database rebuilds. If the identifiers change the group is a different group,
    and that is the intended behaviour.
    """
    payload = event_key + "|" + ",".join(sorted(ids))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def digest(row: dict) -> str:
    """Everything a reviewer would have looked at, hashed."""
    material = [row[f] for f in FIELDS
                if f not in ("evidence_digest", "decision", "decided_by",
                             "decided_at", "decision_stale", "priority")]
    return hashlib.sha256("|".join(map(str, material)).encode("utf-8")).hexdigest()[:32]


def grade(*, same_entry: bool, conflicting: bool,
          teams_contained: bool, teams_overlap: bool) -> str:
    """
    Priority of INVESTIGATION. None of these grades asserts that two groups are
    the same competition; they order the queue.

    A shared car number is not evidence against a merge when the team matches on
    both sides: that is one entry recorded under two identifiers, which happens
    inside a single list. It is evidence against when the teams differ.

      A  the same entry appears in both groups under different identifiers
      B  no shared number, and every team of the smaller group fields in the larger
      C  no shared number, teams partly in common
      D  a number is shared by DIFFERENT teams — tells against the merge
      E  nothing but the subset heuristic, and no team in common either
    """
    if conflicting:
        return "D"
    if same_entry:
        return "A"
    if teams_contained:
        return "B"
    if teams_overlap:
        return "C"
    return "E"


def load_decisions(path: Path | None) -> dict[str, dict]:
    if not path or not path.is_file():
        return {}
    with path.open(newline="", encoding="utf-8") as fh:
        return {r["hypothesis_id"]: r for r in csv.DictReader(fh)
                if r.get("hypothesis_id") and r.get("decision")}


def collect(raw: Path):
    for key, ev in iter_events(raw):
        a = analyse(ev)
        if not a or not a["hyp"]:
            continue
        for small, big, _, _ in a["hyp"]:
            s, b = a["comps"][small], a["comps"][big]
            s_teams = {t for _, t in s["pairs"]}
            b_teams = {t for _, t in b["pairs"]}
            shared_numbers = s["numbers"] & b["numbers"]

            same_entry, conflicting = [], []
            for n in sorted(shared_numbers):
                sp = {t for num, t in s["pairs"] if num == n}
                bp = {t for num, t in b["pairs"] if num == n}
                (same_entry if sp & bp else conflicting).append(n)

            forw, against, absent = [], [], []
            if same_entry:
                forw.append("mesma inscricao com dois identificadores: #"
                            + ", #".join(same_entry))
            if conflicting:
                against.append("numero compartilhado com equipe diferente: #"
                               + ", #".join(conflicting))
            teams_contained = bool(s_teams) and s_teams <= b_teams
            teams_overlap = bool(s_teams & b_teams)
            if teams_contained:
                forw.append("equipes do menor contidas no maior")
            elif teams_overlap:
                forw.append("equipes parcialmente comuns")
            elif s_teams and b_teams:
                # Weak, and recorded as such: two lists of one championship can
                # legitimately share no team when the smaller is a handful of
                # absentees from a different squad.
                absent.append("nenhuma equipe em comum: sinal fraco contra a fusao, "
                              "nao conclusivo")
            if not shared_numbers:
                absent.append("numeros disjuntos: nao distingue lista com ausentes "
                              "de campeonato separado")
            if not s["window"] or not b["window"]:
                absent.append("sem janela temporal em pelo menos um dos grupos")
            if set(s["classes"]) == set(b["classes"]):
                absent.append("classes identicas: nao discrimina")

            s_ids, b_ids = sorted(s["ids"]), sorted(b["ids"])
            row = {
                "hypothesis_id": "",
                "priority": "",
                "series": key.split("/")[0],
                "season": key.split("/")[1],
                "event": key.split("/")[2],
                "smaller_group_id": group_id(key, s["ids"]),
                "larger_group_id": group_id(key, b["ids"]),
                "smaller_identifiers": len(s_ids),
                "larger_identifiers": len(b_ids),
                "smaller_identifier_sample": " | ".join(s_ids[:6]),
                "larger_identifier_sample": " | ".join(b_ids[:6]),
                "shared_sessions": " | ".join(sorted(s["sessions"] & b["sessions"])),
                "smaller_only_sessions": " | ".join(sorted(s["sessions"] - b["sessions"])),
                "larger_only_sessions": " | ".join(sorted(b["sessions"] - s["sessions"])),
                "smaller_classes": " | ".join(sorted(s["classes"])),
                "larger_classes": " | ".join(sorted(b["classes"])),
                "smaller_numbers": " | ".join(sorted(s["numbers"])),
                "larger_numbers": " | ".join(sorted(b["numbers"])),
                "smaller_teams": " | ".join(sorted(s_teams)),
                "larger_teams": " | ".join(sorted(b_teams)),
                "evidence_for": " ; ".join(forw),
                "evidence_against": " ; ".join(against),
                "evidence_absent": " ; ".join(absent),
                "criteria_raising_hypothesis":
                    "sessoes do menor contidas nas do maior ; "
                    "classes do menor contidas nas do maior ; "
                    "nenhuma classe propria",
                "evidence_digest": "",
                "decision": "", "decided_by": "", "decided_at": "",
                "decision_stale": "",
            }
            row["priority"] = grade(same_entry=bool(same_entry),
                                    conflicting=bool(conflicting),
                                    teams_contained=teams_contained,
                                    teams_overlap=teams_overlap)
            row["hypothesis_id"] = hashlib.sha256(
                (key + "|" + row["smaller_group_id"] + "|" + row["larger_group_id"]
                 ).encode("utf-8")).hexdigest()[:16]
            row["evidence_digest"] = digest(row)
            yield row


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw", default="raw", type=Path)
    ap.add_argument("--out", default="merge_hypotheses.csv", type=Path)
    ap.add_argument("--decisions", type=Path,
                    help="previous export; decisions are carried over while the "
                         "evidence digest still matches")
    args = ap.parse_args(argv)

    if not args.raw.is_dir():
        print(f"raw directory not found: {args.raw}", file=sys.stderr)
        return 2

    previous = load_decisions(args.decisions)
    rows = sorted(collect(args.raw),
                  key=lambda r: (r["priority"], r["series"], r["season"], r["event"]))

    carried = stale = 0
    for r in rows:
        prev = previous.get(r["hypothesis_id"])
        if not prev:
            continue
        if prev.get("evidence_digest") == r["evidence_digest"]:
            r["decision"] = prev.get("decision", "")
            r["decided_by"] = prev.get("decided_by", "")
            r["decided_at"] = prev.get("decided_at", "")
            carried += 1
        else:
            # The evidence moved under the decision. It is reported, never
            # reapplied: a merge approved against one set of sessions is not
            # approved against another.
            r["decision"] = ""
            r["decided_by"] = prev.get("decided_by", "")
            r["decided_at"] = prev.get("decided_at", "")
            r["decision_stale"] = (f"decisao '{prev.get('decision')}' registrada contra "
                                   f"digest {prev.get('evidence_digest', '')[:12]}; "
                                   f"evidencia mudou, revisar novamente")
            stale += 1

    with args.out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    counts = {}
    for r in rows:
        counts[r["priority"]] = counts.get(r["priority"], 0) + 1
    print(f"hipoteses                : {len(rows)}")
    for g in sorted(counts):
        print(f"  prioridade {g}           : {counts[g]}")
    print(f"escritas em {args.out}")
    if previous:
        print(f"decisoes reaproveitadas  : {carried}")
        print(f"decisoes obsoletas       : {stale}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
