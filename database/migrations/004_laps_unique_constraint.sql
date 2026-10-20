-- 004_laps_unique_constraint.sql
-- Idempotent: ALTER TABLE ... ADD CONSTRAINT has no IF NOT EXISTS, so the
-- guard is explicit. Needed because the migrations are now applied in CI on a
-- database that schema.sql already created with this constraint in place.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'laps_session_car_lap_unique'
    ) THEN
        ALTER TABLE laps ADD CONSTRAINT laps_session_car_lap_unique
        UNIQUE (session_id, car_id, lap_number);
    END IF;
END $$;
