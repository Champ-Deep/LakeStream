CREATE TABLE IF NOT EXISTS scraped_data (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    job_id UUID REFERENCES scrape_jobs(id) ON DELETE CASCADE,
    domain TEXT NOT NULL,
    data_type TEXT NOT NULL,
    url TEXT,
    title TEXT,
    published_date DATE,
    metadata JSONB DEFAULT '{}',
    scraped_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_scraped_data_job_id ON scraped_data(job_id);
CREATE INDEX IF NOT EXISTS idx_scraped_data_domain ON scraped_data(domain);
CREATE INDEX IF NOT EXISTS idx_scraped_data_data_type ON scraped_data(data_type);
CREATE INDEX IF NOT EXISTS idx_scraped_data_url ON scraped_data(url);
