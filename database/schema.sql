-- ============================================================
-- OpenWEC — Unified Database Schema
-- PostgreSQL 16 + TimescaleDB
--
-- Covers: WEC, ELMS, Asian LMS, Le Mans Cup, IMSA
--
-- Setup:
--   1. CREATE EXTENSION IF NOT EXISTS timescaledb;
--   2. psql -U postgres -d openwec -f schema.sql
-- ============================================================


-- ────────────────────────────────────────────────────────────
-- ENUMS
-- ────────────────────────────────────────────────────────────

CREATE TYPE series_name AS ENUM (
    'WEC', 'ELMS', 'ALMS', 'LEMANSCUP', 'IMSA'
);

CREATE TYPE session_type AS ENUM (
    'Race', 'Qualifying', 'Hyperpole', 'Practice',
    'WarmUp', 'Test', 'Prologue', 'Other'
);

CREATE TYPE entry_status AS ENUM (
    'Classified', 'Not Classified', 'DNF', 'DNS',
    'DSQ', 'Retired', 'Other'
);

CREATE TYPE driver_rating AS ENUM (
    'Platinum', 'Gold', 'Silver', 'Bronze'
);

CREATE TYPE flag_type AS ENUM (
    'GF', 'SC', 'FCY', 'YF', 'RF', 'Other'
);


-- ────────────────────────────────────────────────────────────
-- SERIES & SEASONS
-- ────────────────────────────────────────────────────────────

CREATE TABLE series (
    id          SMALLSERIAL PRIMARY KEY,
    key         series_name UNIQUE NOT NULL,
    name        VARCHAR(60) NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO series (key, name) VALUES
    ('WEC',        'FIA World Endurance Championship'),
    ('ELMS',       'European Le Mans Series'),
    ('ALMS',       'Asian Le Mans Series'),
    ('LEMANSCUP',  'Michelin Le Mans Cup'),
    ('IMSA',       'IMSA WeatherTech SportsCar Championship');


CREATE TABLE seasons (
    id          SERIAL PRIMARY KEY,
    series_id   SMALLINT NOT NULL REFERENCES series(id),
    raw_id      VARCHAR(20) NOT NULL,       -- "13_2024"
    year        SMALLINT NOT NULL,
    label       VARCHAR(20) NOT NULL,       -- "2024", "2018-2019"
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (series_id, raw_id)
);


-- ────────────────────────────────────────────────────────────
-- EVENTS & SESSIONS
-- ────────────────────────────────────────────────────────────

CREATE TABLE events (
    id          SERIAL PRIMARY KEY,
    season_id   INT NOT NULL REFERENCES seasons(id),
    raw_id      VARCHAR(60) NOT NULL,       -- "04_LE MANS"
    name        VARCHAR(100) NOT NULL,
    round       SMALLINT,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (season_id, raw_id)
);


CREATE TABLE sessions (
    id              SERIAL PRIMARY KEY,
    event_id        INT NOT NULL REFERENCES events(id),
    raw_id          VARCHAR(80) NOT NULL,   -- "202406151000_Race"
    name            VARCHAR(80) NOT NULL,   -- "Race", "Free Practice 1"
    session_type    session_type NOT NULL DEFAULT 'Other',
    session_at      TIMESTAMPTZ,
    imsa_series     VARCHAR(40),            -- "WeatherTech", "Endurance", null for non-IMSA
    source_url      TEXT,
    snapshot_hour   SMALLINT,              -- for Le Mans hourly snapshots (1-24), null otherwise
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (event_id, raw_id)
);


-- ────────────────────────────────────────────────────────────
-- TEAMS
-- ────────────────────────────────────────────────────────────

CREATE TABLE teams (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(120) NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (name)
);


-- ────────────────────────────────────────────────────────────
-- CARS
-- ────────────────────────────────────────────────────────────

CREATE TABLE cars (
    id              SERIAL PRIMARY KEY,
    number          VARCHAR(4) NOT NULL,
    team_id         INT REFERENCES teams(id),
    vehicle         VARCHAR(80),
    manufacturer    VARCHAR(60),
    car_class       VARCHAR(20),        -- HYPERCAR, LMP2, LMGT3, GTP, GTD...
    car_group       VARCHAR(20),
    tires           VARCHAR(10),
    imsa_car_id     INT,               -- IMSA internal ID (nullable)
    imsa_team_id    INT,               -- IMSA internal ID (nullable)
    created_at      TIMESTAMPTZ DEFAULT NOW()
);


-- ────────────────────────────────────────────────────────────
-- DRIVERS
-- ────────────────────────────────────────────────────────────

CREATE TABLE drivers (
    id                      SERIAL PRIMARY KEY,
    first_name              VARCHAR(60),
    last_name               VARCHAR(60),
    short_name              VARCHAR(20),
    country                 VARCHAR(4),
    license                 VARCHAR(20),
    hometown                VARCHAR(80),
    imsa_driver_id          INT,           -- IMSA internal ID (nullable)
    imsa_driver_plug_id     INT,
    imsa_driver_rating      driver_rating, -- Platinum/Gold/Silver/Bronze (nullable)
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (first_name, last_name)
);


-- ────────────────────────────────────────────────────────────
-- RESULTS
-- Final classification per session per car
-- ────────────────────────────────────────────────────────────

CREATE TABLE results (
    id                  SERIAL PRIMARY KEY,
    session_id          INT NOT NULL REFERENCES sessions(id),
    car_id              INT NOT NULL REFERENCES cars(id),

    position            SMALLINT,
    status              entry_status NOT NULL DEFAULT 'Classified',
    laps_completed      SMALLINT,

    total_time_raw      VARCHAR(30),        -- "24:01'55.856" — kept as-is
    total_time_s        DECIMAL(12,3),      -- converted to seconds

    gap_to_first_raw    VARCHAR(20),
    gap_to_first_s      DECIMAL(10,3),
    gap_to_prev_raw     VARCHAR(20),
    gap_to_prev_s       DECIMAL(10,3),

    fl_lap_number       SMALLINT,
    fl_time_raw         VARCHAR(20),        -- "3'29.208"
    fl_time_s           DECIMAL(8,3),
    fl_kph              DECIMAL(7,2),

    source_url          TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (session_id, car_id)
);


-- Many-to-many: result ↔ driver (slot = 1..6)
CREATE TABLE result_drivers (
    id          SERIAL PRIMARY KEY,
    result_id   INT NOT NULL REFERENCES results(id) ON DELETE CASCADE,
    driver_id   INT NOT NULL REFERENCES drivers(id),
    slot        SMALLINT NOT NULL,          -- 1..6 (position in entry list)
    UNIQUE (result_id, slot)
);


-- ────────────────────────────────────────────────────────────
-- LAPS  (WEC + ELMS only — time-series)
-- ────────────────────────────────────────────────────────────

CREATE TABLE laps (
    id                          BIGSERIAL,
    session_id                  INT NOT NULL REFERENCES sessions(id),
    car_id                      INT NOT NULL REFERENCES cars(id),
    driver_id                   INT REFERENCES drivers(id),
    driver_slot                 SMALLINT,   -- which driver was driving (1..5)

    lap_number                  SMALLINT NOT NULL,
    lap_time_s                  DECIMAL(10,3),

    -- Sectors (float — comes ready from CSV)
    s1_s                        DECIMAL(8,3),
    s2_s                        DECIMAL(8,3),
    s3_s                        DECIMAL(8,3),

    -- Speed
    kph                         DECIMAL(7,2),
    top_speed_kph               DECIMAL(7,2),

    -- Race clock
    elapsed_raw                 VARCHAR(20),    -- "3:53.276" from lap start
    hour_raw                    VARCHAR(20),    -- "16:04:19.878" wall clock

    -- Flags
    lap_improvement             BOOLEAN DEFAULT FALSE,  -- personal best
    crossing_finish_in_pit      BOOLEAN DEFAULT FALSE,  -- pit lap
    flag_at_fl                  flag_type,
    pit_time_s                  DECIMAL(8,3),

    -- Timestamp (derived from session_at + elapsed when available)
    lap_recorded_at             TIMESTAMPTZ,

    created_at                  TIMESTAMPTZ DEFAULT NOW(),

    PRIMARY KEY (id, lap_recorded_at)
);

-- TimescaleDB hypertable (run after CREATE TABLE)
-- SELECT create_hypertable('laps', 'lap_recorded_at',
--     chunk_time_interval => INTERVAL '1 month',
--     if_not_exists => TRUE
-- );


-- ────────────────────────────────────────────────────────────
-- INGEST LOG
-- Tracks every file processed — enables resume + audit
-- ────────────────────────────────────────────────────────────

CREATE TABLE ingest_log (
    id              SERIAL PRIMARY KEY,
    series_key      series_name,
    source_url      TEXT UNIQUE NOT NULL,
    file_type       VARCHAR(20),        -- classification, analysis, weather
    status          VARCHAR(20),        -- success, error, skipped
    rows_loaded     INT DEFAULT 0,
    error_message   TEXT,
    ingested_at     TIMESTAMPTZ DEFAULT NOW()
);


-- ────────────────────────────────────────────────────────────
-- INDEXES
-- ────────────────────────────────────────────────────────────

CREATE INDEX idx_seasons_series      ON seasons(series_id);
CREATE INDEX idx_events_season       ON events(season_id);
CREATE INDEX idx_sessions_event      ON sessions(event_id);
CREATE INDEX idx_sessions_type       ON sessions(session_type);
CREATE INDEX idx_results_session     ON results(session_id);
CREATE INDEX idx_results_position    ON results(session_id, position);
CREATE INDEX idx_results_car         ON results(car_id);
CREATE INDEX idx_result_drivers_res  ON result_drivers(result_id);
CREATE INDEX idx_result_drivers_drv  ON result_drivers(driver_id);
CREATE INDEX idx_laps_session_car    ON laps(session_id, car_id);
CREATE INDEX idx_laps_lap_number     ON laps(session_id, lap_number);
CREATE INDEX idx_laps_driver         ON laps(driver_id);
CREATE INDEX idx_cars_number         ON cars(number);
CREATE INDEX idx_cars_class          ON cars(car_class);
CREATE INDEX idx_drivers_names       ON drivers(last_name, first_name);
CREATE INDEX idx_ingest_status       ON ingest_log(status);


-- ────────────────────────────────────────────────────────────
-- VIEWS
-- ────────────────────────────────────────────────────────────

-- Full race result with all context
CREATE OR REPLACE VIEW v_race_results AS
SELECT
    sr.key                                      AS series,
    se.label                                    AS season,
    ev.name                                     AS event,
    s.session_at,
    s.name                                      AS session,
    s.snapshot_hour,
    c.number                                    AS car,
    c.car_class,
    c.vehicle,
    t.name                                      AS team,
    r.position,
    r.status,
    r.laps_completed,
    r.total_time_s,
    r.gap_to_first_s,
    r.fl_time_s,
    r.fl_kph
FROM results r
JOIN sessions s   ON s.id = r.session_id
JOIN events   ev  ON ev.id = s.event_id
JOIN seasons  se  ON se.id = ev.season_id
JOIN series   sr  ON sr.id = se.series_id
JOIN cars     c   ON c.id = r.car_id
LEFT JOIN teams t ON t.id = c.team_id
WHERE s.session_type = 'Race'
ORDER BY se.year, ev.round, r.position;


-- Lap records per circuit per class
CREATE OR REPLACE VIEW v_lap_records AS
SELECT
    ev.name                 AS event,
    c.car_class,
    MIN(r.fl_time_s)        AS lap_record_s,
    se.label                AS season,
    sr.key                  AS series
FROM results r
JOIN sessions s   ON s.id = r.session_id
JOIN events   ev  ON ev.id = s.event_id
JOIN seasons  se  ON se.id = ev.season_id
JOIN series   sr  ON sr.id = se.series_id
JOIN cars     c   ON c.id = r.car_id
WHERE s.session_type = 'Race'
  AND r.fl_time_s IS NOT NULL
  AND (s.snapshot_hour IS NULL OR s.snapshot_hour = 24)
GROUP BY ev.name, c.car_class, se.label, sr.key
ORDER BY ev.name, c.car_class;


-- Driver career results (one row per race entry)
CREATE OR REPLACE VIEW v_driver_career AS
SELECT
    d.first_name || ' ' || d.last_name     AS driver,
    d.country,
    d.imsa_driver_rating                   AS rating,
    sr.key                                 AS series,
    se.label                               AS season,
    ev.name                                AS event,
    s.session_at,
    c.number                               AS car,
    c.car_class,
    t.name                                 AS team,
    c.vehicle,
    r.position,
    r.status,
    r.laps_completed
FROM result_drivers rd
JOIN results  r   ON r.id = rd.result_id
JOIN drivers  d   ON d.id = rd.driver_id
JOIN sessions s   ON s.id = r.session_id
JOIN events   ev  ON ev.id = s.event_id
JOIN seasons  se  ON se.id = ev.season_id
JOIN series   sr  ON sr.id = se.series_id
JOIN cars     c   ON c.id = r.car_id
LEFT JOIN teams t ON t.id = c.team_id
WHERE s.session_type = 'Race'
  AND (s.snapshot_hour IS NULL OR s.snapshot_hour = 24)
ORDER BY s.session_at, r.position;