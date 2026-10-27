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

Usage — as a module, not as a file. The script imports `database.db`, which
resolves only when the repository root is on the path, and running the file
directly puts its own directory there instead:

    python -m database.enrichment.normalize_teams --dry-run
    python -m database.enrichment.normalize_teams

Exit codes:
    0  nothing failed, including when there was nothing to merge
    1  it ran and at least one merge failed; the summary names which

The output is ASCII on purpose. The decorations this file used to print — an
arrow, a tick, a cross — have no cp1252 encoding, which is what a Windows
console uses by default, and printing one raised UnicodeEncodeError before a
single merge had been attempted.
"""

import argparse
import re
import sys
import psycopg2
import psycopg2.extras
from collections import defaultdict


from database.db import DB_CONFIG


def _primeira_linha(erro: Exception) -> str:
    """
    One line of a message that psycopg2 writes over several.

    A foreign key violation arrives as the error, then DETAIL, then sometimes
    a hint. The whole text is printed where the failure happens; the summary
    at the end needs the part that identifies it, on one line each.
    """
    texto = " ".join(str(erro).split())
    return texto or erro.__class__.__name__


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


def run(dry_run: bool = False) -> int:
    """
    Merges teams whose names differ only in spelling. Returns an exit code.

    Each merge is its own transaction, as it always was: the UPDATE that
    repoints `cars` and the DELETE that removes the duplicate commit together
    or not at all, and a failure rolls that pair back without reaching the
    merges already committed. What changes here is only that the failure
    reaches the caller — the run used to end with `Errors: 1` and exit 0.

    `cars` stays the only table repointed. `entries` and `entry_team_history`
    reference `teams` as well, so a team either of them holds cannot be
    deleted and that merge fails on the foreign key. That is the current
    policy and not an oversight to settle here: what a team merge means for a
    competitive entry is the entry model's question. The failure is reported
    with its reason instead of being counted and forgotten.
    """
    print("=" * 60)
    print("OpenWEC - Phase 4: Team Normalization")
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
            print(f"    merge <- [{d['id']:5d}] '{d['name']}'")

    if dry_run:
        print(f"\n[DRY RUN] No changes written.")
        cur.close()
        conn.close()
        return 0

    print(f"\n[APPLY] Merging {total_to_delete} duplicate teams...")

    write_cur = conn.cursor()
    merged = 0
    falhas: list[tuple[int, int, str]] = []
    interrompido = False

    for g in groups:
        if interrompido:
            break
        canonical_id = g["canonical_id"]
        for dup in g["duplicates"]:
            dup_id = dup["id"]
            try:
                # Update all cars pointing to the duplicate
                write_cur.execute("""
                    UPDATE cars SET team_id = %s WHERE team_id = %s
                """, (canonical_id, dup_id))

                # Delete the duplicate team
                write_cur.execute("DELETE FROM teams WHERE id = %s", (dup_id,))
                conn.commit()

                merged += 1

            except Exception as falha:
                # The original error first and whole, before anything else can
                # go wrong: a rollback that fails must not be what the reader
                # is left holding.
                print(f"  [ERROR] merge {dup_id} -> {canonical_id}: {falha}")
                motivo = _primeira_linha(falha)
                try:
                    conn.rollback()
                except Exception as falha_do_rollback:
                    # The connection cannot be trusted any more, and every
                    # merge left would fail the same way. Stop, and say why.
                    razao_do_rollback = _primeira_linha(falha_do_rollback)
                    falhas.append((dup_id, canonical_id,
                                   f"{motivo} | o rollback tambem falhou: "
                                   f"{razao_do_rollback}"))
                    print(f"  [ABORT] o rollback falhou ({razao_do_rollback}); "
                          f"as fusoes restantes nao serao tentadas")
                    interrompido = True
                    break
                falhas.append((dup_id, canonical_id, motivo))

    for fechavel in (write_cur, cur, conn):
        try:
            fechavel.close()
        except Exception:                 # noqa: BLE001
            pass                          # ja fechado, ou a conexao se perdeu

    print(f"\n  Merged:  {merged}")
    print(f"  Deleted: {merged}")
    print(f"  Errors:  {len(falhas)}")
    print(f"  Teams remaining: {total_before - merged}")

    if falhas:
        print(f"\n[FAILURES] {len(falhas)} merge(s) nao foram aplicados")
        for de, para, motivo in falhas:
            print(f"  team {de} -> {para}: {motivo}")
        if interrompido:
            print("  (a execucao parou antes de tentar as fusoes restantes)")
        return 1
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Team Normalization")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    sys.exit(run(dry_run=args.dry_run))