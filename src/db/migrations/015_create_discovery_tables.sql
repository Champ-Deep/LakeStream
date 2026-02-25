-- Discovery pipeline tables: discovery_jobs, discovery_job_domains, tracked_searches

-- Parent job for search-to-scrape pipeline
CREATE TABLE IF NOT EXISTS discovery_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL,
    query TEXT NOT NULL,
    search_mode TEXT NOT NULL DEFAULT 'auto',
    search_pages INT NOT NULL DEFAULT 3,
    results_per_page INT NOT NULL DEFAULT 10,
    data_types TEXT[] NOT NULL,
    template_id TEXT NOT NULL DEFAULT 'generic',
    max_pages_per_domain INT NOT NULL DEFAULT 50,
    status TEXT NOT NULL DEFAULT 'searching'
        CHECK (status IN ('searching', 'scraping', 'completed', 'failed')),
    domains_found INT NOT NULL DEFAULT 0,
    domains_skipped INT NOT NULL DEFAULT 0,
    search_results JSONB,
    error_message TEXT,
    total_cost_usd NUMERIC(10,6) NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_discovery_jobs_org ON discovery_jobs(org_id);
CREATE INDEX IF NOT EXISTS idx_discovery_jobs_status ON discovery_jobs(status);

-- Links a discovery job to the scrape jobs it spawned
CREATE TABLE IF NOT EXISTS discovery_job_domains (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    discovery_id UUID NOT NULL REFERENCES discovery_jobs(id),
    domain TEXT NOT NULL,
    scrape_job_id UUID REFERENCES scrape_jobs(id),
    source_url TEXT NOT NULL,
    source_title TEXT,
    source_snippet TEXT,
    source_score FLOAT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'scraping', 'completed', 'skipped', 'failed')),
    skip_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_djd_discovery ON discovery_job_domains(discovery_id);
CREATE INDEX IF NOT EXISTS idx_djd_domain ON discovery_job_domains(domain);

-- Recurring search-to-scrape schedules
CREATE TABLE IF NOT EXISTS tracked_searches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL,
    query TEXT NOT NULL,
    search_mode TEXT NOT NULL DEFAULT 'auto',
    search_pages INT NOT NULL DEFAULT 2,
    results_per_page INT NOT NULL DEFAULT 10,
    data_types TEXT[] NOT NULL,
    template_id TEXT NOT NULL DEFAULT 'generic',
    max_pages_per_domain INT NOT NULL DEFAULT 50,
    scrape_frequency TEXT NOT NULL DEFAULT 'weekly'
        CHECK (scrape_frequency IN ('daily', 'weekly', 'biweekly', 'monthly')),
    webhook_url TEXT,
    is_active BOOLEAN NOT NULL DEFAULT true,
    last_run_at TIMESTAMPTZ,
    next_run_at TIMESTAMPTZ,
    total_runs INT NOT NULL DEFAULT 0,
    total_domains_discovered INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tracked_searches_next
    ON tracked_searches(next_run_at) WHERE is_active = true;
