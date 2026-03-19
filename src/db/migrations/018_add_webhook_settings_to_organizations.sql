-- Migration 018: Add webhook settings columns to organizations table
-- Moves webhook configuration from browser localStorage to persistent DB storage.

ALTER TABLE organizations ADD COLUMN IF NOT EXISTS webhook_url TEXT DEFAULT '';
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS webhook_auto_send BOOLEAN DEFAULT FALSE;
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS webhook_include_metadata BOOLEAN DEFAULT FALSE;
