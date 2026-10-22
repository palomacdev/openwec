# ADR 0001 — Identity of a competitive entry

Status: **accepted as direction.** Nothing in it is implemented, and it changes
no constraint, no data and no code. Any migration that follows needs its own
approval.

Scope of the evidence: 18,008 determined entries, 431 events, 3,240 sessions,
115,694 classification rows. Every figure below was measured read-only against
an isolated database built from the raw exports for this analysis, and against
`raw/` itself.

What this document is for: the sources disagree with each other, and sometimes
with themselves, about what names a car. Before any rule can decide that two
rows describe the same competitive entry, the disagreements have to be written
down as measurements rather than impressions. That is what section 1 does; the
rest states the two rules those measurements already rule out.

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
that race together. Section 2 measures what that costs.

**F6 — A source identifier does not identify at most one entry per
competition.** 34 identifiers name more than one entry, from four distinct
mechanisms: 15 by number spelling, 7 by evolving class, 7 where the field holds
a name, 5 where the source reassigns the identifier.

**F7 — A source identifier is not always numeric.** 60 rows carry 12 distinct
textual values, every one of them under `raw/elms/19_2024/03_IMOLA`. They hold
names: of a team (`Hans Weijs Motorsport`), of an entrant
(`Nationale Autoclub Excelsior`) and of a driver (`Robin Knutsson`, against car
#32 of NGT Racing). Textual identifiers appear in no other event folder.

**F8 — A source identifier can repeat within a single file.** At WEC Bahrain
2025 the Morning session export writes `105154` twice, on #50 and on #51 — 29
rows, 28 distinct identifiers.

**F9 — Legacy duplicate results agree.** Measured in the development database,
and stated only about it: of its 94,868 `(session, number)` groups, 85,418 hold
more than one result row and 98.4% agree on everything a reader sees. The 1,379
that disagree are dominated by team spelling; 15 disagree on position and 16 on
elapsed time, and those are groups holding two *different* entries under one
key.

F9 is a reading of one environment, not a property of the data in general. No
measurement has been taken in production or in any other instance, and none of
these proportions should be assumed to hold there.

F9 also describes the legacy tables as they stand: nothing has been
deduplicated. `DATA_POLICY.md` documents that duplication and how to read
around it.

---

## 2. Counterexamples of coexistence

Normalising car numbers — treating `#01` and `#1` as one — would merge 116
pairs of entries that appear in the same session. By F1, sharing a session
makes them different cars.

```
IMSA  #01 DPi   Cadillac Chip Ganassi   VS  #1 GTD   Paul Miller Racing   in Practice 2
IMSA  #7  PC    Starworks Motorsport    VS  #007 GTD TRG-AMR              in the Race
ELMS  #8  G3/P1 BBM Sport               VS  #008 G3/P1 CGA Race Eng.      in Free Practice
```

Of those 116 pairs: **0 share a literal number and 0 share a team.** By series:
IMSA 96, WEC 19, ELMS 1.

---

## 3. Decision: source identifiers are stored as the source wrote them

**D3 — A source identifier is stored as the source wrote it.**

This is the rule in force. The 60 textual values of `ECM Participant Id` at
Imola 2024 (F7) are kept as values, like every other identifier: surrounding
whitespace is stripped and nothing else is done to them — no case folding, no
coercion to a numeric type, no rewriting into another column.

They are **not** converted to `entrant_name`: the field holds team names,
entrant names and at least one driver name, and telling them apart needs the
context of the rows that carry them, not a column rename.

**What is not implemented.** Telling those kinds apart would have to happen
when the value is read, by whatever consumes it, and no such interpretation
exists. A reader that needs the distinction has to derive it from the
surrounding rows itself.

One caveat for anyone reading `entry_source_ids`: the population code assigns
`source_system` by whether the stored value is all digits, so a textual value
is labelled `imsa` whatever its origin. In the population built for this
analysis that mislabels all 60 textual rows, which are ELMS data. The column
records the shape of the value, not the series it came from. This is recorded
here, not corrected: changing it is outside what this ADR decides, and it is
not the semantic interpretation described above.

---

## 4. Rules this ADR refuses

Rules that are refused on evidence, not on preference. Each has a measured
counterexample.

**P1 — Normalising car numbers globally.** Refuted by F5 and the 116
counterexamples: it merges cars that race together, 96 of them in IMSA alone.

**P6 — Treating team name as identity.** The model has held this from the
start; the 1,062 legacy groups that differ only in team spelling are why — the
largest single category of disagreement in the development database, out of
1,379 disagreeing groups in all. The breakdown is under
[*Legacy result groups that disagree*](../competitive-entry-model.md#legacy-result-groups-that-disagree).

---

## 5. What this ADR does not decide

It records what the source data does, and two rules that the measurements
already rule out. It does not decide:

- whether any existing entries are consolidated, by what rule, or by whom;
- whether `car_class` keeps its present role in the model;
- any change to a constraint, an index or a stored row.

No entry has been merged, split or deduplicated on the strength of this
document, and none may be without a separate decision carrying its own
evidence.
