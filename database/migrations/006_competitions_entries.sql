-- 006_competitions_entries.sql
--
-- Additive only. Introduces the competitive-entry model alongside the existing
-- schema. Nothing is dropped, backfilled or re-pointed here: results, laps and
-- the analytics tables keep referencing cars exactly as before.
--
-- Three identities are kept apart on purpose:
--   cars    — the physical car. Keeps its own surrogate id; no assumption that
--             (number, vehicle) is unique.
--   teams   — the team name, unchanged.
--   entries — the competitive entry: this number, in this class, in this
--             competition. This is what results and laps will eventually point
--             at, in a later PR.
--
-- competitions exist because an event folder can hold more than one entry list
-- (Monza 2018 Le Mans Cup ran two; Le Mans 2026 issues a separate list for the
-- Test Day). Grouping only by event would merge entries that are not the same.

BEGIN;

-- ── Resolution state ──────────────────────────────────────────────────────
-- resolved  : the competition came from a source entry-list identifier
-- inferred  : grouped by heuristic (event + session set); reversible
-- ambiguous : a conflict was detected and NOT resolved. The rows stay apart.
--             Never collapse these automatically.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'entry_identity_status') THEN
        CREATE TYPE entry_identity_status AS ENUM ('resolved', 'inferred', 'ambiguous');
    END IF;
END $$;


-- ── competitions ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS competitions (
    id              SERIAL PRIMARY KEY,
    event_id        INT NOT NULL REFERENCES events(id),
    label           VARCHAR(80) NOT NULL,   -- "main", "test-day", "support-1"
    source_hint     TEXT,                   -- observed entry-list id range, if any
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (event_id, label)
);

CREATE INDEX IF NOT EXISTS idx_competitions_event ON competitions(event_id);


-- ── entries ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS entries (
    id               SERIAL PRIMARY KEY,
    competition_id   INT NOT NULL REFERENCES competitions(id),

    -- Attributes, not identity. team_id in particular must never be the thing
    -- that splits an entry: the same entry is routinely spelled several ways
    -- ("Corvette Racing" / "Corvette Racing - GM").
    car_id           INT REFERENCES cars(id),    -- nullable: the physical car
    team_id          INT REFERENCES teams(id),   -- most recently observed

    number           VARCHAR(10) NOT NULL,
    car_class        VARCHAR(20),                -- NULL in pre-2013 exports

    identity_status  entry_identity_status NOT NULL DEFAULT 'inferred',
    review_note      TEXT,                       -- why it is ambiguous

    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_entries_competition ON entries(competition_id);
CREATE INDEX IF NOT EXISTS idx_entries_car         ON entries(car_id);
CREATE INDEX IF NOT EXISTS idx_entries_team        ON entries(team_id);
CREATE INDEX IF NOT EXISTS idx_entries_status      ON entries(identity_status);


-- ── Uniqueness, in three regimes ──────────────────────────────────────────
-- The regimes exist because the source data has three regimes. A single index
-- would have to pick one and be wrong for the other two.

-- 1. Class present and identity settled: (competition, number, class).
--    team_id is deliberately absent, so name variants do not split an entry.
CREATE UNIQUE INDEX IF NOT EXISTS entries_ident_classed
    ON entries (competition_id, number, car_class)
    WHERE identity_status <> 'ambiguous' AND car_class IS NOT NULL;

-- 2. Class absent (pre-2013 exports) and identity settled: fall back to team.
--    Without a class there is nothing else to tell two entries apart, and
--    those events are known to mix separate races in one folder.
CREATE UNIQUE INDEX IF NOT EXISTS entries_ident_unclassed
    ON entries (competition_id, number, team_id)
    WHERE identity_status <> 'ambiguous' AND car_class IS NULL;

-- 3. Ambiguous: the rows must be allowed to coexist, but re-importing the same
--    file must not keep adding more of them. Including team_id makes the row
--    reproducible without asserting that the entries are the same.
--    NULLS NOT DISTINCT so a NULL class or team still collides with itself.
CREATE UNIQUE INDEX IF NOT EXISTS entries_ident_ambiguous
    ON entries (competition_id, number, car_class, team_id) NULLS NOT DISTINCT
    WHERE identity_status = 'ambiguous';


-- ── Provenance: team names ────────────────────────────────────────────────
-- Decision 7: a new spelling must not silently overwrite the previous one.
CREATE TABLE IF NOT EXISTS entry_team_history (
    id            SERIAL PRIMARY KEY,
    entry_id      INT NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    team_id       INT NOT NULL REFERENCES teams(id),
    session_id    INT REFERENCES sessions(id),   -- where this spelling was seen
    observed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (entry_id, team_id, session_id)
);

CREATE INDEX IF NOT EXISTS idx_entry_team_history_entry ON entry_team_history(entry_id);


-- ── Provenance: source identifiers ────────────────────────────────────────
-- Decision 5: an ECM Participant Id identifies an entry LIST, not an entry.
-- The same entry legitimately carries several across an event, so this is a
-- one-to-many record of evidence, never a key.
CREATE TABLE IF NOT EXISTS entry_source_ids (
    id               SERIAL PRIMARY KEY,
    entry_id         INT NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    source_system    VARCHAR(20) NOT NULL,       -- 'ecm' | 'imsa'
    source_entry_id  VARCHAR(40) NOT NULL,
    session_id       INT REFERENCES sessions(id),
    observed_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (entry_id, source_system, source_entry_id, session_id)
);

CREATE INDEX IF NOT EXISTS idx_entry_source_ids_entry  ON entry_source_ids(entry_id);
CREATE INDEX IF NOT EXISTS idx_entry_source_ids_lookup ON entry_source_ids(source_system, source_entry_id);

COMMIT;
