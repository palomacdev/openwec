-- 004_laps_unique_constraint.sql
ALTER TABLE laps ADD CONSTRAINT laps_session_car_lap_unique 
UNIQUE (session_id, car_id, lap_number);