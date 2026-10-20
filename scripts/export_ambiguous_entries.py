#!/usr/bin/env python3
"""
Export the entry groups that no rule in the data can resolve.

Reads the raw classification exports and writes one CSV row per
(event, number, class) group where more than one team appears and neither the
source entry-list identifier nor the vehicle separates them.

These are the cases a human has to decide. Nothing here guesses: the script
reports the evidence and stops. Driver surnames and team names are included
because the reviewer needs them; both are public classification data. No
credentials, paths outside raw/, or environment values are written.

Usage:
    python scripts/export_ambiguous_entries.py [--raw raw] [--out ambiguous_entries.csv]

Exit status is 0 even when groups are found: finding them is the point.
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import io
import re
import sys
import unicodedata
from pathlib import Path

SERIES = ("wec", "elms", "alms", "lemanscup", "imsa")

SOURCE_ID_COLUMNS = ("ECM Participant Id", "IMSA_EventEntryId", "IMSAEventEntryId")

RE_DRIVER_FULL = re.compile(r"DRIVER_\d+$")
RE_DRIVER_LAST = re.compile(r"DRIVER\d+_SECONDNAME$")


def read_csv(path: Path) -> list[dict]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        return []
    return [
        {(k or "").strip().lstrip("﻿"): (v or "").strip() for k, v in row.items() if k}
        for row in csv.DictReader(io.StringIO(text), delimiter=";")
    ]


def normalise(text: str) -> str:
    """Fold case, accents and the filler words that make the same team look new."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().upper()
    text = re.sub(r"\b(TEAM|RACING|MOTORSPORTS?|COMPETITION|SPORT)\b", "", text)
    return re.sub(r"[^A-Z0-9]", "", text)


def source_entry_id(row: dict) -> str:
    for column in SOURCE_ID_COLUMNS:
        value = row.get(column, "").strip()
        if value:
            return value
    return ""


def driver_names(row: dict) -> set[str]:
    names = set()
    for key, value in row.items():
        if not value:
            continue
        if RE_DRIVER_FULL.fullmatch(key) or RE_DRIVER_LAST.fullmatch(key):
            folded = normalise(value)
            if folded and not folded.isdigit():
                names.add(folded)
    return names


def related(a: set[str], b: set[str]) -> bool:
    """A surname in one export is a full name in another. Containment, not equality."""
    return any(x and y and (x in y or y in x) for x in a for y in b)


def group_id(series: str, season: str, event: str, number: str, car_class: str) -> str:
    """
    Stable identity for an ambiguous group.

    Derived from the natural key only, so it survives re-export, database
    rebuilds and id renumbering. Two people running the script on the same raw
    tree get the same ids, which is what makes a decision file replayable.
    """
    payload = "".join((series, season, event, number, car_class))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def load_decisions(path: Path | None) -> dict[str, dict]:
    """Previously recorded decisions, keyed by group_id, carried into the new export."""
    if not path or not path.is_file():
        return {}
    with path.open(newline="", encoding="utf-8") as fh:
        return {
            row["group_id"]: row
            for row in csv.DictReader(fh)
            if row.get("group_id") and row.get("decision")
        }


def classification_files(event_dir: Path):
    for path in event_dir.rglob("*"):
        if path.suffix.upper() == ".CSV" and "classification" in str(path).lower():
            yield path


def session_name(path: Path) -> str:
    return path.parent.parent.name if path.parent.name.lower() == "classification" else path.parent.name


def collect(raw_dir: Path):
    """Yields (series, season, event, number, car_class, per_team) for conflicts."""
    for series in SERIES:
        series_dir = raw_dir / series
        if not series_dir.is_dir():
            continue
        for season_dir in sorted(d for d in series_dir.iterdir() if d.is_dir()):
            for event_dir in sorted(d for d in season_dir.iterdir() if d.is_dir()):
                groups = collections.defaultdict(list)
                for path in classification_files(event_dir):
                    session = session_name(path)
                    for row in read_csv(path):
                        number = row.get("NUMBER", "")
                        if not number:
                            continue
                        key = (number, row.get("CLASS", "").strip().upper())
                        groups[key].append(
                            {
                                "team": row.get("TEAM", "").strip(),
                                "vehicle": row.get("VEHICLE", "").strip(),
                                "source_id": source_entry_id(row),
                                "drivers": driver_names(row),
                                "session": session,
                            }
                        )
                for (number, car_class), rows in groups.items():
                    if len({normalise(r["team"]) for r in rows}) <= 1:
                        continue
                    per_team = collections.defaultdict(
                        lambda: {"spellings": set(), "vehicles": set(),
                                 "source_ids": set(), "drivers": set(), "sessions": set()}
                    )
                    for r in rows:
                        bucket = per_team[normalise(r["team"])]
                        bucket["spellings"].add(r["team"])
                        if r["vehicle"]:
                            bucket["vehicles"].add(r["vehicle"])
                        if r["source_id"]:
                            bucket["source_ids"].add(r["source_id"])
                        bucket["drivers"] |= r["drivers"]
                        bucket["sessions"].add(r["session"])
                    yield series, season_dir.name, event_dir.name, number, car_class, per_team


def verdict(per_team: dict) -> tuple[str, str]:
    """
    Returns (status, reason). 'resolved' only when the data itself separates the
    groups. Driver overlap is reported as evidence, never used to merge.
    """
    buckets = list(per_team.values())

    ids = [b["source_ids"] for b in buckets]
    if all(ids) and not any(a & b for i, a in enumerate(ids) for b in ids[i + 1:]):
        return "resolved", "distinct source entry-list ids"

    vehicles = [b["vehicles"] for b in buckets]
    if all(vehicles) and not any(a & b for i, a in enumerate(vehicles) for b in vehicles[i + 1:]):
        return "ambiguous", "different vehicles, no source id — likely distinct, confirm"

    drivers = [b["drivers"] for b in buckets]
    if all(drivers):
        if any(related(a, b) for i, a in enumerate(drivers) for b in drivers[i + 1:]):
            return "ambiguous", "drivers overlap — likely one entry spelled two ways, confirm"
        return "ambiguous", "drivers disjoint — likely distinct entries, confirm"

    return "ambiguous", "insufficient data (no drivers, no source id)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", default="raw", type=Path)
    parser.add_argument("--out", default="ambiguous_entries.csv", type=Path)
    parser.add_argument("--decisions", type=Path,
                        help="previous export; decisions already recorded are carried over")
    args = parser.parse_args(argv)

    if not args.raw.is_dir():
        print(f"raw directory not found: {args.raw}", file=sys.stderr)
        return 2

    decisions = load_decisions(args.decisions)
    rows, counts = [], collections.Counter()
    for series, season, event, number, car_class, per_team in collect(args.raw):
        status, reason = verdict(per_team)
        counts[status] += 1
        if status == "resolved":
            continue
        gid = group_id(series, season, event, number, car_class)
        previous = decisions.get(gid, {})
        for folded, bucket in sorted(per_team.items()):
            rows.append(
                {
                    "group_id": gid,
                    "series": series,
                    "season": season,
                    "event": event,
                    "number": number,
                    "car_class": car_class,
                    "team_spellings": " | ".join(sorted(bucket["spellings"])),
                    "vehicles": " | ".join(sorted(bucket["vehicles"])),
                    "source_entry_ids": " | ".join(sorted(bucket["source_ids"])),
                    "drivers": " | ".join(sorted(bucket["drivers"])),
                    "sessions": " | ".join(sorted(bucket["sessions"])),
                    "reason": reason,
                    "decision": previous.get("decision", ""),   # same-entry | distinct-entries
                    "decided_by": previous.get("decided_by", ""),
                    "decided_at": previous.get("decided_at", ""),
                }
            )

    fields = ["group_id", "series", "season", "event", "number", "car_class",
              "team_spellings", "vehicles", "source_entry_ids", "drivers",
              "sessions", "reason", "decision", "decided_by", "decided_at"]
    with args.out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    print(f"conflicting groups       : {sum(counts.values())}")
    print(f"  resolved by source id  : {counts['resolved']}")
    print(f"  needing human review   : {counts['ambiguous']}")
    print(f"rows written to {args.out}: {len(rows)}")
    if decisions:
        print(f"decisions carried over   : {len(decisions)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
