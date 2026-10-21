-- 007_competition_resolution.sql
--
-- Additive. Completes the entry model with the two resolution states migration
-- 006 could not express, and with the candidate evidence behind an inferred
-- assignment.
--
-- 006 could record whether an ENTRY was resolved. It could not record:
--   * whether the COMPETITION grouping itself was proven, inferred or merely
--     operational, and
--   * which sessions belong to which competition — there was no association at
--     all, so a session whose competition is unknown had nowhere to exist and
--     would have to be assigned artificially just to satisfy a foreign key.
--
-- Cardinality is N:N by evidence, not by preference. Measured at Monza 2018
-- (Le Mans Cup, round 2): the Morning and Afternoon sessions each carry 50
-- entries, 49 of them with an entry-list identifier, drawn from two lists —
--   27 identifiers, 29659..29687, GT3 + LMP3, also observed on their own in
--      Free Practice 1 and 2 and in both qualifying sessions
--   34 identifiers, 29745..29786, LMP2 + LMGTE + LMP3, never observed on their
--      own anywhere in this event
-- Two championships sharing track time, and the combined sessions belong to
-- both. A unique session_id would make that unrepresentable.
--
-- Note what this does NOT say: that identifier connectivity separates the two.
-- It does not. Because the combined sessions contain both lists, following
-- shared identifiers from session to session merges them into one group. The
-- split is recovered from which sessions each identifier appears in, never
-- from how far apart the identifiers are numerically.
--
-- The 50th entry of those sessions — car #065, Graff, LMP3 — carries no
-- identifier at all, so no competition can be determined for it. Migration 008
-- is what gives it somewhere to exist without inventing one.

BEGIN;

-- ── Resolution of the competitive grouping ────────────────────────────────
-- resolved    : the grouping rests on entry-list identifiers present in the
--               source, and on the structure of which sessions each identifier
--               appears in.
--
--               It is NOT enough that one car number carries two different
--               identifiers across sessions. Measured across the corpus, six
--               entries do exactly that inside a single list — #51 HYPERCAR at
--               Bahrain 2025 is recorded as 105154 in one session and 105155 in
--               another, both well inside the event's only list. Treating that
--               as proof of a second competition invents one.
-- inferred    : derived from identifiers present in the source, but nothing
--               proves the split
-- provisional : operational grouping with no proven competitive identity, for
--               events whose exports carry no identifier at all. It exists so
--               entries have somewhere to live; it does not assert history.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'competition_resolution') THEN
        CREATE TYPE competition_resolution AS ENUM ('resolved', 'inferred', 'provisional');
    END IF;
END $$;

ALTER TABLE competitions
    ADD COLUMN IF NOT EXISTS resolution competition_resolution NOT NULL DEFAULT 'inferred',
    ADD COLUMN IF NOT EXISTS evidence   TEXT;

CREATE INDEX IF NOT EXISTS idx_competitions_resolution ON competitions(resolution);


-- ── Candidate evaluation ──────────────────────────────────────────────────
-- Evaluating a candidate is not the same as deciding an association. A session
-- with two eligible candidates gets no automatic assignment at all, and is not
-- swept into a provisional competition either: it stays unresolved.
--
-- Four levels, by what the evidence actually supports:
--
--   direct        the session carries entry-list identifiers owned by the
--                 competition. Recorded as a 'resolved' association, with no
--                 candidate row and no timestamp requirement — an identifier
--                 the source itself printed needs no corroboration.
--   eligible      no identifiers, but the session sits inside the competition's
--                 observed time window, or its (number, team) population sits
--                 inside the competition's. Becomes an 'inferred' association
--                 only when it is the single eligible candidate in the event.
--   compatible    classes and numbers agree and nothing contradicts, but
--                 neither time nor population places it. Never assigned.
--   indeterminate too little evidence to say anything.
--
-- rejected is separate: at least one criterion was evaluated and failed.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'candidate_evaluation') THEN
        CREATE TYPE candidate_evaluation AS ENUM ('eligible', 'rejected', 'compatible', 'indeterminate');
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS session_competition_candidates (
    id              SERIAL PRIMARY KEY,
    session_id      INT NOT NULL REFERENCES sessions(id),
    competition_id  INT NOT NULL REFERENCES competitions(id),

    evaluation      candidate_evaluation NOT NULL,

    -- The criteria, each independently recorded.
    -- NULL means "no evidence either way" and is never read as TRUE. A
    -- criterion that could not be evaluated cannot support eligibility.
    timestamp_ok    BOOLEAN,   -- source timestamp falls in the competition's window
    classes_ok      BOOLEAN,   -- the session's classes fit the competition's
    numbers_ok      BOOLEAN,   -- the session's car numbers fit the competition's
    no_id_conflict  BOOLEAN,   -- no entry-list identifier contradicts it
    -- Agreement of the entry population itself — (number, team) pairs, not just
    -- numbers. Two championships at one circuit routinely share car numbers;
    -- they do not share the team fielding each number. This is the criterion
    -- that separates "the fields look alike" from "this is the same field",
    -- which is why classes_ok and numbers_ok alone never confer eligibility.
    population_ok   BOOLEAN,

    rationale       TEXT,      -- why this verdict, in words
    provenance      TEXT,      -- which files and fields the criteria came from
    evaluated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (session_id, competition_id),
    -- Lets another table point at a candidate *and* require its verdict.
    UNIQUE (id, evaluation)
);

CREATE INDEX IF NOT EXISTS idx_sc_candidates_session ON session_competition_candidates(session_id);
CREATE INDEX IF NOT EXISTS idx_sc_candidates_eval    ON session_competition_candidates(evaluation);


-- ── Session ↔ competition association ─────────────────────────────────────
-- resolved   : the source states it — the session carries that competition's
--              entry-list identifiers
-- inferred   : derived, and only ever from a candidate evaluated 'eligible'
-- unresolved : association pending. competition_id stays NULL.
--
-- 'unresolved' describes ONE pending association, not the state of the whole
-- session. A session may legitimately hold proven associations alongside
-- entries whose competition is still unidentified, and both are recorded.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'session_assignment_status') THEN
        CREATE TYPE session_assignment_status AS ENUM ('resolved', 'inferred', 'unresolved');
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS session_competitions (
    id                      SERIAL PRIMARY KEY,
    session_id              INT NOT NULL REFERENCES sessions(id),
    competition_id          INT REFERENCES competitions(id),   -- NULL ⇔ unresolved

    status                  session_assignment_status NOT NULL,
    evidence                TEXT,

    -- An inferred association must name the eligible candidate behind it.
    -- The composite foreign key below is what makes "a rejected candidate
    -- cannot justify an association" a structural fact rather than a
    -- convention: the referenced row has to carry evaluation = 'eligible'.
    justified_by            INT,
    justified_by_evaluation candidate_evaluation,

    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- One row per session/competition pair. NULLS NOT DISTINCT also caps a
    -- session at a single pending row.
    UNIQUE NULLS NOT DISTINCT (session_id, competition_id),

    -- Never assign a competition just to satisfy the foreign key.
    CONSTRAINT session_unresolved_has_no_competition
        CHECK ((status = 'unresolved') = (competition_id IS NULL)),

    -- Only an inferred association carries a justification; it must carry one.
    CONSTRAINT inferred_requires_candidate
        CHECK ((status = 'inferred') = (justified_by IS NOT NULL)),

    CONSTRAINT justification_is_eligible
        CHECK (justified_by_evaluation IS NULL OR justified_by_evaluation = 'eligible'),

    CONSTRAINT justification_pair_complete
        CHECK ((justified_by IS NULL) = (justified_by_evaluation IS NULL)),

    FOREIGN KEY (justified_by, justified_by_evaluation)
        REFERENCES session_competition_candidates (id, evaluation)
);

CREATE INDEX IF NOT EXISTS idx_session_competitions_session ON session_competitions(session_id);
CREATE INDEX IF NOT EXISTS idx_session_competitions_comp    ON session_competitions(competition_id);
CREATE INDEX IF NOT EXISTS idx_session_competitions_status  ON session_competitions(status);

COMMIT;
