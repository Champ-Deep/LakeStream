-- Tech Detect batch runs: tech_detect_runs, tech_detect_results

-- Parent run for a CSV/URL-list tech detection batch
CREATE TABLE IF NOT EXISTS tech_detect_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL,
    user_id UUID,
    source TEXT NOT NULL DEFAULT 'csv'
        CHECK (source IN ('csv', 'list')),
    total INT NOT NULL DEFAULT 0,
    done_count INT NOT NULL DEFAULT 0,
    ok_count INT NOT NULL DEFAULT 0,
    blocked_count INT NOT NULL DEFAULT 0,
    failed_count INT NOT NULL DEFAULT 0,
    skipped_count INT NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled')),
    options JSONB NOT NULL DEFAULT '{}'::jsonb,
    client_token TEXT,
    error_message TEXT,
    heartbeat_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_tdr_user_created
    ON tech_detect_runs(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tdr_org_created
    ON tech_detect_runs(org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tdr_status ON tech_detect_runs(status);

-- Idempotent submits: a double-clicked form returns the existing run
CREATE UNIQUE INDEX IF NOT EXISTS idx_tdr_client_token
    ON tech_detect_runs(user_id, client_token)
    WHERE client_token IS NOT NULL;

-- One row per URL, written as each URL completes so partial results survive
CREATE TABLE IF NOT EXISTS tech_detect_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES tech_detect_runs(id) ON DELETE CASCADE,
    position INT NOT NULL DEFAULT 0,
    input_url TEXT NOT NULL,
    final_url TEXT,
    domain TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'ok', 'blocked', 'skipped', 'failed')),
    http_status INT,
    error TEXT,
    detect JSONB NOT NULL DEFAULT '{}'::jsonb,
    duration_ms INT NOT NULL DEFAULT 0,
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_tdres_run ON tech_detect_results(run_id, position);
CREATE INDEX IF NOT EXISTS idx_tdres_domain ON tech_detect_results(domain);
