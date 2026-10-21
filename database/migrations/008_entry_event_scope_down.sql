-- 008_entry_event_scope_down.sql
--
-- Reverses 008, restoring the three uniqueness regimes of 006 and the
-- NOT NULL on entries.competition_id.
--
-- This direction is only safe while no entry has competition_id IS NULL;
-- those rows cannot be expressed by 006 and would have to be assigned a
-- competition by hand first. The migration refuses rather than guessing.
--
-- Running it twice is harmless.

BEGIN;

DO $$
DECLARE
    orphans INT;
BEGIN
    SELECT count(*) INTO orphans FROM entries WHERE competition_id IS NULL;
    IF orphans > 0 THEN
        RAISE EXCEPTION
            '% entries have no determined competition; 006 cannot represent them. '
            'Assign or remove them before reverting 008.', orphans;
    END IF;
END $$;

DROP INDEX IF EXISTS entries_undet_classed;
DROP INDEX IF EXISTS entries_undet_unclassed;
DROP INDEX IF EXISTS entries_undet_ambiguous;
DROP INDEX IF EXISTS entries_ident_classed;
DROP INDEX IF EXISTS entries_ident_unclassed;
DROP INDEX IF EXISTS entries_ident_ambiguous;

CREATE UNIQUE INDEX IF NOT EXISTS entries_ident_classed
    ON entries (competition_id, number, car_class)
    WHERE identity_status <> 'ambiguous' AND car_class IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS entries_ident_unclassed
    ON entries (competition_id, number, team_id) NULLS NOT DISTINCT
    WHERE identity_status <> 'ambiguous' AND car_class IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS entries_ident_ambiguous
    ON entries (competition_id, number, car_class, team_id) NULLS NOT DISTINCT
    WHERE identity_status = 'ambiguous';

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'entries_competition_event_fkey') THEN
        ALTER TABLE entries DROP CONSTRAINT entries_competition_event_fkey;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'entries_competition_id_fkey') THEN
        ALTER TABLE entries
            ADD CONSTRAINT entries_competition_id_fkey
            FOREIGN KEY (competition_id) REFERENCES competitions(id);
    END IF;
END $$;

ALTER TABLE entries ALTER COLUMN competition_id SET NOT NULL;

DROP INDEX IF EXISTS idx_entries_event;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'entries_event_id_fkey') THEN
        ALTER TABLE entries DROP CONSTRAINT entries_event_id_fkey;
    END IF;
END $$;

ALTER TABLE entries DROP COLUMN IF EXISTS event_id;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'competitions_id_event_key') THEN
        ALTER TABLE competitions DROP CONSTRAINT competitions_id_event_key;
    END IF;
END $$;

COMMIT;
