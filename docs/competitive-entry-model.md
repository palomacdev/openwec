# Competitive entry model

Status: **additive schema only.** Migration 006 creates the tables. Nothing
reads or writes them yet, and `results`, `laps` and the analytics tables still
reference `cars` exactly as before.

This document records why the model looks the way it does, because every part of
it exists to answer a specific thing the source data does.

---

## The problem

`upsert_car` runs `ON CONFLICT DO NOTHING` with no conflict target against a
table with no unique constraint. No conflict is ever possible, so the insert
always succeeds and always mints a new `car_id`. `results` then gets a new row
too, because its `UNIQUE (session_id, car_id)` is keyed on the id that just
changed.

Reproduced in an isolated database with the loader's own functions:

```
1st load   car_id=1   result_id=1   cars=1  results=1
2nd load   car_id=2   result_id=2   cars=2  results=2
3rd load   car_id=3   result_id=3   cars=3  results=3
```

The dashboard shows this as `1, 1, 2, 2, 3, 3`: positions repeat once per load.

Fixing the loader requires knowing what identifies a car. That turned out to be
the hard part.

---

## Why `cars` cannot carry the identity

`cars` has no temporal scope — no `season_id`, no `event_id` — so one row is
"number + team + class" for all time. Measured over 3,436 classification exports
across the five series:

| Candidate key | Identities | Wrongly merges | Wrongly splits |
|---|---:|---:|---:|
| `(event, number, class)` | 17,201 | **4** | 0 |
| `(event, number, class, team)` | 17,386 | 0 | **185** |
| `(session, number, class)` | 109,862 | 0 | ~6× per event |

Neither error is acceptable. A merge attributes one team's result to another
entry — silently. A split re-creates the duplication this work exists to stop.

---

## What the data actually offers

**Within a session, `(number, class)` is unique.** Zero collisions in 3,436
files. That is the foundation.

**Across sessions of one event, it is not.** An event folder can hold more than
one entry list:

- Le Mans Cup, Monza 2018: `#3` LMP3 is DKR Engineering (Norma M30) in the
  practice sessions and United Autosports (Ligier JS P3) in the Morning and
  Afternoon sessions. Different car, different drivers.
- Le Mans 2026: the Test Day issues entry ids `107457–107518` while the race
  weekend uses `107123–107184`, for the same 62 cars.

That is what `competitions` models: the group of sessions sharing one entry
list, inside an event.

**The source identifier is evidence, not identity.** `ECM Participant Id` and
`IMSA_EventEntryId` identify an *entry list*, so one entry legitimately carries
several across an event. Coverage is also partial:

```
entry lines with an identifier        : 52.8%
race-file lines with an identifier    : 12.6%
events with full coverage             : 182 of 432
events with none                      : 111 of 432
```

It can separate competitions where present — it resolves 5 of the 160 conflicts
— but it cannot be the key.

---

## The model

```
cars         physical car. Keeps its own surrogate id. No assumption that
             (number, vehicle) is unique.
teams        team name. Unchanged.
competitions a group of sessions inside an event that share one entry list.
entries      THE COMPETITIVE ENTRY: this number, in this class, in this
             competition. What results and laps will eventually point at.
```

`entries.team_id` is an **attribute**, never part of the identity. That is what
keeps "Corvette Racing" and "Corvette Racing - GM" as one entry instead of two.

### Three uniqueness regimes

The source has three regimes, so one index would be wrong for two of them.

```sql
-- class present, identity settled
UNIQUE (competition_id, number, car_class)
    WHERE identity_status <> 'ambiguous' AND car_class IS NOT NULL;

-- class absent (pre-2013 exports), identity settled
-- NULLS NOT DISTINCT because team_id is nullable: without it a NULL team
-- compares unequal to itself and this regime protects nothing.
UNIQUE (competition_id, number, team_id) NULLS NOT DISTINCT
    WHERE identity_status <> 'ambiguous' AND car_class IS NULL;

-- ambiguous: rows must coexist, but re-import must not multiply them
UNIQUE (competition_id, number, car_class, team_id) NULLS NOT DISTINCT
    WHERE identity_status = 'ambiguous';
```

Eight events carry no `CLASS` column at all (ELMS 2011–2012, WEC 2012 Sebring,
IMSA 2017 February Test) and mix separate races in one folder. The second regime
exists for them.

### Resolution states

| State | Meaning |
|---|---|
| `resolved` | the competition came from a source entry-list identifier |
| `inferred` | grouped by heuristic; reversible |
| `ambiguous` | a conflict was found and **not** resolved — rows stay apart |

`ambiguous` is the point of the model. 155 of 17,201 groups cannot be decided
from the data. They are kept separate, marked, and exported for review. Nothing
is merged on a guess.

### Provenance

- `entry_team_history` — every team spelling ever observed, with the session it
  came from. A new spelling never silently overwrites the old one.
- `entry_source_ids` — every source identifier seen, one-to-many by design,
  because one entry carries several entry-list ids across an event.

---

## Migration strategy

Phases 1–4 are additive and reversible. Nothing after this PR is implemented.

```
F1  create competitions and entries                    ← THIS PR
F2  populate competitions per event
F3  populate entries, setting identity_status
F4  export the ambiguous groups for review             ← STOP for human decision
F5  add entry_id (nullable) to results, laps, analytics_*
F6  backfill entry_id; canonical row chosen by its links
      order: has laps > has analytics > has results > created_at
      never MIN(id)
F7  verify zero orphans and zero null entry_id
F8  NOT NULL + FK + indexes
F9  point API queries at entry_id
F10 retire cars.team_id only after F9 is stable
```

The canonical-row rule matters: `load_laps.py:205` resolves a car with
`LIMIT 1` and no `ORDER BY`, so laps attach to exactly one of the duplicate
rows. The duplicate with no laps is not the one to keep.

---

## During the transition

The loader duplicates on every run and this PR does not change that. Repairing
idempotency means choosing an identity rule, and every candidate rule either
merges entries the source proves are different or splits entries it only spells
differently — a decision that does not belong in an additive change.

So writes are blocked:

```
$ python -m database.loader.load_classification --series WEC
BLOCKED - this loader duplicates classification data on every run.
  Use --dry-run to inspect without writing, or set
  OPENWEC_ALLOW_DUPLICATING_LOADER=1 to override and accept the duplication.
$ echo $?
2
```

`--dry-run` is unaffected. The override exists so the block is a decision, not
an obstacle.

**This PR does not fix the duplication already in production.** It prepares the
identity that will, and stops the bleeding by refusing to add more.

---

## Reviewing the ambiguous groups

```
python scripts/export_ambiguous_entries.py --out ambiguous_entries.csv
```

Writes one row per team per unresolved group, with the spellings, vehicles,
source ids, drivers and sessions behind it, plus empty `decision` and
`decided_by` columns. It never guesses: driver overlap is reported as evidence
and nothing more.

```
conflicting groups       : 160
  resolved by source id  : 5
  needing human review   : 155
```

Each group carries a `group_id`: `sha256(series | season | event | number |
class)` truncated to 16 hex characters. It depends on the natural key alone, so
it survives re-export, database rebuilds and id renumbering — which is what
makes a decision file replayable:

```
python scripts/export_ambiguous_entries.py --out new.csv --decisions reviewed.csv
```

Decisions already recorded against a `group_id` are carried into the new export,
so review is incremental rather than all-or-nothing.

### Behaviour of the three regimes

Verified against the database, not assumed:

| Case | Result |
|---|---|
| Two ambiguous rows differing only by team | kept apart |
| Two ambiguous rows identical in number, class **and** team | refused — nothing in the model separates them |
| `team_id` NULL with `car_class` NULL | refused (needed `NULLS NOT DISTINCT`; the first version did not) |
| Promoting both sides of an ambiguity to `resolved` | second refused — a reviewer must merge or re-key, not flip a flag |
| Demoting `resolved` → `ambiguous` | allowed; frees the classed regime |
| Two concurrent imports of the same entry | one row, both transactions return the same id |
