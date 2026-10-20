-- 006_competitions_entries_down.sql
--
-- Reverses 006. Safe because 006 is purely additive: nothing outside these
-- objects references them yet. results, laps and the analytics tables are not
-- touched by either direction.
--
-- Running this twice is harmless.

BEGIN;

DROP TABLE IF EXISTS entry_source_ids;
DROP TABLE IF EXISTS entry_team_history;
DROP TABLE IF EXISTS entries;
DROP TABLE IF EXISTS competitions;

DROP TYPE IF EXISTS entry_identity_status;

COMMIT;
