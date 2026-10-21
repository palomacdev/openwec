# ADR 0001 — Identity of a competitive entry

Status: **accepted as direction.** Nothing in it is implemented, and it changes
no constraint, no data and no code. The migration that would follow needs its
own approval against the criteria at the end.

Scope of the evidence: 18,008 determined entries, 431 events, 3,240 sessions,
115,694 classification rows. Every figure below was measured read-only against
a disposable database populated from the raw exports, and against `raw/` itself.

---

## 1. Observed facts

Facts, not readings of them. Each is a measurement that can be reproduced.

**F1 — A car number is unique within a session.** Zero collisions across 3,240
sessions, and zero repetitions of `(number, class)` within any single
classification file out of 3,436.

**F2 — A session is not the scope of an entry.** Monza 2018 runs two
championships in the same Morning and Afternoon sessions. 285 sessions belong
to more than one competition.

**F3 — `car_class` is not stable for one entry within a competition.** Seven
entries carry the same source identifier, number and team while their class
changes between sessions:

```
ELMS Aragon 2023   #23 United Autosports  id 75172
   LMP2         in Bronze Test, FP1, FP2, Qualifying
   LMP2 PRO/AM  in Morning Test, Afternoon Test

Le Mans Cup 2022   #24 H24 Racing  id 62736
   LMP3  in Free Practice 1
   INN   in FP2, Qualifying 1 and 2, Race 1 and 2

ELMS Monza 2022    #17 ARCTIC ENERGY
   class S  in Qualifying Practice 1, Race 1, Race 3
   class J  in Qualifying Practice 2, Race 2, Race 4
```

**F4 — The textual form of a number varies within one competition, in one
event.** At Monza 2018, fifteen entries appear both padded and unpadded. The
padding tracks which of the two merged entry lists the row came from, not the
car: of the 50 rows in the Morning Session, the 16 padded ones all carry
identifiers 29660–29686 (Le Mans Cup) and the 34 unpadded ones all carry
29745–29786 (ELMS).

**F5 — A leading zero is semantic in IMSA.** `#01` and `#1` are different cars
that race together. This is covered in §3.

**F6 — A source identifier does not identify at most one entry per
competition.** 34 identifiers name more than one entry, from four distinct
mechanisms: 15 by number spelling, 7 by evolving class, 7 where the field holds
a name, 5 where the source reassigns the identifier.

**F7 — A source identifier is not always numeric.** 60 of 61,062 identified
rows (0.10%), all in `ELMS/19_2024/03_IMOLA`, hold names: of a team
(`Hans Weijs Motorsport`), of an entrant (`Nationale Autoclub Excelsior`) and
of a driver (`Robin Knutsson`, against car #32 of NGT Racing).

**F8 — A source identifier can repeat within a single file.** At WEC Bahrain
2025 the Morning session export writes `105154` twice, on #50 and on #51 — 29
rows, 28 distinct identifiers.

**F9 — Legacy duplicate results agree.** Of 94,868 `(session, number)` groups,
85,418 hold more than one result row and 98.4% agree on everything a reader
sees. The 1,379 that disagree are dominated by team spelling; 15 disagree on
position and 16 on elapsed time, and those are groups holding two *different*
entries under one key.

---

## 2. Candidate keys

Measured over the 18,008 determined entries. A **collision** is a group the key
would merge that is separate today. **Coexist** means the entries the key would
merge appear in the same session, which by F1 makes them different cars.

| Key | Collisions | Coexist | Corroborated by identifier | Same team, no identifier | Different teams |
|---|---:|---:|---:|---:|---:|
| **K1** `(competition, number)` | 41 | **0** | 7 | 33 | 1 |
| **K2** `(competition, normalised number)` | 172 | **116** | 22 | 33 | 1 |
| **K3** `(competition, number, class)` — current | 0 | 0 | — | — | — |
| **K4** `(competition, normalised number, class)` | 37 | **22** | 15 | — | — |
| **K5** `(competition, normalised number, team)` | 55 | **0** | 22 | 33 | — |

K3 does not collide because it is the unique index of migration 008. It splits
instead: the seven entries of F3 and the fifteen of F4.

K1's single "different teams" group dissolves on inspection into two artefacts:
`#102/TBA` against `#102/PRIV` — placeholders, not teams — and
`#99/ASTON MARTIN RACING V8` against `#99/Aston Martin Racing V8`, a case
variant with a class change.

### Counterexamples of coexistence

The 116 pairs that K2 would merge while they share a session:

```
IMSA  #01 DPi   Cadillac Chip Ganassi   VS  #1 GTD   Paul Miller Racing   in Practice 2
IMSA  #7  PC    Starworks Motorsport    VS  #007 GTD TRG-AMR              in the Race
ELMS  #8  G3/P1 BBM Sport               VS  #008 G3/P1 CGA Race Eng.      in Free Practice
```

Of those 116 pairs: **0 share a literal number and 0 share a team.** By series:
IMSA 96, WEC 19, ELMS 1.

### Absence of coexistence does not prove identity

K1 and K5 show zero coexistence. That is evidence they do not merge cars the
source shows racing together — it is **not** evidence that what they merge is
the same entry.

Two entries can fail to coexist for reasons that have nothing to do with
identity: one withdrew before the race, one only ran the test day, the export
for one session omitted a class. WEC Bahrain is the case in point. Identifier
`105149` names #33 in the practice sessions and #81 in the Morning session, same
team, and the two never appear together in a session that lists both — yet they
are plainly different cars.

Any rule that reads "they never raced together, therefore they are the same"
is rejected. Coexistence refutes identity; its absence establishes nothing.

---

## 3. Approved architectural decisions

**D1 — `car_class` ceases to be treated as an immutable identity attribute in
the target model.**

Grounded in F3, and in the absence of any counterexample: searching for two
genuinely distinct entries sharing a literal number within one competition, the
corpus produces none — all 41 K1 collisions have zero coexistence.

This decision **does not authorise** changing the current constraint, and
**does not authorise** consolidating the 41 colliding entries. It states the
direction; §6 states what must be true before anything changes.

**D2 — Monza 2018 receives local treatment grounded in provenance. There will
be no global normalisation of numbers.**

The 116 coexisting groups of §2 demonstrate the risk. Whatever is decided about
`#03` and `#3` at Monza applies to that event, on the evidence of that event —
that the padding tracks the source list, and that the fifteen cases share
identifier, class and team with disjoint sessions.

**D3 — The 60 textual values of `ECM Participant Id` at Imola 2024 are
preserved in their raw form and interpreted semantically at read time.**

They are not converted to `entrant_name` indiscriminately: the field holds team
names, entrant names and at least one driver name, and telling them apart needs
the context of the rows that carry them, not a column rename. The stored value
stays exactly as the source wrote it.

**D4 — Observational history for class and number; claims and auditable
decisions for identity consolidation.**

Class and number become things observed per session and recorded, rather than
components of a key that silently splits an entry when they change. Two entries
only become one through a recorded decision carrying its evidence, in the same
shape as `competition_anchors` and `competition_match_reviews`.

---

## 4. Hypotheses not yet established

Stated as open, so that nothing downstream treats them as settled.

**H1 — That `#03` and `#3` at Monza 2018 are the same entry.** The evidence
points one way in all fifteen cases: shared identifier, same class, same team,
disjoint sessions, and padding explained by the source list rather than by the
car. No evidence points the other way. That is not the same as proof, and F5
shows the opposite holds elsewhere.

**H2 — That the 41 K1 collisions are 41 duplications.** Zero coexistence is
consistent with that reading and does not establish it (§2).

**H3 — That a literal number identifies an entry within a competition in
general.** It holds across the measured corpus. It is a property of the data
observed, not a rule the sources promise, and a series that reassigns a number
mid-event would break it.

**H4 — That the five reassigned identifiers at WEC Bahrain are source defects
rather than a convention we have not understood.** The within-file duplicate of
F8 makes "defect" the natural reading, but the convention of that export has not
been confirmed against any specification.

---

## 5. Explicitly forbidden rules

Rules that are refused on evidence, not on preference. Each has a measured
counterexample.

**P1 — Normalising car numbers globally.** Refuted by F5 and the 116
counterexamples: it merges cars that race together, 96 of them in IMSA alone.

**P2 — Concluding identity from absence of coexistence.** Refuted in §2.

**P3 — Discarding non-numeric identifiers as malformed.** Refuted by F7: they
are well-formed values of a different kind, and carry information.

**P4 — Choosing between candidate entries by `MIN(id)`, by `LIMIT 1`, or by
any arbitrary ordering.** Already the rule of the resolver merged in PR #33 and
restated here as architecture.

**P5 — Merging two entries without a recorded decision carrying its evidence.**
Follows from D4.

**P6 — Treating team name as identity.** The model has held this from the
start; the 1,062 legacy groups that differ only in team spelling are why.

---

## 6. Potential impact on the PR #33 resolver

The resolver reads `(competition, number, class)` today, through the index of
migration 008.

| | If D1 is implemented | Under D4 alone |
|---|---|---|
| `_entry_of()` looks up by | `(number)` | claims, settled by recorded decision |
| `entry_not_found` | less frequent | unchanged |
| `contradictory_evidence` (189 today) | falls to near zero for the class cases | unchanged, with finer reasons |
| Coverage, 86.4% today | rises, by consolidating the 41 | stable at first |
| `test_classe_diferente_nao_resolve_para_outra_inscricao` | **asserts the opposite of the new model** and must be inverted deliberately | stays valid |
| "nothing chosen in silence" | preserved | reinforced |

The test that would have to invert is the signal to watch. It is not a fragile
test: it encodes the current semantics correctly, and changing it means the
semantics changed. Any PR that quietly rewrites it is doing something this ADR
did not authorise.

---

## 7. Criteria required before changing any constraint

All of them, measured and presented, before a migration is written.

**C1 — The observational histories exist and are populated.** Class and number
per session, in a disposable database, reconciled against `raw/` with zero
divergence between what the discovery found and what was persisted.

**C2 — The consolidation is measured before it is performed.** The exact set of
entries a key change would merge, enumerated with evidence per case, reviewed
as a list rather than as a count.

**C3 — Every merge carries a recorded decision.** No entry is consolidated by
the migration itself.

**C4 — Provenance survives.** `entry_source_ids` and `entry_team_history` rows
of a consolidated entry are preserved, not deleted; consolidation adds history
rather than replacing it.

**C5 — The resolver's coverage is measured before and after** against the same
corpus, and every row whose outcome changes is accounted for by name.

**C6 — Legacy tables are untouched.** `results`, `laps`, `result_drivers`,
`cars` and `analytics_car_session` keep every row, and no new constraint is
added to them.

**C7 — Idempotency is demonstrated.** Two full runs produce identical state,
compared on projections independent of row ids, as PR #32 established.

**C8 — The reverse migration is written and exercised,** and refuses rather
than destroying identity when identity exists.

---

## 8. What this ADR does not decide

- Whether `#03` and `#3` at Monza 2018 are consolidated, and by whom.
- Whether the 41 K1 collisions are consolidated.
- The shape of the claims and decisions tables.
- Whether `entries.car_class` is dropped, kept as last observation, or kept
  as-is with history alongside.
- Anything about the 77 pending merge hypotheses, which remain frozen.
