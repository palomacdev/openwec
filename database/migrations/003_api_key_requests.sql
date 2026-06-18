-- 003_api_key_requests.sql
-- API key request/approval system

CREATE TABLE IF NOT EXISTS api_key_requests (
    id                   SERIAL PRIMARY KEY,
    name                 VARCHAR(120) NOT NULL,
    email                VARCHAR(160) NOT NULL,
    intended_use         TEXT,
    api_key              VARCHAR(64) NOT NULL UNIQUE,
    status               VARCHAR(20) NOT NULL DEFAULT 'pending', -- pending | approved | rejected
    requests_per_minute  INT NOT NULL DEFAULT 60,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    approved_at          TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_api_key_requests_key    ON api_key_requests(api_key);
CREATE INDEX IF NOT EXISTS idx_api_key_requests_status ON api_key_requests(status);