CREATE TABLE IF NOT EXISTS domain_metadata (
    domain TEXT PRIMARY KEY,
    last_successful_strategy TEXT,
    block_count INTEGER DEFAULT 0,
    last_scraped_at TIMESTAMPTZ,
    success_rate DECIMAL(5, 2),
    avg_cost_usd DECIMAL(10, 6),
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
