-- OpenWEC — Test Fixtures
-- Minimal data set for API integration tests.
-- Applied after schema.sql in CI environment.

-- Series
INSERT INTO series (id, key, name) VALUES
  (1, 'WEC',       'FIA World Endurance Championship'),
  (2, 'ELMS',      'European Le Mans Series'),
  (3, 'ALMS',      'Asian Le Mans Series'),
  (4, 'LEMANSCUP', 'Michelin Le Mans Cup'),
  (5, 'IMSA',      'IMSA WeatherTech SportsCar Championship')
ON CONFLICT DO NOTHING;

-- Season
INSERT INTO seasons (id, series_id, raw_id, year, label) VALUES
  (1, 1, '15_2026', 2026, '2026')
ON CONFLICT DO NOTHING;

-- Event
INSERT INTO events (id, season_id, raw_id, name, round) VALUES
  (1, 1, '03_LE MANS', 'LE MANS', 3)
ON CONFLICT DO NOTHING;

-- Session
INSERT INTO sessions (id, event_id, raw_id, name, session_type) VALUES
  (1, 1, '20260614_Race', 'Race', 'Race')
ON CONFLICT DO NOTHING;

-- Team
INSERT INTO teams (id, name) VALUES
  (1, 'Toyota Gazoo Racing'),
  (2, 'AF Corse')
ON CONFLICT DO NOTHING;

-- Driver
INSERT INTO drivers (id, first_name, last_name, country) VALUES
  (1, 'Mike',  'Conway',    'GBR'),
  (2, 'Kamui', 'Kobayashi', 'JPN')
ON CONFLICT DO NOTHING;

-- Car
INSERT INTO cars (id, team_id, number, car_class, vehicle, manufacturer, tires) VALUES
  (1, 1, '7', 'HYPERCAR', 'Toyota GR010 Hybrid', 'Toyota', 'M')
ON CONFLICT DO NOTHING;

-- Result
INSERT INTO results (id, session_id, car_id, position, status, laps_completed, total_time_s) VALUES
  (1, 1, 1, 1, 'Classified', 381, 86400.0)
ON CONFLICT DO NOTHING;

-- Result drivers
INSERT INTO result_drivers (result_id, driver_id, slot) VALUES

  (1, 1, 1),
  (1, 2, 2)
ON CONFLICT DO NOTHING;

-- API key requests table (may not exist if migration not run)
CREATE TABLE IF NOT EXISTS api_key_requests (
    id                   SERIAL PRIMARY KEY,
    name                 VARCHAR(120) NOT NULL,
    email                VARCHAR(160) NOT NULL,
    intended_use         TEXT,
    api_key              VARCHAR(64) NOT NULL UNIQUE,
    status               VARCHAR(20) NOT NULL DEFAULT 'pending',
    requests_per_minute  INT NOT NULL DEFAULT 60,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    approved_at          TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_api_key_requests_key ON api_key_requests(api_key);
CREATE INDEX IF NOT EXISTS idx_api_key_requests_status ON api_key_requests(status);

-- Analytics tables (created by engine.py at runtime, needed for API tests)
CREATE TABLE IF NOT EXISTS analytics_stints (
    id              SERIAL PRIMARY KEY,
    session_id      INT NOT NULL,
    car_id          INT NOT NULL,
    stint_number    SMALLINT NOT NULL,
    start_lap       SMALLINT,
    end_lap         SMALLINT,
    lap_count       SMALLINT,
    tyre_age_laps   SMALLINT,
    baseline_pace_s DECIMAL(8,3),
    degradation_s_per_lap DECIMAL(8,4),
    consistency_s   DECIMAL(8,3),
    is_final_stint  BOOLEAN DEFAULT FALSE,
    computed_at     TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (session_id, car_id, stint_number)
);

CREATE TABLE IF NOT EXISTS analytics_car_session (
    id              SERIAL PRIMARY KEY,
    session_id      INT NOT NULL,
    car_id          INT NOT NULL,
    total_laps      SMALLINT,
    green_flag_laps SMALLINT,
    pit_stops       SMALLINT,
    best_lap_s      DECIMAL(8,3),
    avg_pace_s      DECIMAL(8,3),
    consistency_s   DECIMAL(8,3),
    computed_at     TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (session_id, car_id)
);