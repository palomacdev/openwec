-- 009_competition_identity.sql
--
-- PROPOSAL — not to be applied outside an isolated database until approved.
--
-- Separates four things that a content hash was conflating:
--
--   identity_uid         what the competition IS. Minted once, never derived
--                        from evidence, never recomputed. Survives every
--                        re-import, re-discovery and database rebuild.
--   evidence_fingerprint what has been OBSERVED about it. Deterministic over
--                        the current import, and free to change whenever the
--                        source gains a session or corrects a number. Never
--                        used to decide identity.
--   display_name         what it is CALLED. Human-facing, mutable, carries no
--                        constraint.
--   competition_anchors  how a new discovery is MATCHED to an existing
--                        competition. This is the only thing matching reads.
--
-- A content hash fails as identity precisely because the content is supposed to
-- change: adding the Thursday practice session to an event does not create a
-- different championship, but it does change the hash.

BEGIN;

ALTER TABLE competitions
    ADD COLUMN IF NOT EXISTS identity_uid         UUID NOT NULL DEFAULT gen_random_uuid(),
    ADD COLUMN IF NOT EXISTS evidence_fingerprint CHAR(64),
    ADD COLUMN IF NOT EXISTS display_name         TEXT,
    ADD COLUMN IF NOT EXISTS last_seen_at         TIMESTAMPTZ;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'competitions_identity_uid_key') THEN
        ALTER TABLE competitions ADD CONSTRAINT competitions_identity_uid_key UNIQUE (identity_uid);
    END IF;
END $$;


-- ── Anchors: the substrate matching reads ─────────────────────────────────
-- An anchor is a piece of evidence durable enough to recognise a competition
-- again on the next import. Two kinds of evidence, and they are not
-- interchangeable:
--
--   exclusive  an entry-list identifier. An entry belongs to one list, so the
--              identifier belongs to one competition within the event. This is
--              what can establish identity on its own.
--   shared     a session, or an entry as (number, team). Both can belong to
--              several competitions at once — Monza 2018 runs two
--              championships in the same Morning and Afternoon sessions, and
--              Le Mans fields the same cars in the Test Day list and the race
--              list — so neither can establish identity by itself.
--
-- Treating a session as exclusive is what made two provisional competitions
-- sharing one session collapse into one. The uniqueness below therefore
-- applies only to exclusive anchors.
--
-- 'entry' anchors exist because session overlap alone is not enough to match a
-- competition that has no identifiers. A support series running only in Free
-- Practice 1 has a session set contained in the main competition's, and
-- containment would wrongly match it; its entry population would not.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'anchor_exclusivity') THEN
        CREATE TYPE anchor_exclusivity AS ENUM ('exclusive', 'shared');
    END IF;
END $$;

-- Evidence goes stale. An identifier the source has since corrected away still
-- sits in this table, and without a state it would go on confirming matches
-- long after it stopped being true: a discovery carrying only the old value
-- would be recognised automatically. So an anchor is 'current' while the most
-- recent import of its competition still carried it, and 'superseded' once it
-- did not.
--
-- Only current anchors confirm. A superseded one may still nominate a
-- candidate, and that route ends in review, never in an automatic match.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'anchor_state') THEN
        CREATE TYPE anchor_state AS ENUM ('current', 'superseded');
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS competition_anchors (
    id              SERIAL PRIMARY KEY,
    competition_id  INT NOT NULL REFERENCES competitions(id) ON DELETE CASCADE,
    event_id        INT NOT NULL REFERENCES events(id),
    anchor_kind     VARCHAR(20) NOT NULL,
    anchor_value    VARCHAR(120) NOT NULL,
    exclusivity     anchor_exclusivity NOT NULL,
    state           anchor_state NOT NULL DEFAULT 'current',
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    superseded_at   TIMESTAMPTZ,

    -- One row per anchor per competition, whatever its exclusivity.
    UNIQUE (competition_id, anchor_kind, anchor_value),

    CONSTRAINT anchor_kind_known
        CHECK (anchor_kind IN ('ecm_entry_id', 'imsa_entry_id', 'session', 'entry')),

    -- Exclusivity follows from the kind, so it cannot drift by convention.
    CONSTRAINT anchor_exclusivity_follows_kind
        CHECK (exclusivity = CASE WHEN anchor_kind IN ('session', 'entry')
                                  THEN 'shared'::anchor_exclusivity
                                  ELSE 'exclusive'::anchor_exclusivity END),

    CONSTRAINT anchor_supersession_dated
        CHECK ((state = 'superseded') = (superseded_at IS NOT NULL))
);

-- Only exclusive anchors are unique per event, and only while current. A value
-- the source moved from one competition to another must be claimable by the
-- new one; the superseded row stays as history and stops blocking it.
CREATE UNIQUE INDEX IF NOT EXISTS competition_anchors_exclusive
    ON competition_anchors (event_id, anchor_kind, anchor_value)
    WHERE exclusivity = 'exclusive' AND state = 'current';

CREATE INDEX IF NOT EXISTS idx_competition_anchors_comp ON competition_anchors(competition_id);
CREATE INDEX IF NOT EXISTS idx_competition_anchors_look ON competition_anchors(event_id, anchor_kind, anchor_value);


-- ── Matching decisions that were NOT taken automatically ──────────────────
-- Written when a discovery cannot be matched one-to-one against what already
-- exists. Nothing is created, merged or split on these; they are the queue a
-- person works through.
--
--   ambiguous_match   one discovered group's anchors reach several existing
--                     competitions, or several discovered groups reach the
--                     same one. Either way the correspondence is not 1:1.
--   merge_hypothesis  two identifier groups where one is confined to the
--                     other's sessions and fields no class of its own. That is
--                     what an entry list with absentees looks like, and also
--                     what a second championship sharing sessions looks like.
--                     Monza 2018 produces exactly one of these.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'competition_review_kind') THEN
        CREATE TYPE competition_review_kind AS ENUM ('ambiguous_match', 'merge_hypothesis');
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS competition_match_reviews (
    id                SERIAL PRIMARY KEY,
    event_id          INT NOT NULL REFERENCES events(id),
    kind              competition_review_kind NOT NULL,

    -- Deterministic over the discovery that raised it, so re-running the same
    -- import updates this row instead of adding another.
    discovery_key     CHAR(64) NOT NULL,

    candidate_ids     INT[] NOT NULL DEFAULT '{}',   -- competitions involved
    evidence          TEXT,
    rationale         TEXT,
    detected_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at       TIMESTAMPTZ,
    resolved_by       VARCHAR(80),
    resolution_note   TEXT,

    UNIQUE (event_id, kind, discovery_key),
    CONSTRAINT review_resolution_complete
        CHECK ((resolved_at IS NULL) = (resolved_by IS NULL))
);

CREATE INDEX IF NOT EXISTS idx_competition_reviews_open
    ON competition_match_reviews(event_id) WHERE resolved_at IS NULL;

COMMIT;
