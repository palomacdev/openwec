"""
OpenWEC — Phase 4: Driver Name Normalization

Fixes two problems found in the database:
  1. Compound name particles split into first_name
     e.g. first="Roman DE", last="ANGELIS" → first="Roman", last="De Angelis"

  2. Last names in ALL CAPS (WEC/ELMS format from CSV)
     e.g. last="PIER GUIDI" → last="Pier Guidi"

Also detects duplicate drivers (same person, different spellings)
and reports them for manual review.

Usage:
    python database/enrichment/normalize_drivers.py --dry-run
    python database/enrichment/normalize_drivers.py
"""

import argparse
import psycopg2
import psycopg2.extras
from collections import defaultdict


from database.db import DB_CONFIG

# Particles that belong to the last name
# Sorted longest first to match "VAN DER" before "VAN"
PARTICLES = [
    "VAN DER", "VAN DEN", "VAN DE", "DE LA", "DE LOS", "DE LAS",
    "DEL LA", "DELLA",
    "VAN", "VON", "DE", "DI", "DA", "DU", "DES", "DOS", "DAS",
    "DEN", "DER", "TEN", "TER", "OP", "AL", "EL", "BIN", "LE", "LA",
    "FELIX DA", "MOURA DE", "VIVIAN DE LA", "SENNA DE",
]


def title_case_name(name: str) -> str:
    if not name:
        return name
    return " ".join(word.capitalize() for word in name.split())


def normalize_first_name(first: str) -> str:
    """
    Normalizes first name capitalisation.
    Handles hyphenated names and multi-word first names.
    "MIGUEL" → "Miguel"
    "jean-karl" → "Jean-Karl"
    "Roman senna" → "Roman Senna"
    """
    if not first:
        return first
    parts = first.split()
    result = []
    for part in parts:
        subparts = part.split("-")
        result.append("-".join(p.capitalize() for p in subparts))
    return " ".join(result)


def split_compound_name(first: str, last: str) -> tuple[str, str]:
    """
    Detects particles at the end of first_name and moves them to last_name.

    e.g.:
      first="Roman DE",         last="ANGELIS"  → ("Roman", "De Angelis")
      first="Ahmad AL",         last="HARTHY"   → ("Ahmad", "Al Harthy")
      first="Antonio FELIX DA", last="COSTA"    → ("Antonio Felix", "Da Costa")
      first="Sheldon VAN DER",  last="LINDE"    → ("Sheldon", "Van Der Linde")
    """
    first = first.strip()
    last  = last.strip()

    first_upper = first.upper()
    matched_particle = None

    for particle in PARTICLES:
        if first_upper.endswith(" " + particle):
            matched_particle = particle
            break
        if first_upper == particle:
            matched_particle = particle
            break

    if matched_particle:
        idx = first_upper.rfind(matched_particle)
        clean_first = first[:idx].strip()
        new_last_raw = matched_particle + " " + last
        new_last = title_case_name(new_last_raw)
        return normalize_first_name(clean_first), new_last

    # No particle — just fix capitalisation
    return normalize_first_name(first), title_case_name(last)


def find_duplicates(drivers: list[dict]) -> list[list[dict]]:
    """
    Finds potential duplicate drivers — same last name, similar first name.
    Returns groups of likely duplicates for review.
    """
    by_last: dict[str, list[dict]] = defaultdict(list)
    for d in drivers:
        key = d["last_name"].upper().strip()
        by_last[key].append(d)

    duplicates = []
    for last, group in by_last.items():
        if len(group) > 1:
            sub_groups: dict[str, list[dict]] = defaultdict(list)
            for d in group:
                prefix = d["first_name"][:3].upper() if d["first_name"] else "?"
                sub_groups[prefix].append(d)
            for prefix, sub in sub_groups.items():
                if len(sub) > 1:
                    duplicates.append(sub)

    return duplicates


def run(dry_run: bool = False):
    print("=" * 60)
    print("OpenWEC — Phase 4: Driver Name Normalization")
    print(f"  Dry run: {dry_run}")
    print("=" * 60)

    conn = psycopg2.connect(**DB_CONFIG)
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("""
        SELECT id, first_name, last_name, country, imsa_driver_rating
        FROM drivers
        ORDER BY last_name, first_name
    """)
    drivers = [dict(r) for r in cur.fetchall()]
    print(f"\n  Total drivers: {len(drivers)}")

    # ── Pass 1: Fix compound names and capitalisation ──────────
    print("\n[PASS 1] Fixing compound names and capitalisation...")

    compound_fixes = []
    cap_fixes      = []

    for d in drivers:
        original_first = d["first_name"] or ""
        original_last  = d["last_name"] or ""

        new_first, new_last = split_compound_name(original_first, original_last)

        changed = (new_first != original_first or new_last != original_last)

        if changed:
            entry = {
                "id":           d["id"],
                "old_first":    original_first,
                "old_last":     original_last,
                "new_first":    new_first,
                "new_last":     new_last,
                "compound_fix": new_last != title_case_name(original_last),
            }
            if entry["compound_fix"]:
                compound_fixes.append(entry)
            else:
                cap_fixes.append(entry)

    print(f"  Compound name fixes: {len(compound_fixes)}")
    for f in compound_fixes:
        print(f"    [{f['id']:5d}] '{f['old_first']}' '{f['old_last']}'")
        print(f"           → '{f['new_first']}' '{f['new_last']}'")

    print(f"\n  Capitalisation fixes: {len(cap_fixes)}")
    print(f"  (showing first 10)")
    for f in cap_fixes[:10]:
        print(f"    [{f['id']:5d}] '{f['old_last']}' → '{f['new_last']}'")

    # ── Pass 2: Find duplicates ────────────────────────────────
    print(f"\n[PASS 2] Detecting potential duplicates...")

    normalized = []
    for d in drivers:
        first, last = split_compound_name(
            d["first_name"] or "", d["last_name"] or ""
        )
        normalized.append({**d, "first_name": normalize_first_name(first), "last_name": last})

    duplicates = find_duplicates(normalized)
    print(f"  Potential duplicate groups: {len(duplicates)}")
    for group in duplicates[:15]:
        print(f"\n  GROUP (last: '{group[0]['last_name']}')")
        for d in group:
            print(f"    [{d['id']:5d}] '{d['first_name']}' '{d['last_name']}' "
                  f"country={d['country']} rating={d['imsa_driver_rating']}")

    # ── Apply fixes ────────────────────────────────────────────
    if dry_run:
        print(f"\n[DRY RUN] No changes written.")
        print(f"  Would fix {len(compound_fixes)} compound names")
        print(f"  Would fix {len(cap_fixes)} capitalisation issues")
        cur.close()
        conn.close()
        return

    print(f"\n[APPLY] Writing fixes to database...")

    all_fixes = compound_fixes + cap_fixes
    updated = 0
    skipped = 0

    write_cur = conn.cursor()
    for f in all_fixes:
        try:
            write_cur.execute("""
                UPDATE drivers
                SET first_name = %s, last_name = %s
                WHERE id = %s
            """, (f["new_first"], f["new_last"], f["id"]))
            conn.commit()
            updated += 1
        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            skipped += 1
            print(f"  [SKIP] id={f['id']} '{f['new_first']}' '{f['new_last']}' already exists")

    write_cur.close()
    cur.close()
    conn.close()

    print(f"\n  ✓ {updated} drivers updated")
    print(f"  ⊘ {skipped} skipped (already exist with correct name)")
    print(f"\n  Next step: merge duplicate drivers")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Driver Name Normalization")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(dry_run=args.dry_run)