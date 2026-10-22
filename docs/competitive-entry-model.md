# Competitive entry model

Status: **additive schema, populated in isolation only.** Migrations 006-009
create the tables and `database/populate_competitions.py` fills them from the
raw exports. `results`, `laps` and the analytics tables still reference `cars`
exactly as before, and nothing in the API or the dashboard reads any of this
yet.

The population has only ever been run against a disposable database. The
script refuses to start otherwise.

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

---

## Resolution, identity and matching (007-009)

006 could say whether an *entry* was resolved. It could not say whether the
**competition** grouping itself was proven, nor which sessions belonged to
which competition — so a session whose competition was unknown had nowhere to
exist and would have to be assigned artificially to satisfy a foreign key.

### What each migration adds

```
007  competitions.resolution (resolved | inferred | provisional)
     session_competition_candidates — five independently recorded criteria
     session_competitions — N:N, with 'unresolved' as a first-class state
008  entries.event_id NOT NULL, entries.competition_id nullable
     composite FK (competition_id, event_id) -> competitions (id, event_id)
     six uniqueness regimes instead of three
009  competitions.identity_uid, evidence_fingerprint, display_name
     competition_anchors — exclusive vs shared, current vs superseded
     competition_match_reviews — what was NOT decided automatically
```

### Four things a content hash was conflating

| | | |
|---|---|---|
| `identity_uid` | what the competition **is** | minted once, never recomputed |
| `evidence_fingerprint` | what has been **observed** | changes freely with the source |
| `display_name` | what it is **called** | human-facing, no constraint |
| `competition_anchors` | how a discovery is **matched** | the only thing matching reads |

A content hash fails as identity precisely because the content is meant to
change: adding the Thursday practice session does not create a different
championship, but it does change the hash.

### Exclusive and shared evidence

An entry-list identifier belongs to one list, so it identifies on its own. A
session does not: Monza 2018 runs two championships in the same Morning and
Afternoon sessions. An entry, as `(number, team)`, does not either: Le Mans
fields the same cars in the Test Day list and the race list.

Only exclusive anchors are unique per event, and only while `current`. An
identifier the source has since corrected away becomes `superseded`: the row
survives as provenance, and stops being able to confirm a match.

Matching by shared evidence alone requires two things, not one — containment of
the session sets **and** agreement of the entry population. Containment alone
would match a support series that runs only in Free Practice 1 to the main
competition, because `{FP1} ⊆ {FP1, FP2, Race}`.

### Four levels of association

| Level | Objective criterion | Result |
|---|---|---|
| direct | the session carries identifiers owned by the competition | `resolved`, no timestamp required |
| eligible | no identifiers, but the session sits inside the competition's time window **or** its `(number, team)` population | `inferred`, only if it is the single eligible candidate |
| compatible | classes and numbers agree, nothing contradicts, neither time nor population places it | never assigned |
| indeterminate | too little evidence | never assigned |

Class and number agreement alone never confirms a competition: they say the
fields look alike, not that they are the same field.

### What the population produced

Measured over the whole raw tree, in a disposable database:

```
eventos                      431      sessoes                     3240
competicoes resolved         426      competicoes provisional      165
associacoes resolved        2855      inferred                     403
associacoes unresolved       291      sessoes com >1 competicao    285
inscricoes determinadas    18008      indeterminadas               161
hipoteses de fusao pendentes  77      aplicadas automaticamente      0
```

Reconciliation closes exactly:

```
17201 identidades (evento, numero, classe)
+ 829 presentes em mais de uma competicao do mesmo evento   (32 eventos)
+ 139 do regime sem classe, chaveado por (numero, equipe)   (21 eventos)
= 18169 = 18008 determinadas + 161 indeterminadas
```

### The 77 merge hypotheses

One identifier group confined to another's sessions, fielding no class of its
own, is what an entry list with absentees looks like — and also what a second
championship confined to shared sessions looks like. None is applied.

```
python scripts/export_merge_hypotheses.py --out merge_hypotheses.csv
```

| Priority | Independent corroboration | n |
|---|---|---:|
| A | the same entry in both groups under different identifiers | 1 |
| B | no shared number, teams of the smaller contained in the larger | 20 |
| C | no shared number, teams partly in common | 24 |
| D | a number shared by **different** teams — tells against | 0 |
| E | nothing but the subset heuristic | 32 |

A-E is a priority of investigation, never a verdict. Decisions are carried
between exports by `hypothesis_id` while `evidence_digest` is unchanged; a
decision recorded against different evidence comes back marked stale rather
than being reapplied.

Leaving them unapplied costs 173 session associations: with them, unresolved
sessions fall from 291 to 118. The entry counts barely move.

### No identifier distance, anywhere

Lists come from the structure of which sessions each identifier appears in.
Identifier values are only ever compared for equality. Replacing every
identifier with `"Z" + sha256(id)[:12]` leaves the partition byte-identical:

```
normal       b652dcc12b5dcbbdab40f39c28c3a303934e8d095182dcdca959dd2279482323
reetiquetado b652dcc12b5dcbbdab40f39c28c3a303934e8d095182dcdca959dd2279482323
```

### Continuity: when a shared identifier carries identity forward

An entry-list identifier found in both a discovery and an existing competition
is where the question starts, not where it ends. The source can move an
identifier from one list to another, and a bare intersection cannot tell that
apart from a list that simply grew or shrank.

This was a real defect, found by testing before anything was committed. A
competition carrying one reassigned identifier inherited the `identity_uid` of
another, although its sessions and entry population were entirely different —
a silent merge and a silent identity loss in the same operation:

```
A: ids={900,901} sessions={S1} population=#1 ALPHA, #2 BETA
B: ids={900,902} sessions={S9} population=#9 OMEGA, #8 SIGMA
   -> B silently became A
```

Five things are now weighed separately, and the identifier intersection is only
the first of them:

1. exclusive identifiers kept, lost and gained, and whether one set contains the other
2. continuity of the entry population, by `(number, class)` and team
3. agreement of the sessions
4. a reassignment signal — identifiers both lost **and** gained
5. absence of conflicting claims from other discoveries

**Material contradiction blocks reuse, whatever else agrees.** Corroboration
has to come from something other than the identifiers, and absence of evidence
is neither corroboration nor contradiction:

```
population disjoint, or sessions disjoint   -> contraditoria  -> review
population agrees, or sessions agree        -> corroborada    -> reuse + record evidence
neither                                     -> insuficiente   -> review
```

A contradictory match writes nothing at all: the existing competition keeps its
`identity_uid`, `evidence_fingerprint`, `evidence`, anchors and provenance
untouched until a person decides.

Population continuity has three states rather than two, and the third matters.
A team spelling change — "Corvette Racing" becoming "Corvette Racing - GM" —
has the same shape in the data as a replacement, and this model has held from
the start that a team name is an attribute of an entry and never its identity.
So it decides nothing by itself:

```
FALSE  the fields are disjoint on (number, class)          -> a different field
TRUE   they meet and some shared entry carries the same team
None   they meet but every shared number is spelled differently
```

Every reuse records what corroborated it, in `competitions.evidence`:

```
continuidade corroborada: identificadores mantidos=2, perdidos=0, novos=1;
contencao=sim; populacao=sim; sessoes=sim; reatribuicao=nao
```

On the current corpus this rule changes no decision: no identifier has ever
been reassigned there, so `ambiguous_match` reviews and superseded anchors are
both zero across the 431 real events. Verified by comparing competitions,
anchors, associations, entries, candidates and provenance against a population
built with the previous rule — byte-identical on every projection.

---

## Resolving a classification row onto an entry

A classification row says "car #51, LMGT3, Ferrari AF Corse, position 3". None
of that names an entry. `number` is unique within a session — measured, zero
collisions across 3,240 sessions — but a session is not the scope of an entry:
Monza 2018 runs two championships in the same Morning session, and one number
in two competitions of an event is two entries.

The legacy data shows what treating `(session_id, number)` as an identity
costs. At Imola 2025 the session row named "Race 1" holds #115 as GPA RACING
over 12 laps and as DC RACING over 8 laps. Two entries, one key.

### Six outcomes, and only two of them resolve

```
resolved_by_source_id          the source printed an entry-list identifier and
                               it names an entry of a competition this session
                               runs, agreeing on number and class
resolved_by_sole_competition   the session runs exactly one competition
multiple_candidates            several competitions, nothing in the row places it
source_id_out_of_scope         the identifier is owned by a competition of this
                               event that this session does not run
contradictory_evidence         the identifier names an entry this row does not
                               describe, or names several at once
entry_not_found                competition known, no entry with this number and class
no_association                 the session belongs to no competition at all
```

An identifier that places nothing is not one thing. It is three, and they are
kept apart because only the middle one is a warning:

```
ausente        the row carries none -> the sole competition decides
fora do escopo owned elsewhere in this event -> the row likely belongs there,
               so nothing is decided here
desconhecido   the event has never seen it -> no evidence; the sole competition
               decides, and the evidence records that it went unrecognised
```

Measured over the corpus, all 39,062 rows resolved by sole competition carry no
identifier at all: `source_id_out_of_scope` is zero and the distinction costs
nothing in coverage today. It exists because without it that path resolves
silently when the case does arise — after the pending merge hypotheses are
decided, or when new data lands.

Absence of association, several candidates and contradictory evidence are kept
apart on purpose. Only one of them is a defect, and collapsing them would hide
which.

Nothing falls back to choosing among candidates. A multiplicity is an outcome,
never a tie broken quietly: the adapter contains no `LIMIT 1` and no `MIN(id)`,
and a test parses its SQL literals to guard that. The test reads the source, so
it guards against the change being made carelessly rather than proving that no
such query can exist.

### Measured coverage

```
linhas de classificacao            : 115694
  resolved_by_source_id              60873   52.6%
  resolved_by_sole_competition       39062   33.8%
  multiple_candidates                    9    0.0%
  source_id_out_of_scope                 0    0.0%
  contradictory_evidence               189    0.2%
  entry_not_found                        0    0.0%
  no_association                     15561   13.5%

  RESOLVIDAS                         99935   86.4%
```

The 15,561 unassociated rows are the sessions still waiting on the 77 pending
merge hypotheses, dominated by the hourly snapshots at Le Mans. None of those
hypotheses is applied here.

### One identifier, several entries

34 entry-list identifiers name more than one entry of the same competition, and
not for one reason:

```
15  the same car number written #03 and #3
12  genuinely different numbers — at Bahrain 2025, 105154 names both #50 and #51
 7  the same number recorded under two classes
```

They account for the 189 contradictory rows. The resolver refuses them; it does
not normalise `#03` into `#3`, because masking a defect in the stored identity
would hide it from the report that has to surface it.

### Legacy result groups that disagree

Of 94,868 `(session, number)` groups in the development database, 85,418 hold
more than one result row and 98.4% of those agree on everything a reader sees.
The disagreements:

```
1062  equipe                        144  voltas
  55  voltas+equipe                  55  voltas+classe+equipe+veiculo
  18  voltas+classe+equipe           13  voltas+equipe+veiculo
   8  posicao+voltas+tempo+equipe     7  posicao+voltas+tempo+classe+equipe
   6  classe+equipe                   6  voltas+classe
   2  voltas+classe+veiculo           1  equipe+veiculo
   1  classe+equipe+veiculo           1  voltas+tempo+classe+equipe
```

1,379 in total, dominated by team spelling — "KROHN RACING" against "Krohn
Racing" — which the model has always treated as an attribute rather than an
identity.

Sixteen groups disagree on finishing position or elapsed time.
`scripts/export_legacy_divergences.py` writes every copy of those with its
provenance, choosing nothing. Read them before concluding anything: in every
case examined the copies describe *different entries* sharing one key, not one
entry recorded twice with different outcomes.

A note on counting them: `count(DISTINCT x)` returns 0 when every value is
NULL, so a test for disagreement must be `> 1`. Written as `= 1` it silently
drops the all-NULL groups, which is how an earlier count of these came out
wrong.

---

## Observational history (010)

Migration 008 keys an entry on `(competition, number, class)`. The audit of the
34 shared source identifiers found four mechanisms behind them, and two are
consequences of that key rather than defects of the source: seven entries whose
class is refined mid-event, and fifteen at Monza 2018 whose number is written
padded in two files.

So class and number are recorded as things **observed**, per source row. What
to do about the entries that split is not decided by this migration, and the
key is untouched.

### The grain is the raw observation

One row per entry, per source file, per version of that file, per line — not
one row per entry per session. The source publishes a session several times:

```
arquivos por estagio: Provisional 65, Unofficial 78, Official 61
sessoes com 2 arquivos: 66   com 3: 56   com 4: 1
sessoes onde dois arquivos sao byte-identicos: 69
```

Keying on the digest alone would collapse a byte-identical Provisional and
Official into one row. No value would be lost — the bytes are equal — but the
fact that the source published both would be, and the revision stage lives in
the file name.

```sql
UNIQUE (entry_id, source_file, source_digest, source_line)
```

| Case | Preserved |
|---|---|
| several files of one session | yes, different paths |
| `Provisional` and `Official`, byte-identical | **yes** — what the digest alone lost |
| a file revised | yes, different digest |
| a revision that only reorders rows | yes, the bytes change |
| the same file ingested again | refused, idempotent |
| one event duplicated across two series | yes, different `entry_id` |
| two rows for one entry in one file | yes, different line |

`source_line` discriminates nothing today — zero repeated rows and zero
same-entry duplicates across 3,436 files — but a store of raw observations has
to be able to hold both rather than refuse the second.

### No timestamp is invented

Searched and not found: no classification file carries a date or time column
with absolute meaning, there is no metadata preamble before the header, and
nothing records when a file was published or revised. The filesystem mtime is
the local download — files from a 2025 event are dated 2026-05-19.

The weather files do carry UTC time, but they date the **session**, not the
observation of a class or a number. Using them here would invent precision the
source does not give.

```sql
source_observed_at TIMESTAMPTZ,                    -- NULL today, always
source_time_basis  source_time_basis NOT NULL DEFAULT 'none',
ingested_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),   -- the write, only

CHECK ((source_time_basis = 'none') = (source_observed_at IS NULL))
```

Only `none` occurs. `file_published` and `row_timestamp` are the shapes a
source could provide, so a future value cannot be written without declaring
where it came from.

### Evidence is not deleted implicitly

`ON DELETE RESTRICT` on both `entry_id` and `session_id`. Deleting an entry or
a session that has observations is refused; consolidating entries later has to
move the history first, as a deliberate act. Reverting the migration refuses
while either table holds rows, unless `openwec.allow_history_loss` is set in
that session.

### Indexes

No index on `(entry_id)` alone: the UNIQUE constraint already indexes it as a
leading column and a btree serves that lookup. `entry_team_history` carries
exactly that redundancy today; 010 does not repeat it.

```
UNIQUE (entry_id, source_file, source_digest, source_line)   identity and idempotency
       (session_id)                                          what a session observed
       (source_digest)                                       what a file version observed
```
