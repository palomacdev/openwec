-- 007_competition_resolution_down.sql
--
-- Reverses 007. Additive in, additive out: nothing outside these objects
-- references them, and results/laps/result_drivers/analytics are untouched in
-- both directions. Running it twice is harmless.

BEGIN;

DROP TABLE IF EXISTS session_competitions;
DROP TABLE IF EXISTS session_competition_candidates;

DROP TYPE IF EXISTS session_assignment_status;
DROP TYPE IF EXISTS candidate_evaluation;

ALTER TABLE competitions DROP COLUMN IF EXISTS evidence;
ALTER TABLE competitions DROP COLUMN IF EXISTS resolution;

DROP TYPE IF EXISTS competition_resolution;

COMMIT;
