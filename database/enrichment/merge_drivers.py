"""
OpenWEC — Phase 4: Driver Merge — DISCOVERY AND REVIEW ONLY

Finds drivers that may be duplicates and keeps a review file of them.
**It cannot merge anything.** The statements that redirected references and
deleted rows are gone from this file; applying a merge raises and exits
non-zero. Enabling a real consolidation is a separate project, and §12 of
docs/data-integrity/driver-consolidation-assessment.md says what it needs
first.

Why the previous version was unsafe
-----------------------------------
It ran three destructive statements — UPDATE result_drivers, UPDATE laps,
DELETE FROM drivers — and reached them by three paths:

  python merge_drivers.py                 merged and committed, no flag asked
  python merge_drivers.py --apply-reviewed  merged, then merged again from
                                          needs_review.json
  (only --dry-run was safe)

What authorised those merges was not a decision. It was a heuristic:
`LEFT(first_name, 4)` matching, with the row that has a country surviving
and the other deleted. The audit of PR #46 measured what that rests on:

  · country **diverges between rows of the same person** — Devlin
    DeFrancesco is CAN and ITA under one imsa_driver_id — so "has a country"
    is not evidence of being canonical
  · a four-letter prefix is similarity, not identity: Christian and
    Christopher share one
  · needs_review.json, the file that was meant to hold human review, was
    committed with "action": "merge" on all fifteen pairs, including
    Nicolas Minassian into Nic Minassian — a case PRs #44 and #45 left
    open, and whose direction the source contradicts
  · the file was written and read without an explicit encoding, so the
    committed copy was cp1252 and unreadable on a Linux runner
  · one commit covered every merge, with no verification afterwards and no
    way back short of a restore

What the three modes do now
---------------------------
  discover (default)  queries for candidate pairs and refreshes the review
                      file. Reads only.
  review              validates the review file and reports each decision.
                      Reads only; touches no database.
  apply               refuses, explains, and exits 2.

`--dry-run` and `--apply-reviewed` are kept so that an old command line
still parses, and both now land on a refusal rather than on a merge.

How a candidate is presented
----------------------------
A candidate is a pair of ids with their stored names and attributes, and
**no direction**. Which row would survive is part of the decision, not of
the discovery: PR #46 measured seven plausible criteria and found that in
none of the 1.079 duplicate groups do they all agree.

Why "action": "merge" alone is not approval
-------------------------------------------
Because it was the default the old script wrote. A file carrying it proves
only that the script ran, not that a person looked. Decisions are therefore
read as approval only when they name the direction explicitly — and even
then, nothing applies them, because applying is blocked.

Usage:
    python database/enrichment/merge_drivers.py                # discover
    python database/enrichment/merge_drivers.py --review       # report
    python database/enrichment/merge_drivers.py --apply-reviewed  # refuses
"""

import argparse
import json
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras

from database.db import DB_CONFIG

REVIEW_FILE = Path("database/enrichment/needs_review.json")

# A decision may hold one of these. Anything else is rejected, and `merge`
# is listed because the old default wrote it, not because it authorises:
# see `is_approved`.
PENDING = "pending"
SKIP = "skip"
LEGACY_MERGE = "merge"
KNOWN_ACTIONS = (PENDING, SKIP, LEGACY_MERGE)


class MergeBlocked(RuntimeError):
    """Raised wherever a real merge would have happened."""


class ReviewFileInvalid(ValueError):
    """The review file cannot be trusted, so nothing is read from it."""


# ── Review file ───────────────────────────────────────────────

def read_review_file(path: Path | None = None) -> list[dict]:
    """
    Loads the review file, or raises. A file that cannot be parsed, decoded
    or validated yields no decisions at all — never a partial list, because
    a partial list is indistinguishable from an approved one.
    """
    # Resolved at call time, not bound as a default: a default argument
    # binds at import, and a test that redirects REVIEW_FILE would still
    # have written over the versioned file.
    path = path or REVIEW_FILE
    if not path.exists():
        return []
    try:
        texto = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ReviewFileInvalid(
            f"{path} is not valid UTF-8 ({exc}). The old script wrote it in "
            f"the platform's default encoding; re-create it with --discover."
        ) from exc
    try:
        dados = json.loads(texto)
    except json.JSONDecodeError as exc:
        raise ReviewFileInvalid(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(dados, list):
        raise ReviewFileInvalid(f"{path} must hold a list, found {type(dados).__name__}")

    vistos: set[tuple[int, int]] = set()
    for i, item in enumerate(dados):
        validate_decision(item, i, vistos, path)
    return dados


def validate_decision(item, indice: int, vistos: set,
                      path: Path | None = None) -> None:
    onde = f"{path or REVIEW_FILE} entry {indice}"
    if not isinstance(item, dict):
        raise ReviewFileInvalid(f"{onde}: expected an object")

    ids = {}
    for campo in ("wec_id", "imsa_id"):
        valor = item.get(campo)
        if not isinstance(valor, int) or isinstance(valor, bool) or valor <= 0:
            raise ReviewFileInvalid(f"{onde}: {campo} must be a positive integer")
        ids[campo] = valor
    if ids["wec_id"] == ids["imsa_id"]:
        raise ReviewFileInvalid(
            f"{onde}: a driver cannot be both sides of a merge (id {ids['wec_id']})")

    par = tuple(sorted(ids.values()))
    if par in vistos:
        raise ReviewFileInvalid(f"{onde}: pair {par} appears more than once")
    vistos.add(par)

    acao = item.get("action")
    if acao is None:
        raise ReviewFileInvalid(
            f"{onde}: no action. Absence is not approval, and it is not a "
            f"valid state either — write \"{PENDING}\"."
        )
    if acao not in KNOWN_ACTIONS:
        raise ReviewFileInvalid(
            f"{onde}: unknown action {acao!r}; expected one of {KNOWN_ACTIONS}")

    # The direction is part of the decision, so it is validated whenever it
    # is present, whatever the action says.
    desde, para = item.get("merge_from"), item.get("merge_into")
    if desde is None and para is None:
        return
    if desde is None or para is None:
        raise ReviewFileInvalid(
            f"{onde}: merge_from and merge_into must be given together")
    if desde == para:
        raise ReviewFileInvalid(f"{onde}: merge_from and merge_into are the same id")
    if {desde, para} != set(ids.values()):
        raise ReviewFileInvalid(
            f"{onde}: direction {desde}->{para} does not match the pair "
            f"{ids['wec_id']}/{ids['imsa_id']}")


def is_approved(decisao: dict) -> bool:
    """
    Whether a decision carries an explicit human approval.

    Never true in this version, and it is written so on purpose: the field
    that used to mean approval was a default the script itself wrote, so no
    value of it can be read as one. A later project will define approval,
    and will have to say what evidence backs it.
    """
    return False


# ── Discovery ─────────────────────────────────────────────────

def find_merge_candidates(cur) -> list[dict]:
    """
    Pairs that *might* be one person. Reads only, decides nothing.

    The query is the one the old script used, kept so the candidate set does
    not change silently in a safety PR. It is narrow: comparing last_name
    for exact equality makes it blind to the case variants, so it finds 15
    pairs where PR #46 measured 1.079 duplicate groups. Widening it belongs
    to the consolidation project, not here.
    """
    cur.execute("""
        SELECT d1.id AS wec_id, d1.first_name AS wec_first, d1.last_name AS wec_last,
               d1.country AS wec_country, d1.imsa_driver_id AS wec_imsa,
               d2.id AS imsa_id, d2.first_name AS imsa_first, d2.last_name AS imsa_last,
               d2.country AS imsa_country, d2.imsa_driver_id AS imsa_imsa,
               d2.imsa_driver_rating::text AS rating
        FROM drivers d1
        JOIN drivers d2
            ON d1.last_name = d2.last_name
            AND LEFT(d1.first_name, 3) = LEFT(d2.first_name, 3)
            AND d1.id != d2.id
        WHERE d1.country IS NULL AND d2.country IS NOT NULL
        ORDER BY d1.last_name, d1.first_name
    """)
    return [dict(r) for r in cur.fetchall()]


def as_pending(candidato: dict) -> dict:
    """A candidate as it enters the review file: pending, and no direction."""
    return {
        "wec_id":    candidato["wec_id"],
        "wec_name":  f"{candidato['wec_first']} {candidato['wec_last']}",
        "imsa_id":   candidato["imsa_id"],
        "imsa_name": f"{candidato['imsa_first']} {candidato['imsa_last']}",
        "country":   candidato.get("imsa_country"),
        "rating":    candidato.get("rating"),
        "action":    PENDING,
        "merge_from": None,
        "merge_into": None,
        "note": "Direction and identity both undecided. See "
                "docs/data-integrity/driver-consolidation-assessment.md",
    }


def write_review_file(decisoes: list[dict], path: Path | None = None) -> None:
    """UTF-8, explicitly, on both ends — the old file was cp1252."""
    path = path or REVIEW_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(decisoes, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def merge_decisions(antigas: list[dict], candidatos: list[dict]) -> list[dict]:
    """
    Refreshes the file without inventing approval.

    A pair already in the file keeps its entry, so a human `skip` is not
    undone by a later discovery. A pair that is new enters as `pending`.
    """
    por_par = {tuple(sorted((d["wec_id"], d["imsa_id"]))): d for d in antigas}
    saida = []
    for c in candidatos:
        chave = tuple(sorted((c["wec_id"], c["imsa_id"])))
        saida.append(por_par.get(chave) or as_pending(c))
    return saida


# ── Application: blocked ──────────────────────────────────────

def apply_merges(decisoes: list[dict]) -> None:
    """
    Where the merge used to happen. It raises.

    There is no flag that reaches past this, and the statements it would
    need are not in this file any more. A future consolidation will have to
    write them again, deliberately, with the backup, the invariants and the
    survivor rule that PR #46 showed are still missing.
    """
    aprovadas = [d for d in decisoes if is_approved(d)]
    raise MergeBlocked(
        "Applying driver merges is blocked.\n"
        f"  {len(decisoes)} decisions read, {len(aprovadas)} approved.\n"
        "  No merge can run from this script: the statements that redirected\n"
        "  references and deleted rows were removed in PR #47.\n"
        "  A real consolidation needs a verified backup, a survivor rule, and\n"
        "  the evidence listed in §11 of\n"
        "  docs/data-integrity/driver-consolidation-assessment.md.\n"
        "  Nothing was written."
    )


# ── Main ─────────────────────────────────────────────────────

def run(dry_run: bool = False, apply_reviewed: bool = False,
        review: bool = False) -> int:
    print("=" * 60)
    print("OpenWEC — Driver Merge — DISCOVERY AND REVIEW ONLY")
    print("=" * 60)

    if apply_reviewed:
        try:
            decisoes = read_review_file()
        except ReviewFileInvalid as exc:
            print(f"\n  [REFUSED] {exc}")
            return 2
        try:
            apply_merges(decisoes)
        except MergeBlocked as exc:
            print(f"\n  [BLOCKED] {exc}")
            return 2
        return 2            # unreachable; apply_merges always raises

    if review:
        try:
            decisoes = read_review_file()
        except ReviewFileInvalid as exc:
            print(f"\n  [REFUSED] {exc}")
            return 2
        print(f"\n  {len(decisoes)} decisions in {REVIEW_FILE}")
        for d in decisoes:
            marca = "legacy, carries no approval" if d.get("action") == LEGACY_MERGE \
                else d.get("action")
            print(f"    [{d['wec_id']:>7}] {d['wec_name']!r}")
            print(f"       vs [{d['imsa_id']:>7}] {d['imsa_name']!r}   action={marca}")
        print(f"\n  approved: {sum(1 for d in decisoes if is_approved(d))}"
              f"  — applying is blocked in any case")
        return 0

    # discover
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        candidatos = find_merge_candidates(cur)
        cur.close()
    finally:
        conn.close()

    print(f"\n  {len(candidatos)} candidate pairs found")
    for c in candidatos:
        print(f"    [{c['wec_id']:>7}] {c['wec_first']!r} {c['wec_last']!r}")
        print(f"       vs [{c['imsa_id']:>7}] {c['imsa_first']!r} "
              f"country={c['imsa_country']} rating={c['rating']}")

    try:
        antigas = read_review_file()
    except ReviewFileInvalid as exc:
        print(f"\n  [REFUSED] {exc}")
        return 2

    atualizadas = merge_decisions(antigas, candidatos)
    if dry_run:
        print(f"\n  --dry-run: {REVIEW_FILE} not written")
    else:
        write_review_file(atualizadas)
        print(f"\n  [SAVED] {REVIEW_FILE} — {len(atualizadas)} decisions, "
              f"new ones as {PENDING!r}")

    print("\n  No database row was read for writing and none was changed.")
    print("  Applying a merge is blocked; see the module docstring.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Driver merge — discovery and review only; applying is blocked")
    parser.add_argument("--dry-run", action="store_true",
                        help="discover without rewriting the review file")
    parser.add_argument("--review", action="store_true",
                        help="report the decisions in the review file")
    parser.add_argument("--apply-reviewed", action="store_true",
                        help="refuses: applying merges is blocked (exit 2)")
    args = parser.parse_args()
    sys.exit(run(dry_run=args.dry_run, apply_reviewed=args.apply_reviewed,
                 review=args.review))
