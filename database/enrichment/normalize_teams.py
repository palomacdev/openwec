"""
OpenWEC — Phase 4: Team Normalization and Deduplication

Fixes team name inconsistencies across seasons and series:
  - Capitalisation: "AF CORSE" → "AF Corse"
  - Duplicates: multiple entries for same team → merge into one

Strategy:
  - clean_key = lowercase + alphanumeric only (used for matching)
  - canonical name = picked by scoring (Title Case > ALL CAPS > other)
  - all car references updated to canonical team_id
  - duplicate team entries deleted

Usage:
    python database/enrichment/normalize_teams.py --dry-run
    python database/enrichment/normalize_teams.py
"""

import argparse
import re
import psycopg2
import psycopg2.extras
from collections import defaultdict


DB_CONFIG = {
    "host":     "127.0.0.1",
    "port":     5433,
    "dbname":   "openwec",
    "user":     "openwec",
    "password": "openwec",
}


def clean_key(name: str) -> str:
    """Strips non-alphanumeric and lowercases for matching."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def name_score(name: str) -> int:
    """
    Scores a name for canonicality. Higher = better.
    Title Case with spaces > mixed case > ALL CAPS > all lower
    """
    if not name:
        return 0
    score = 0
    # Has at least one space (not a single word)
    if " " in name:
        score += 10
    # Not all uppercase
    if name != name.upper():
        score += 5
    # Not all lowercase
    if name != name.lower():
        score += 3
    # Starts with uppercase
    if name[0].isupper():
        score += 2
    # Title-case-ish (most words capitalized)
    words = name.split()
    cap_words = sum(1 for w in words if w and w[0].isupper())
    if words and cap_words / len(words) > 0.6:
        score += 4
    return score


def pick_canonical(names: list[str]) -> str:
    """Picks the best name from a group of duplicates."""
    return max(names, key=name_score)


def find_duplicate_groups(cur) -> list[dict]:
    """Returns groups of team IDs/names that share the same clean_key."""
    cur.execute("SELECT id, name FROM teams ORDER BY id")
    rows = cur.fetchall()

    groups_by_key: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        key = clean_key(row["name"])
        groups_by_key[key].append({"id": row["id"], "name": row["name"]})

    duplicates = []
    for key, group in groups_by_key.items():
        if len(group) > 1:
            canonical_name = pick_canonical([g["name"] for g in group])
            canonical_id   = next(g["id"] for g in group if g["name"] == canonical_name)
            # If tie, pick lowest id
            if sum(1 for g in group if g["name"] == canonical_name) > 1:
                canonical_id = min(g["id"] for g in group if g["name"] == canonical_name)

            duplicates.append({
                "clean_key":      key,
                "canonical_id":   canonical_id,
                "canonical_name": canonical_name,
                "duplicates":     [g for g in group if g["id"] != canonical_id],
            })

    return sorted(duplicates, key=lambda x: x["canonical_name"])


def run(dry_run: bool = False):
    print("=" * 60)
    print("OpenWEC — Phase 4: Team Normalization")
    print(f"  Dry run: {dry_run}")
    print("=" * 60)

    conn = psycopg2.connect(**DB_CONFIG)
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT COUNT(*) AS n FROM teams")
    total_before = cur.fetchone()["n"]
    print(f"\n  Teams before: {total_before}")

    groups = find_duplicate_groups(cur)
    total_to_delete = sum(len(g["duplicates"]) for g in groups)

    print(f"  Duplicate groups: {len(groups)}")
    print(f"  Teams to merge:   {total_to_delete}")
    print(f"  Teams after:      {total_before - total_to_delete}")

    print(f"\n[GROUPS] (showing first 20)")
    for g in groups[:20]:
        print(f"\n  canonical: [{g['canonical_id']:5d}] '{g['canonical_name']}'")
        for d in g["duplicates"]:
            print(f"    merge ← [{d['id']:5d}] '{d['name']}'")

    if dry_run:
        print(f"\n[DRY RUN] No changes written.")
        cur.close()
        conn.close()
        return

    print(f"\n[APPLY] Merging {total_to_delete} duplicate teams...")

    write_cur = conn.cursor()
    merged  = 0
    deleted = 0
    errors  = 0

    for g in groups:
        canonical_id = g["canonical_id"]
        for dup in g["duplicates"]:
            dup_id = dup["id"]
            try:
                # Update all cars pointing to the duplicate
                write_cur.execute("""
                    UPDATE cars SET team_id = %s WHERE team_id = %s
                """, (canonical_id, dup_id))
                cars_updated = write_cur.rowcount

                # Delete the duplicate team
                write_cur.execute("DELETE FROM teams WHERE id = %s", (dup_id,))
                conn.commit()

                merged  += 1
                deleted += 1

            except Exception as e:
                conn.rollback()
                errors += 1
                print(f"  [ERROR] merge {dup_id} → {canonical_id}: {e}")

    write_cur.close()
    cur.close()
    conn.close()

    print(f"\n  ✓ Merged:  {merged}")
    print(f"  ✓ Deleted: {deleted}")
    print(f"  ✗ Errors:  {errors}")
    print(f"  Teams remaining: {total_before - deleted}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Team Normalization")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(dry_run=args.dry_run)