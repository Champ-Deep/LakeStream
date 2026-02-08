-- Migration 013: Enable Row-Level Security Policies
-- Phase E: Multi-Tenant Foundation
-- This migration implements PostgreSQL Row-Level Security (RLS) for bulletproof data isolation
--
-- How RLS Works:
-- 1. Every authenticated request sets: SET app.current_org_id = '<user's org UUID>'
-- 2. PostgreSQL enforces policies AUTOMATICALLY - users CANNOT bypass them
-- 3. Defense-in-depth: Even buggy application code can't leak cross-org data

-- Enable RLS on all tenant-scoped tables
ALTER TABLE tracked_domains ENABLE ROW LEVEL SECURITY;
ALTER TABLE scrape_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE scraped_data ENABLE ROW LEVEL SECURITY;

-- ====================================================================================
-- READ POLICIES (SELECT)
-- Users can only see rows where org_id matches their session's current_org_id
-- ====================================================================================

CREATE POLICY org_isolation_policy ON tracked_domains
    FOR SELECT
    USING (org_id = current_setting('app.current_org_id', TRUE)::UUID);

CREATE POLICY org_isolation_policy ON scrape_jobs
    FOR SELECT
    USING (org_id = current_setting('app.current_org_id', TRUE)::UUID);

CREATE POLICY org_isolation_policy ON scraped_data
    FOR SELECT
    USING (org_id = current_setting('app.current_org_id', TRUE)::UUID);

-- ====================================================================================
-- INSERT POLICIES
-- Users can only insert rows with their own org_id
-- ====================================================================================

CREATE POLICY org_insert_policy ON tracked_domains
    FOR INSERT
    WITH CHECK (org_id = current_setting('app.current_org_id', TRUE)::UUID);

CREATE POLICY org_insert_policy ON scrape_jobs
    FOR INSERT
    WITH CHECK (org_id = current_setting('app.current_org_id', TRUE)::UUID);

CREATE POLICY org_insert_policy ON scraped_data
    FOR INSERT
    WITH CHECK (org_id = current_setting('app.current_org_id', TRUE)::UUID);

-- ====================================================================================
-- UPDATE POLICIES
-- Users can only update rows from their own org
-- ====================================================================================

CREATE POLICY org_update_policy ON tracked_domains
    FOR UPDATE
    USING (org_id = current_setting('app.current_org_id', TRUE)::UUID);

CREATE POLICY org_update_policy ON scrape_jobs
    FOR UPDATE
    USING (org_id = current_setting('app.current_org_id', TRUE)::UUID);

CREATE POLICY org_update_policy ON scraped_data
    FOR UPDATE
    USING (org_id = current_setting('app.current_org_id', TRUE)::UUID);

-- ====================================================================================
-- DELETE POLICIES
-- Users can only delete rows from their own org
-- ====================================================================================

CREATE POLICY org_delete_policy ON tracked_domains
    FOR DELETE
    USING (org_id = current_setting('app.current_org_id', TRUE)::UUID);

CREATE POLICY org_delete_policy ON scrape_jobs
    FOR DELETE
    USING (org_id = current_setting('app.current_org_id', TRUE)::UUID);

CREATE POLICY org_delete_policy ON scraped_data
    FOR DELETE
    USING (org_id = current_setting('app.current_org_id', TRUE)::UUID);

-- ====================================================================================
-- NOTES:
-- - current_setting('app.current_org_id', TRUE) uses TRUE flag for missing_ok
--   This prevents errors on public endpoints where session variable isn't set
-- - RLS policies apply to ALL queries, including JOINs and subqueries
-- - Superuser role bypasses RLS (use for migrations/admin tasks only)
-- - Test thoroughly: migration 012 created default org, all existing data assigned to it
-- ====================================================================================
