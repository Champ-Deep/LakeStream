-- Proxy health tracking (persistence layer — primary tracking is in Redis)
-- Provides queryable stats and survives Redis restarts.

CREATE TABLE IF NOT EXISTS proxy_health (
    url TEXT PRIMARY KEY,
    region TEXT,
    success_count INTEGER DEFAULT 0,
    fail_count INTEGER DEFAULT 0,
    avg_latency_ms INTEGER DEFAULT 0,
    last_failure_at TIMESTAMPTZ,
    consecutive_failures INTEGER DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_proxy_health_region ON proxy_health(region);
