"""
OpenWEC — Phase 4: Driver Merge
Merges duplicate driver entries (WEC without country) with IMSA entries (with country).

Strategy:
  - IMSA entry is canonical (has country + rating)
  - All references in result_drivers and laps are updated to point to IMSA entry
  - WEC duplicate entry is deleted

Two modes:
  - Safe merges (LEFT(first_name, 4) matches) → automated
  - Ambiguous merges → saved to needs_review.json for manual decision

Usage:
    python database/enrichment/merge_drivers.py --dry-run
    python database/enrichment/merge_drivers.py
    python database/enrichment/merge_drivers.py --apply-reviewed  ← after editing needs_review.json
"""

import argparse
import json
import psycopg2
import psycopg2.extras
from pathlib import Path


DB_CONFIG = {
    "host":     "127.0.0.1",
    "port":     5433,
    "dbname":   "openwec",
    "user":     "openwec",
    "password": "openwec",
}

REVIEW_FILE = Path("database/enrichment/needs_review.json")


# ── Find merge candidates ─────────────────────────────────────

def find_merge_candidates(cur) -> tuple[list[dict], list[dict]]:
    """
    Returns (safe_merges, needs_review).
    safe_merges: LEFT(first_name, 4) matches → automated
    needs_review: LEFT(first_name, 3) matches but not 4 → manual
    """
    cur.execute("""
        SELECT
            d1.id           AS wec_id,
            d1.first_name   AS wec_first,
            d1.last_name    AS wec_last,
            d2.id           AS imsa_id,
            d2.first_name   AS imsa_first,
            d2.last_name    AS imsa_last,
            d2.country,
            d2.imsa_driver_rating::text AS rating
        FROM drivers d1
        JOIN drivers d2
            ON d1.last_name = d2.last_name
            AND LEFT(d1.first_name, 3) = LEFT(d2.first_name, 3)
            AND d1.id != d2.id
        WHERE d1.country IS NULL
          AND d2.country IS NOT NULL
        ORDER BY d1.last_name, d1.first_name
    """)
    rows = [dict(r) for r in cur.fetchall()]

    safe        = []
    needs_review = []

    for row in rows:
        wec_first  = (row["wec_first"]  or "").upper()
        imsa_first = (row["imsa_first"] or "").upper()
        if wec_first[:4] == imsa_first[:4]:
            safe.append(row)
        else:
            needs_review.append(row)

    return safe, needs_review


# ── Merge operation ───────────────────────────────────────────

def merge_driver(cur, wec_id: int, imsa_id: int, dry_run: bool) -> dict:
    """
    Redirects all references from wec_id to imsa_id, then deletes wec_id.
    Returns stats dict.
    """
    # Count references before merge
    cur.execute("SELECT COUNT(*) AS n FROM result_drivers WHERE driver_id = %s", (wec_id,))
    rd_count = cur.fetchone()["n"]

    cur.execute("SELECT COUNT(*) AS n FROM laps WHERE driver_id = %s", (wec_id,))
    laps_count = cur.fetchone()["n"]

    if dry_run:
        return {"result_drivers": rd_count, "laps": laps_count}

    # Update result_drivers
    if rd_count > 0:
        cur.execute("""
            UPDATE result_drivers SET driver_id = %s WHERE driver_id = %s
        """, (imsa_id, wec_id))

    # Update laps
    if laps_count > 0:
        cur.execute("""
            UPDATE laps SET driver_id = %s WHERE driver_id = %s
        """, (imsa_id, wec_id))

    # Delete WEC duplicate
    cur.execute("DELETE FROM drivers WHERE id = %s", (wec_id,))

    return {"result_drivers": rd_count, "laps": laps_count}


# ── Main ─────────────────────────────────────────────────────

def run(dry_run: bool = False, apply_reviewed: bool = False):
    print("=" * 60)
    print("OpenWEC — Phase 4: Driver Merge")
    print(f"  Dry run:        {dry_run}")
    print(f"  Apply reviewed: {apply_reviewed}")
    print("=" * 60)

    conn = psycopg2.connect(**DB_CONFIG)
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    safe, needs_review = find_merge_candidates(cur)

    print(f"\n  Safe merges:    {len(safe)}")
    print(f"  Needs review:   {len(needs_review)}")

    # ── Safe merges ───────────────────────────────────────────
    print(f"\n[SAFE MERGES]")
    total_rd   = 0
    total_laps = 0
    merged     = 0

    write_cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    for m in safe:
        print(f"  [{m['wec_id']:5d}] '{m['wec_first']}' '{m['wec_last']}'")
        print(f"         → [{m['imsa_id']:5d}] '{m['imsa_first']}' country={m['country']} rating={m['rating']}")

        stats = merge_driver(write_cur, m["wec_id"], m["imsa_id"], dry_run)
        total_rd   += stats["result_drivers"]
        total_laps += stats["laps"]
        merged     += 1

        print(f"           result_drivers: {stats['result_drivers']}  laps: {stats['laps']}")

    if not dry_run:
        conn.commit()

    print(f"\n  ✓ {merged} merges {'would be ' if dry_run else ''}applied")
    print(f"  result_drivers updated: {total_rd}")
    print(f"  laps updated:           {total_laps}")

    # ── Needs review ──────────────────────────────────────────
    if apply_reviewed and REVIEW_FILE.exists():
        print(f"\n[APPLY REVIEWED MERGES]")
        with open(REVIEW_FILE) as f:
            reviewed = json.load(f)

        approved = [r for r in reviewed if r.get("action") == "merge"]
        skipped  = [r for r in reviewed if r.get("action") != "merge"]

        print(f"  Approved: {len(approved)}  Skipped: {len(skipped)}")

        for m in approved:
            print(f"  Merging [{m['wec_id']}] → [{m['imsa_id']}]")
            if not dry_run:
                stats = merge_driver(write_cur, m["wec_id"], m["imsa_id"], dry_run=False)
                print(f"    result_drivers: {stats['result_drivers']}  laps: {stats['laps']}")

        if not dry_run:
            conn.commit()

    else:
        # Save needs_review to file
        print(f"\n[NEEDS REVIEW — {len(needs_review)} pairs]")
        for m in needs_review:
            print(f"  [{m['wec_id']:5d}] '{m['wec_first']}' '{m['wec_last']}'")
            print(f"         → [{m['imsa_id']:5d}] '{m['imsa_first']}' country={m['country']} rating={m['rating']}")

        review_data = [
            {
                "wec_id":    m["wec_id"],
                "wec_name":  f"{m['wec_first']} {m['wec_last']}",
                "imsa_id":   m["imsa_id"],
                "imsa_name": f"{m['imsa_first']} {m['imsa_last']}",
                "country":   m["country"],
                "rating":    m["rating"],
                "action":    "merge",  # change to "skip" to not merge
            }
            for m in needs_review
        ]

        REVIEW_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(REVIEW_FILE, "w") as f:
            json.dump(review_data, f, indent=2, ensure_ascii=False)

        print(f"\n  [SAVED] {REVIEW_FILE}")
        print(f"  Review the file and set action='skip' for pairs that should NOT be merged.")
        print(f"  Then run: python database/enrichment/merge_drivers.py --apply-reviewed")

    write_cur.close()
    cur.close()
    conn.close()

    print(f"\n  Next step: Wikidata lookup for the remaining 770 drivers without country")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Driver Merge")
    parser.add_argument("--dry-run",        action="store_true")
    parser.add_argument("--apply-reviewed", action="store_true",
                        help="Apply merges from needs_review.json")
    args = parser.parse_args()
    run(dry_run=args.dry_run, apply_reviewed=args.apply_reviewed)