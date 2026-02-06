CREATE INDEX IF NOT EXISTS idx_scraped_data_domain_type
    ON scraped_data(domain, data_type);

CREATE INDEX IF NOT EXISTS idx_scrape_jobs_domain_status
    ON scrape_jobs(domain, status);

CREATE INDEX IF NOT EXISTS idx_scraped_data_metadata
    ON scraped_data USING GIN(metadata);

CREATE INDEX IF NOT EXISTS idx_scrape_jobs_active
    ON scrape_jobs(domain, created_at DESC)
    WHERE status IN ('pending', 'running');
