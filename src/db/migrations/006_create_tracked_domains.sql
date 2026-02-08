-- Tracked domains for scheduled/automated scraping
CREATE TABLE IF NOT EXISTS tracked_domains (
    domain TEXT PRIMARY KEY,
    data_types TEXT[] NOT NULL
        DEFAULT ARRAY['blog_url','article','contact','tech_stack','resource','pricing'],
    scrape_frequency TEXT NOT NULL DEFAULT 'weekly'
        CHECK (scrape_frequency IN ('daily', 'weekly', 'biweekly', 'monthly')),
    max_pages INTEGER NOT NULL DEFAULT 100,
    template_id TEXT DEFAULT 'auto',
    webhook_url TEXT,
    is_active BOOLEAN DEFAULT true,
    last_auto_scraped_at TIMESTAMPTZ,
    next_scrape_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tracked_domains_next_scrape
    ON tracked_domains(next_scrape_at) WHERE is_active = true;
