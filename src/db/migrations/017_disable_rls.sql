-- Migration 017: Disable Row-Level Security
-- RLS org-based isolation is replaced by application-level user-based filtering.
-- The set_config('app.current_org_id') approach doesn't work reliably with
-- connection pooling (config is transaction-local but connections are reused),
-- causing all queries to return 0 rows from the web UI.

ALTER TABLE tracked_domains DISABLE ROW LEVEL SECURITY;
ALTER TABLE scrape_jobs DISABLE ROW LEVEL SECURITY;
ALTER TABLE scraped_data DISABLE ROW LEVEL SECURITY;
ALTER TABLE signals DISABLE ROW LEVEL SECURITY;
ALTER TABLE signal_executions DISABLE ROW LEVEL SECURITY;
