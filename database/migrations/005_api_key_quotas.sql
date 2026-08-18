-- 005_api_key_quotas.sql
-- Add daily and monthly request quotas to API key requests table.
-- NULL means unlimited.

ALTER TABLE api_key_requests
    ADD COLUMN IF NOT EXISTS daily_limit   INT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS monthly_limit INT DEFAULT NULL;

COMMENT ON COLUMN api_key_requests.daily_limit   IS 'Max requests per day. NULL = unlimited.';
COMMENT ON COLUMN api_key_requests.monthly_limit IS 'Max requests per month. NULL = unlimited.';