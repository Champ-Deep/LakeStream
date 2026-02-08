-- Migration 012: Add org_id to Existing Tables
-- Phase E: Multi-Tenant Foundation
-- This migration adds org_id foreign keys to all existing tenant-scoped tables

-- Add org_id columns to existing tables
ALTER TABLE tracked_domains ADD COLUMN org_id UUID REFERENCES organizations(id) ON DELETE CASCADE;
ALTER TABLE scrape_jobs ADD COLUMN org_id UUID REFERENCES organizations(id) ON DELETE CASCADE;
ALTER TABLE scraped_data ADD COLUMN org_id UUID REFERENCES organizations(id) ON DELETE CASCADE;

-- Create indexes for org_id columns (critical for query performance)
CREATE INDEX idx_tracked_domains_org ON tracked_domains(org_id);
CREATE INDEX idx_scrape_jobs_org ON scrape_jobs(org_id);
CREATE INDEX idx_scraped_data_org ON scraped_data(org_id);

-- Backfill: Create a default organization for existing data
-- This ensures zero downtime migration - existing data is assigned to a default org
DO $$
DECLARE
    default_org_id UUID;
BEGIN
    -- Create default organization
    INSERT INTO organizations (name, slug, plan)
    VALUES ('Default Organization', 'default', 'enterprise')
    RETURNING id INTO default_org_id;

    -- Backfill all existing records with default org_id
    UPDATE tracked_domains SET org_id = default_org_id WHERE org_id IS NULL;
    UPDATE scrape_jobs SET org_id = default_org_id WHERE org_id IS NULL;
    UPDATE scraped_data SET org_id = default_org_id WHERE org_id IS NULL;

    -- Make org_id NOT NULL after backfill (enforce referential integrity)
    ALTER TABLE tracked_domains ALTER COLUMN org_id SET NOT NULL;
    ALTER TABLE scrape_jobs ALTER COLUMN org_id SET NOT NULL;
    ALTER TABLE scraped_data ALTER COLUMN org_id SET NOT NULL;

    -- Create a default user for the default org (for API access)
    INSERT INTO users (org_id, email, password_hash, full_name, role, is_active)
    VALUES (
        default_org_id,
        'admin@lakeb2b.internal',
        '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewY5GyYzS4HullRK', -- 'changeme123'
        'Default Admin',
        'org_owner',
        TRUE
    );
END $$;
