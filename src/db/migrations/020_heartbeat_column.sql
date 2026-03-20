-- Heartbeat-based stale recovery: track when a job last did useful work
-- so we can distinguish "actively scraping 155 pages" from "truly stuck".

ALTER TABLE scrape_jobs ADD COLUMN IF NOT EXISTS last_activity_at TIMESTAMPTZ;

-- Backfill running jobs so they don't get killed immediately
UPDATE scrape_jobs SET last_activity_at = created_at WHERE status = 'running' AND last_activity_at IS NULL;

-- Partial index for efficient stale job queries
CREATE INDEX IF NOT EXISTS idx_scrape_jobs_heartbeat
    ON scrape_jobs (last_activity_at) WHERE status = 'running';
