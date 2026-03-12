-- Migration 017: Disable Row-Level Security for Worker Access
-- Reason: Workers are background processes without user context
-- Data isolation is enforced at application layer via org_id filtering

-- Drop all existing RLS policies
DROP POLICY IF EXISTS org_isolation_policy ON tracked_domains;
DROP POLICY IF EXISTS org_insert_policy ON tracked_domains;
DROP POLICY IF EXISTS org_update_policy ON tracked_domains;
DROP POLICY IF EXISTS org_delete_policy ON tracked_domains;

DROP POLICY IF EXISTS org_isolation_policy ON scrape_jobs;
DROP POLICY IF EXISTS org_insert_policy ON scrape_jobs;
DROP POLICY IF EXISTS org_update_policy ON scrape_jobs;
DROP POLICY IF EXISTS org_delete_policy ON scrape_jobs;

DROP POLICY IF EXISTS org_isolation_policy ON scraped_data;
DROP POLICY IF EXISTS org_insert_policy ON scraped_data;
DROP POLICY IF EXISTS org_update_policy ON scraped_data;
DROP POLICY IF EXISTS org_delete_policy ON scraped_data;

-- Disable RLS on all tables
ALTER TABLE tracked_domains DISABLE ROW LEVEL SECURITY;
ALTER TABLE scrape_jobs DISABLE ROW LEVEL SECURITY;
ALTER TABLE scraped_data DISABLE ROW LEVEL SECURITY;
