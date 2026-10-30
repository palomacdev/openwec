# ADR 0003 — Session scope for resolving a car row

Status: **accepted and implemented** by the change this document accompanies.
It alters no column, no constraint and no stored row. It changes one lookup.

Related: [ADR 0001](0001-entry-identity.md), which this does not contradict —
section 4 says why.

---

## 1. The question

`cars` has no session column, and nothing here adds one. The question is
narrower: when the loader reads one classification line, **which `cars` row is
that line about?**

Until now the answer was "the oldest row matching the eight columns of
`CAR_COLUMNS`". The eight columns describe a car. They do not identify an
entry.

---

## 2. Observed facts

Measured read-only against the development corpus. Each figure is
reproducible.

**G1 — An identity tuple is not unique to a session.** 645,401 `cars` rows
carry 4,458 distinct tuples. **4,148 of the 4,458** are carried by entries of
more than one session; the most widely shared is carried by 290. `#88` on a
Porsche 911 RSR, entered by the same team, answers for a 2015 race and a 2019
race alike.

**G2 — The invariant that an unscoped lookup can break is a join, not an
attribute.** `results` is `UNIQUE (session_id, car_id)` and `laps` is
`UNIQUE (session_id, car_id, lap_number)`. Of the (session, car) pairs that
carry at least one lap, **58,359 of 58,359** have a result on the same pair.
A lookup that crosses the session writes the published result against a car
this session's laps do not know.

**G3 — The lowest id is not always the row that carries the laps.** In **68**
(session, identity) groups the lap carrier is not `min(id)`. Preferring the
lowest id in those groups would separate the result from the laps.

**G4 — Some groups cannot be resolved by any rule.** **687** (session,
identity) groups have two or more rows that each carry laps of that session,
spread over **343** of the 2,902 sessions that have a classification. They are
residue of a loader that minted one car per classification line, fixed before
this change.

---

## 3. The decision

Resolution of a car row is scoped to the session, by the one predicate
`car_session_scope`:

> A car belongs to a session when that session points at it — by a result or
> by a lap — and by nothing else.

Among the candidates that remain:

| situation | resolution |
|---|---|
| one carries this session's laps | that one, whatever its id (G3) |
| none carries laps | the lowest id: a canonical representative among rows proven identical in every column the loader writes |
| two or more carry laps | **refused** (G4) — `CarIdentityAmbiguous` |

The refusal is the point of the third row. Choosing between two lap carriers
would put the published result on one and leave the other's laps answering to
nothing, which is exactly the association the scope exists to protect.

The lookup and the insert are serialised per `(session, identity)` by an
advisory lock held to the end of the transaction. `normalize_teams` takes the
same lock through the same function, because merging two teams rewrites
`team_id`, which is one of the eight columns: a merge changes identities that
an ingestion may be resolving at that moment.

**The ingestion may wait; the merge may not.** An ingestion takes one lock at
a time, in the order of the file it is reading, and it cannot sort what it
has not read yet. A merge takes several in one transaction, and can only
order them by key. Two orders that do not know each other are a cycle, and
`MEASURED`: PostgreSQL reports `deadlock detected` on two such pairs — one
between two advisory locks, one between an advisory lock and the row lock the
`cars.team_id` foreign key takes on `teams`. The second aborted the
*ingestion*. So the merge acquires everything without waiting —
`pg_try_advisory_xact_lock` and `FOR UPDATE NOWAIT` — and refuses when it
cannot. A merge that cannot see a stable state is a merge that does not
happen.

---

## 4. What this does not decide

ADR 0001 **F2** records that a session is not the scope of a competitive
entry, and that stands. This ADR is not about what an entry is; it is about
which stored row an ingested line refers to. An entry may span sessions and
still be written as one `cars` row per session, which is what the corpus
already contains.

This ADR also does not decide:

- that any existing `cars` row is a duplicate of another, or that any may be
  merged or removed — the ~640,000 excess rows in the corpus are untouched,
  and `resolve_car_id` is unchanged;
- what to do about the 687 groups of G4, beyond refusing to add to them;
- any constraint, index or migration. A `UNIQUE` on the identity tuple is
  still not possible and is still not proposed.
