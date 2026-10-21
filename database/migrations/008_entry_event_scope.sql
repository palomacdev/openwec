-- 008_entry_event_scope.sql
--
-- PROPOSAL — not to be applied outside an isolated database until approved.
--
-- Lets an entry exist while its competition is still undetermined, without
-- inventing a competition to hold it.
--
-- 006 made entries.competition_id NOT NULL. Measured against the raw exports,
-- 229 entries in 8 events have no determinable competition: they appear in a
-- session that belongs to more than one competition and carry no entry-list
-- identifier of their own. Monza 2018 is the clearest one — car #065 (Graff,
-- LMP3) runs the combined Morning and Afternoon sessions, which draw from both
-- the Michelin Le Mans Cup list and the ELMS list, and nothing in the source
-- says which one it entered. Under 006 the only way to store it was to pick
-- one, so the schema forced an assertion the data does not support.
--
--   competition_id IS NULL  ⇒  the entry belongs to the event, and its
--                              competition has not been determined.
--
-- It never means "no competition" and is never a placeholder competition.

BEGIN;

-- ── Event scope ───────────────────────────────────────────────────────────
-- An entry always knows its event, so dropping the competition never leaves it
-- floating. Backfilled from the competition it already had.
ALTER TABLE entries
    ADD COLUMN IF NOT EXISTS event_id INT;

UPDATE entries e
   SET event_id = c.event_id
  FROM competitions c
 WHERE c.id = e.competition_id
   AND e.event_id IS DISTINCT FROM c.event_id;

ALTER TABLE entries
    ALTER COLUMN event_id SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'entries_event_id_fkey') THEN
        ALTER TABLE entries
            ADD CONSTRAINT entries_event_id_fkey
            FOREIGN KEY (event_id) REFERENCES events(id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_entries_event ON entries(event_id);


-- ── The competition becomes optional, but never inconsistent ──────────────
ALTER TABLE entries
    ALTER COLUMN competition_id DROP NOT NULL;

-- Target for the composite foreign key below.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'competitions_id_event_key') THEN
        ALTER TABLE competitions
            ADD CONSTRAINT competitions_id_event_key UNIQUE (id, event_id);
    END IF;
END $$;

-- A competition assigned to an entry must belong to that entry's event. The
-- single-column FK from 006 could not express this: it allowed an entry in
-- event A to point at a competition of event B. MATCH SIMPLE is deliberate —
-- when competition_id is NULL the pair is not checked, which is exactly the
-- undetermined case, while event_id stays NOT NULL on its own.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'entries_competition_id_fkey') THEN
        ALTER TABLE entries DROP CONSTRAINT entries_competition_id_fkey;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'entries_competition_event_fkey') THEN
        ALTER TABLE entries
            ADD CONSTRAINT entries_competition_event_fkey
            FOREIGN KEY (competition_id, event_id)
            REFERENCES competitions (id, event_id);
    END IF;
END $$;


-- ── Uniqueness, in six regimes ────────────────────────────────────────────
-- 006 had three. Each now splits in two, because the scope of an identity is
-- the competition when one is known and the event when none is.
--
-- The scope must change with it. Keeping competition_id in the key for
-- undetermined entries would make every NULL competition across the whole
-- database one scope, so #065 LMP3 at Monza and #065 LMP3 at Spa would
-- collide. Keying those on event_id instead confines them correctly.

DROP INDEX IF EXISTS entries_ident_classed;
DROP INDEX IF EXISTS entries_ident_unclassed;
DROP INDEX IF EXISTS entries_ident_ambiguous;

-- 1. Competition known, class present. No nullable column takes part, so no
--    NULLS NOT DISTINCT is needed and none is used: every component of this
--    key is real evidence.
CREATE UNIQUE INDEX IF NOT EXISTS entries_ident_classed
    ON entries (competition_id, number, car_class)
    WHERE identity_status <> 'ambiguous'
      AND car_class IS NOT NULL
      AND competition_id IS NOT NULL;

-- 2. Competition known, class absent (pre-2013 exports). team_id is nullable
--    and is the only discriminator left, so NULLS NOT DISTINCT is required for
--    the regime to protect anything at all.
CREATE UNIQUE INDEX IF NOT EXISTS entries_ident_unclassed
    ON entries (competition_id, number, team_id) NULLS NOT DISTINCT
    WHERE identity_status <> 'ambiguous'
      AND car_class IS NULL
      AND competition_id IS NOT NULL;

-- 3. Competition known, identity disputed. The rows must coexist; re-import
--    must not multiply them.
CREATE UNIQUE INDEX IF NOT EXISTS entries_ident_ambiguous
    ON entries (competition_id, number, car_class, team_id) NULLS NOT DISTINCT
    WHERE identity_status = 'ambiguous'
      AND competition_id IS NOT NULL;

-- 4. Competition undetermined, class present. Scoped to the event.
CREATE UNIQUE INDEX IF NOT EXISTS entries_undet_classed
    ON entries (event_id, number, car_class)
    WHERE identity_status <> 'ambiguous'
      AND car_class IS NOT NULL
      AND competition_id IS NULL;

-- 5. Competition undetermined, class absent.
CREATE UNIQUE INDEX IF NOT EXISTS entries_undet_unclassed
    ON entries (event_id, number, team_id) NULLS NOT DISTINCT
    WHERE identity_status <> 'ambiguous'
      AND car_class IS NULL
      AND competition_id IS NULL;

-- 6. Competition undetermined, identity disputed.
CREATE UNIQUE INDEX IF NOT EXISTS entries_undet_ambiguous
    ON entries (event_id, number, car_class, team_id) NULLS NOT DISTINCT
    WHERE identity_status = 'ambiguous'
      AND competition_id IS NULL;

COMMIT;
