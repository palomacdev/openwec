#!/usr/bin/env python3
"""
Export the legacy result groups whose copies disagree on position or time.

Read-only. It opens the database read-only, writes one CSV, and chooses
nothing: where copies disagree, every copy is exported as its own row with the
provenance a person needs to decide. No canonical row is elected, no duplicate
is removed, no data is changed.

Why position and time
---------------------
Measured over the development database, 85,418 of 94,868 `(session, number)`
groups hold more than one result row, and 98.4% of those agree on everything a
reader sees. The disagreements concentrate in team spelling, which the model
already treats as an attribute rather than an identity.

What is left is small and substantive: 15 groups disagree on finishing
position and 16 on elapsed time. Those are the ones exported here.

Reading the evidence
--------------------
Do not assume a disagreement means one entry was recorded twice with different
outcomes. In every case examined so far the opposite was true: the copies
describe *different entries* sharing a `(session_id, number)` key — different
team, different lap count, different elapsed time. That is evidence against
`(session_id, number)` as an identity, which is why each copy is exported
whole instead of being summarised.

Usage:
    python scripts/export_legacy_divergences.py --dsn "..." --out divergences.csv
"""
from __future__ import annotations

import argparse
import csv
import sys

import psycopg2

FIELDS = [
    "group_key", "series", "season", "event", "session", "number",
    "copies_in_group", "diverges_on",
    "result_id", "car_id", "position", "status", "laps_completed",
    "total_time_s", "car_class", "team", "vehicle",
]

# Groups are selected on position or elapsed time only. count(DISTINCT x)
# returns 0 when every value is NULL, so "> 1" is the test for disagreement;
# "= 1" would silently drop the all-NULL groups.
SELECT_GROUPS = """
WITH grouped AS (
    SELECT r.session_id,
           c.number,
           count(*)                         AS copies,
           count(DISTINCT r.position)       AS positions,
           count(DISTINCT r.total_time_s)   AS times
    FROM results r
    JOIN cars c ON c.id = r.car_id
    GROUP BY r.session_id, c.number
)
SELECT session_id, number, copies,
       positions > 1 AS diverges_position,
       times > 1     AS diverges_time
FROM grouped
WHERE copies > 1 AND (positions > 1 OR times > 1)
ORDER BY session_id, number
"""

SELECT_COPIES = """
SELECT sr.key::text, se.raw_id, e.raw_id, s.name,
       r.id, c.id, r.position, r.status::text, r.laps_completed,
       r.total_time_s, c.car_class, t.name, c.vehicle
FROM results r
JOIN cars c      ON c.id = r.car_id
JOIN sessions s  ON s.id = r.session_id
JOIN events e    ON e.id = s.event_id
JOIN seasons se  ON se.id = e.season_id
JOIN series sr   ON sr.id = se.series_id
LEFT JOIN teams t ON t.id = c.team_id
WHERE r.session_id = %s AND c.number = %s
ORDER BY r.position NULLS LAST, r.laps_completed NULLS LAST, c.id, r.id
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--out", default="legacy_divergences.csv", type=str)
    args = ap.parse_args(argv)

    conn = psycopg2.connect(args.dsn)
    conn.set_session(readonly=True, autocommit=True)
    cur = conn.cursor()
    detail = conn.cursor()

    cur.execute(SELECT_GROUPS)
    groups = cur.fetchall()

    rows: list[dict] = []
    for session_id, number, copies, diverges_position, diverges_time in groups:
        dimensions = " + ".join(
            d for d, flag in (("posicao", diverges_position), ("tempo", diverges_time))
            if flag)
        detail.execute(SELECT_COPIES, (session_id, number))
        for (series, season, event, session, result_id, car_id, position, status,
             laps, total_time, car_class, team, vehicle) in detail.fetchall():
            rows.append({
                "group_key": f"{series}/{season}/{event}|{session}|#{number}",
                "series": series, "season": season, "event": event,
                "session": session, "number": number,
                "copies_in_group": copies, "diverges_on": dimensions,
                "result_id": result_id, "car_id": car_id,
                "position": "" if position is None else position,
                "status": status or "",
                "laps_completed": "" if laps is None else laps,
                "total_time_s": "" if total_time is None else total_time,
                "car_class": car_class or "",
                "team": team or "",
                "vehicle": vehicle or "",
            })

    rows.sort(key=lambda r: (r["group_key"], r["position"] == "", r["position"],
                             r["car_id"], r["result_id"]))
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    distinct_groups = len({r["group_key"] for r in rows})
    print(f"grupos divergentes : {distinct_groups}")
    print(f"linhas exportadas  : {len(rows)}")
    print(f"escritas em {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
