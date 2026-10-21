-- 010_observational_history.sql
--
-- Additive. Creates two tables and leaves them empty. Nothing is populated,
-- consolidated, normalised or decided here, and no existing column, index or
-- constraint is touched: entries.car_class and the uniqueness regimes of
-- migration 008 stay exactly as they are.
--
-- Why
-- ---
-- 008 keys an entry on (competition, number, class). The audit of the 34
-- shared source identifiers found that two of its four mechanisms are
-- consequences of that key rather than defects of the source:
--
--   * seven entries whose class is refined mid-event — LMP2 to LMP2 PRO/AM in
--     ELMS, LMP3 to INN for a hydrogen prototype, and #17 ARCTIC ENERGY at
--     Monza 2022 alternating S and J session by session
--   * fifteen at Monza 2018 whose number is written padded in two files, where
--     the padding tracks which of two merged entry lists the row came from
--
-- Class and number are therefore recorded as things OBSERVED, per source row,
-- rather than inferred from whichever value the key happened to keep. What to
-- do about the entries that split is not decided here and is not decided by
-- this migration.
--
-- The grain is the raw observation
-- --------------------------------
-- One row per entry, per source file, per version of that file, per line. Not
-- one row per entry per session: the source publishes a session several times.
-- 65 files are named Provisional, 78 Unofficial, 61 Official; 56 sessions hold
-- three classification files and 69 hold two that are byte-identical.
--
-- Keying on the digest alone would collapse a byte-identical Provisional and
-- Official into one row. No value would be lost — the bytes are equal — but
-- the fact that the source published both would be, and the revision stage
-- lives in the file name.
--
-- No timestamp is invented
-- ------------------------
-- Searched and not found: no classification file carries a date or time column
-- with absolute meaning, there is no metadata preamble before the header, and
-- nothing records when a file was published or revised. The filesystem mtime
-- is the local download. The weather files do carry UTC time, but they date
-- the SESSION, not the observation of a class or a number, so they are not
-- used here.
--
-- source_observed_at is therefore NULL, and source_time_basis says so.

BEGIN;

-- Where a timestamp came from, when one exists. Only 'none' occurs today; the
-- other two are the shapes a source could provide, so that a future value
-- cannot be written without declaring its origin.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'source_time_basis') THEN
        CREATE TYPE source_time_basis AS ENUM ('none', 'file_published', 'row_timestamp');
    END IF;
END $$;


CREATE TABLE IF NOT EXISTS entry_class_history (
    id                 SERIAL PRIMARY KEY,

    -- RESTRICT on both: deleting an entry or a session must not take evidence
    -- with it. Consolidating entries later has to move the history first, as
    -- a deliberate act.
    entry_id           INT NOT NULL REFERENCES entries(id)   ON DELETE RESTRICT,
    session_id         INT NOT NULL REFERENCES sessions(id)  ON DELETE RESTRICT,

    car_class          VARCHAR(20),          -- NULL: the source carried no class

    source_file        TEXT     NOT NULL,    -- path relative to raw/
    source_digest      CHAR(64) NOT NULL,    -- sha256 of the whole file
    source_line        INT      NOT NULL,    -- line within THAT version

    source_observed_at TIMESTAMPTZ,
    source_time_basis  source_time_basis NOT NULL DEFAULT 'none',
    ingested_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),   -- the write, only

    UNIQUE (entry_id, source_file, source_digest, source_line),

    CONSTRAINT class_source_line_positive
        CHECK (source_line > 0),
    CONSTRAINT class_source_file_not_empty
        CHECK (length(btrim(source_file)) > 0),
    CONSTRAINT class_source_digest_is_sha256
        CHECK (source_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT class_time_basis_matches_value
        CHECK ((source_time_basis = 'none') = (source_observed_at IS NULL))
);


CREATE TABLE IF NOT EXISTS entry_number_history (
    id                 SERIAL PRIMARY KEY,

    entry_id           INT NOT NULL REFERENCES entries(id)   ON DELETE RESTRICT,
    session_id         INT NOT NULL REFERENCES sessions(id)  ON DELETE RESTRICT,

    -- As the source wrote it. #03 stays #03. Normalising would merge cars that
    -- race together: 116 pairs share a session, 96 of them in IMSA, where #01
    -- and #1 are different cars.
    number_as_written  VARCHAR(10) NOT NULL,

    source_file        TEXT     NOT NULL,
    source_digest      CHAR(64) NOT NULL,
    source_line        INT      NOT NULL,

    source_observed_at TIMESTAMPTZ,
    source_time_basis  source_time_basis NOT NULL DEFAULT 'none',
    ingested_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (entry_id, source_file, source_digest, source_line),

    CONSTRAINT number_source_line_positive
        CHECK (source_line > 0),
    CONSTRAINT number_source_file_not_empty
        CHECK (length(btrim(source_file)) > 0),
    CONSTRAINT number_source_digest_is_sha256
        CHECK (source_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT number_number_not_empty
        CHECK (length(btrim(number_as_written)) > 0),
    CONSTRAINT number_time_basis_matches_value
        CHECK ((source_time_basis = 'none') = (source_observed_at IS NULL))
);


-- ── Indexes ───────────────────────────────────────────────────────────────
-- Only what is not already served.
--
-- An index on (entry_id) alone is NOT created: the UNIQUE constraint above
-- already indexes (entry_id, source_file, source_digest, source_line), and a
-- btree serves a leading-column lookup. entry_team_history carries exactly
-- that redundancy today; this does not repeat it.
--
-- (session_id) is needed: no other index leads with it, and "what was observed
-- in this session" is the reconciliation query.
--
-- (source_digest) is needed for the inverse: "what did this version of this
-- file observe", which is how a re-ingestion is reconciled without scanning.
CREATE INDEX IF NOT EXISTS idx_entry_class_history_session
    ON entry_class_history(session_id);
CREATE INDEX IF NOT EXISTS idx_entry_class_history_digest
    ON entry_class_history(source_digest);

CREATE INDEX IF NOT EXISTS idx_entry_number_history_session
    ON entry_number_history(session_id);
CREATE INDEX IF NOT EXISTS idx_entry_number_history_digest
    ON entry_number_history(source_digest);

COMMIT;
