-- Signal Types: Pre-built library of intent signal definitions
CREATE TABLE signal_types (
    id TEXT PRIMARY KEY,  -- "job_change", "funding_round", "tech_stack_change"
    name TEXT NOT NULL,
    description TEXT,
    category TEXT CHECK (category IN ('people', 'company', 'technology', 'behavior')),
    config_schema JSONB NOT NULL,  -- JSON schema defining configuration options
    enabled BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_signal_types_category ON signal_types(category);
CREATE INDEX idx_signal_types_enabled ON signal_types(enabled) WHERE enabled = TRUE;

-- Signals: User-configured intent signals
CREATE TABLE signals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    is_active BOOLEAN DEFAULT TRUE,

    -- Signal configuration (JSON)
    trigger_config JSONB NOT NULL,  -- {type: "job_change", filters: {...}}
    condition_config JSONB,         -- {operator: "AND", conditions: [...]}
    action_config JSONB NOT NULL,   -- {type: "slack", webhook_url: "..."}

    -- Metadata
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    last_fired_at TIMESTAMPTZ,
    fire_count INTEGER DEFAULT 0,

    CONSTRAINT valid_trigger_config CHECK (jsonb_typeof(trigger_config) = 'object'),
    CONSTRAINT valid_action_config CHECK (jsonb_typeof(action_config) = 'object')
);

CREATE INDEX idx_signals_org ON signals(org_id);
CREATE INDEX idx_signals_active ON signals(is_active) WHERE is_active = TRUE;
CREATE INDEX idx_signals_created_by ON signals(created_by);

-- Signal Executions: History of signal firings
CREATE TABLE signal_executions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    signal_id UUID NOT NULL REFERENCES signals(id) ON DELETE CASCADE,
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,

    -- What triggered it
    trigger_data JSONB NOT NULL,  -- The data that matched (job posting, funding event, etc.)
    matched_at TIMESTAMPTZ DEFAULT NOW(),

    -- Action result
    action_type TEXT CHECK (action_type IN ('slack', 'webhook', 'email')),
    action_status TEXT CHECK (action_status IN ('success', 'failed', 'pending')),
    action_response JSONB,  -- Response from Slack API, webhook, etc.
    error_message TEXT,

    executed_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_signal_executions_signal ON signal_executions(signal_id);
CREATE INDEX idx_signal_executions_org ON signal_executions(org_id);
CREATE INDEX idx_signal_executions_date ON signal_executions(matched_at DESC);
CREATE INDEX idx_signal_executions_status ON signal_executions(action_status);

-- Enable RLS on signals tables
ALTER TABLE signals ENABLE ROW LEVEL SECURITY;
ALTER TABLE signal_executions ENABLE ROW LEVEL SECURITY;

-- RLS Policies for signals
CREATE POLICY signals_org_isolation ON signals
    FOR SELECT
    USING (org_id = current_setting('app.current_org_id', TRUE)::UUID);

CREATE POLICY signals_org_insert ON signals
    FOR INSERT
    WITH CHECK (org_id = current_setting('app.current_org_id', TRUE)::UUID);

CREATE POLICY signals_org_update ON signals
    FOR UPDATE
    USING (org_id = current_setting('app.current_org_id', TRUE)::UUID);

CREATE POLICY signals_org_delete ON signals
    FOR DELETE
    USING (org_id = current_setting('app.current_org_id', TRUE)::UUID);

-- RLS Policies for signal_executions
CREATE POLICY signal_executions_org_isolation ON signal_executions
    FOR SELECT
    USING (org_id = current_setting('app.current_org_id', TRUE)::UUID);

CREATE POLICY signal_executions_org_insert ON signal_executions
    FOR INSERT
    WITH CHECK (org_id = current_setting('app.current_org_id', TRUE)::UUID);

-- Seed signal_types with pre-built definitions
INSERT INTO signal_types (id, name, description, category, config_schema) VALUES
(
    'job_change',
    'Job Change Detected',
    'Fires when someone changes jobs or gets promoted',
    'people',
    '{
        "type": "object",
        "properties": {
            "job_title_contains": {"type": "string", "description": "Filter by job title keyword"},
            "seniority_level": {"type": "string", "enum": ["VP", "Director", "Manager", "IC"], "description": "Seniority level"},
            "company_domain": {"type": "string", "description": "Specific company domain"},
            "change_type": {"type": "string", "enum": ["new_hire", "promotion", "departure"], "description": "Type of change"}
        }
    }'::jsonb
),
(
    'funding_round',
    'Funding Round Announced',
    'Fires when a company raises funding',
    'company',
    '{
        "type": "object",
        "properties": {
            "round_type": {"type": "string", "enum": ["Seed", "Series A", "Series B", "Series C+"], "description": "Funding round stage"},
            "min_amount_usd": {"type": "number", "description": "Minimum funding amount in USD"},
            "investor_contains": {"type": "string", "description": "Filter by investor name"}
        }
    }'::jsonb
),
(
    'tech_stack_change',
    'Tech Stack Change',
    'Fires when a company adopts or removes a technology',
    'technology',
    '{
        "type": "object",
        "properties": {
            "technology": {"type": "string", "description": "Technology name (e.g., Salesforce, HubSpot)"},
            "change_type": {"type": "string", "enum": ["adopted", "removed"], "description": "Type of change"},
            "category": {"type": "string", "enum": ["CRM", "Analytics", "Marketing", "Sales", "Platform"], "description": "Technology category"}
        }
    }'::jsonb
),
(
    'pricing_change',
    'Pricing Page Change',
    'Fires when pricing information changes on a website',
    'behavior',
    '{
        "type": "object",
        "properties": {
            "price_increase_threshold": {"type": "number", "description": "Minimum price increase percentage"},
            "new_plan_added": {"type": "boolean", "description": "Alert on new pricing plan"},
            "plan_removed": {"type": "boolean", "description": "Alert on removed plan"}
        }
    }'::jsonb
),
(
    'hiring_spike',
    'Hiring Spike',
    'Fires when job posting volume increases significantly',
    'company',
    '{
        "type": "object",
        "properties": {
            "department": {"type": "string", "enum": ["Engineering", "Sales", "Marketing", "All"], "description": "Department filter"},
            "spike_threshold": {"type": "number", "description": "Multiplier (e.g., 5 = 5x normal volume)"},
            "timeframe_days": {"type": "number", "description": "Time window in days"}
        }
    }'::jsonb
);
