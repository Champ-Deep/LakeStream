-- Company/brand enrichment cache (v2.1 Phase 10).
-- One durable firmographic profile per (user_id, domain); invisible to the
-- 7-day scraped_data cleanup cron, same pattern as page_content (024).

CREATE TABLE IF NOT EXISTS company_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID REFERENCES organizations(id) ON DELETE SET NULL,
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    domain TEXT NOT NULL,
    name TEXT,
    description TEXT,
    industry TEXT,
    naics_code TEXT,
    sic_code TEXT,
    employee_range TEXT,
    revenue_range TEXT,
    logo_url TEXT,
    location TEXT,
    socials JSONB NOT NULL DEFAULT '{}'::jsonb,
    source TEXT NOT NULL DEFAULT 'scrape',
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE NULLS NOT DISTINCT (user_id, domain)
);

CREATE INDEX IF NOT EXISTS idx_company_profiles_domain ON company_profiles(domain);
CREATE INDEX IF NOT EXISTS idx_company_profiles_org ON company_profiles(org_id);
