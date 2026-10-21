-- 009_competition_identity_down.sql
--
-- Reverses 009. Dropping competition_anchors discards the matching substrate,
-- so a later re-import cannot recognise the competitions it created earlier
-- and will mint new identities for them. Every identity_uid still in
-- competitions becomes unreachable: the rows survive, the ability to match a
-- discovery back to them does not. Unreviewed decisions in
-- competition_match_reviews are lost outright.
--
-- So the migration refuses whenever either table holds anything, and says what
-- it found. Discarding an environment on purpose is a separate, explicit act:
--
--   SET openwec.allow_identity_loss = 'yes';
--   \i 009_competition_identity_down.sql
--
-- On an empty 009 — nothing anchored, nothing under review — it runs
-- unconditionally, which is the only case where it loses nothing.
--
-- Running it twice is harmless.

BEGIN;

DO $$
DECLARE
    n_anchors INT := 0;
    n_reviews INT := 0;
    n_uids    INT := 0;
    override  TEXT := current_setting('openwec.allow_identity_loss', true);
BEGIN
    IF to_regclass('public.competition_anchors') IS NOT NULL THEN
        EXECUTE 'SELECT count(*) FROM competition_anchors' INTO n_anchors;
    END IF;
    IF to_regclass('public.competition_match_reviews') IS NOT NULL THEN
        EXECUTE 'SELECT count(*) FROM competition_match_reviews WHERE resolved_at IS NULL'
            INTO n_reviews;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'competitions' AND column_name = 'identity_uid') THEN
        EXECUTE 'SELECT count(*) FROM competitions' INTO n_uids;
    END IF;

    -- A competition carrying identity_uid is at risk even with no anchors at
    -- all: dropping the column destroys the identity itself, and no re-import
    -- can bring that uid back. So the count of competitions gates this too,
    -- not just the presence of anchors.
    IF (n_anchors > 0 OR n_reviews > 0 OR n_uids > 0) AND coalesce(override, '') <> 'yes' THEN
        RAISE EXCEPTION
            'reverting 009 would destroy % competition identities, % anchors and % open reviews. '
            'This is not reversible by re-importing: the identity_uid values cannot be recovered '
            'and matching cannot recognise these competitions again. If this environment is meant '
            'to be discarded, set openwec.allow_identity_loss = ''yes'' in this session and run it again.',
            n_uids, n_anchors, n_reviews;
    END IF;
END $$;

DROP TABLE IF EXISTS competition_match_reviews;
DROP TABLE IF EXISTS competition_anchors;

DROP TYPE IF EXISTS competition_review_kind;
DROP TYPE IF EXISTS anchor_exclusivity;
DROP TYPE IF EXISTS anchor_state;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'competitions_identity_uid_key') THEN
        ALTER TABLE competitions DROP CONSTRAINT competitions_identity_uid_key;
    END IF;
END $$;

ALTER TABLE competitions
    DROP COLUMN IF EXISTS identity_uid,
    DROP COLUMN IF EXISTS evidence_fingerprint,
    DROP COLUMN IF EXISTS display_name,
    DROP COLUMN IF EXISTS last_seen_at;

COMMIT;
