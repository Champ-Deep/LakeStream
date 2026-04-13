-- Migration 023: Job retry tracking + job params persistence + real-time status notifications
--
-- Changes:
--   1. Add retry_count column to scrape_jobs for smart stale-job restart logic
--   2. Persist job input params (data_types, max_pages, tier_override, region) on the job row
--      so stale jobs can be automatically re-queued with the original parameters
--   3. Add pg_notify trigger on job status changes (SSE/webhook consumers)

-- 1. Retry tracking column
ALTER TABLE scrape_jobs ADD COLUMN IF NOT EXISTS retry_count INT NOT NULL DEFAULT 0;

-- 2. Job input param columns (needed to re-queue stale jobs with original params)
ALTER TABLE scrape_jobs ADD COLUMN IF NOT EXISTS input_data_types TEXT[];
ALTER TABLE scrape_jobs ADD COLUMN IF NOT EXISTS input_max_pages INT;
ALTER TABLE scrape_jobs ADD COLUMN IF NOT EXISTS input_tier_override TEXT;
ALTER TABLE scrape_jobs ADD COLUMN IF NOT EXISTS input_region TEXT;

-- 2. pg_notify trigger so API can stream real-time status changes to connected clients
CREATE OR REPLACE FUNCTION notify_job_status_changed()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.status IS DISTINCT FROM NEW.status THEN
        PERFORM pg_notify('job_status_changed', json_build_object(
            'job_id',       NEW.id,
            'domain',       NEW.domain,
            'status',       NEW.status,
            'retry_count',  NEW.retry_count,
            'error_message',NEW.error_message,
            'pages_scraped',NEW.pages_scraped
        )::text);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS scrape_jobs_status_notify ON scrape_jobs;
CREATE TRIGGER scrape_jobs_status_notify
    AFTER UPDATE ON scrape_jobs
    FOR EACH ROW
    EXECUTE FUNCTION notify_job_status_changed();
