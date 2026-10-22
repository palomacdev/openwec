-- 010_observational_history_down.sql
--
-- Reverses 010.
--
-- This direction is DESTRUCTIVE once the tables hold rows. An observation
-- records what one version of one source file said about one entry; dropping
-- the tables discards that, and it cannot be reconstructed except by
-- re-reading raw/ — which is only possible while the same files are still
-- there, in the same versions. A Provisional file replaced by an Official one
-- is gone from raw/ and gone from here.
--
-- So the migration refuses while either table holds anything, and says what it
-- found. Discarding an environment on purpose is a separate, explicit act:
--
--   SET openwec.allow_history_loss = 'yes';
--   \i 010_observational_history_down.sql
--
-- On empty tables — the state 010 leaves them in — it runs unconditionally,
-- which is the only case where it loses nothing.
--
-- Running it twice is harmless.

BEGIN;

DO $$
DECLARE
    n_class  INT := 0;
    n_number INT := 0;
    override TEXT := current_setting('openwec.allow_history_loss', true);
BEGIN
    IF to_regclass('public.entry_class_history') IS NOT NULL THEN
        EXECUTE 'SELECT count(*) FROM entry_class_history' INTO n_class;
    END IF;
    IF to_regclass('public.entry_number_history') IS NOT NULL THEN
        EXECUTE 'SELECT count(*) FROM entry_number_history' INTO n_number;
    END IF;

    IF (n_class > 0 OR n_number > 0) AND coalesce(override, '') <> 'yes' THEN
        RAISE EXCEPTION
            'reverting 010 would discard % class observations and % number observations. '
            'An observation records what one version of one source file said; it cannot be '
            'reconstructed once that version is gone from raw/. If this environment is meant '
            'to be discarded, set openwec.allow_history_loss = ''yes'' in this session and '
            'run it again.',
            n_class, n_number;
    END IF;
END $$;

DROP TABLE IF EXISTS entry_number_history;
DROP TABLE IF EXISTS entry_class_history;

DROP TYPE IF EXISTS source_time_basis;

COMMIT;
