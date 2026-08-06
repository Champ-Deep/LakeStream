-- Per-key API usage metering (v2.1 Phase 11).
-- One row per billable successful call; credits default to 1 (Context.dev-style).
-- Recording only — enforcement is opt-in via ENABLE_CREDIT_ENFORCEMENT.

CREATE TABLE IF NOT EXISTS api_usage (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    api_key_id UUID REFERENCES api_keys(id) ON DELETE SET NULL,
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    org_id UUID REFERENCES organizations(id) ON DELETE SET NULL,
    endpoint TEXT NOT NULL,
    credits INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_api_usage_user_period ON api_usage(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_api_usage_key_period ON api_usage(api_key_id, created_at);
