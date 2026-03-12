-- Migration 016: Add proxy_url to organizations table
-- Allows per-org proxy configuration via the settings UI.
-- When set, enables Tier 3 (Playwright + Proxy) escalation.

ALTER TABLE organizations ADD COLUMN IF NOT EXISTS proxy_url TEXT DEFAULT '';
