-- Migration 022: Add LLM settings to organizations + domain scrape counters
-- Enables BYOK OpenRouter API key and fixes dead success_rate metric

-- Organization-level LLM configuration (BYOK)
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS openrouter_api_key TEXT DEFAULT '';
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS llm_model TEXT DEFAULT 'anthropic/claude-3.5-haiku';

-- Domain scrape counters for computing real success_rate
ALTER TABLE domain_metadata ADD COLUMN IF NOT EXISTS total_scrapes INTEGER DEFAULT 0;
ALTER TABLE domain_metadata ADD COLUMN IF NOT EXISTS successful_scrapes INTEGER DEFAULT 0;
