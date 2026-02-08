-- Migration 011: Create Organizations, Teams, Users, and API Keys tables
-- Phase E: Multi-Tenant Foundation
-- This migration establishes the organizational hierarchy for multi-tenant data segregation

-- Organizations table (top-level tenant)
CREATE TABLE organizations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    slug TEXT UNIQUE NOT NULL, -- URL-friendly identifier: "acme-corp"
    plan TEXT CHECK (plan IN ('free', 'pro', 'enterprise')) DEFAULT 'free',
    max_users INTEGER DEFAULT 5,
    max_domains INTEGER DEFAULT 10,
    max_signals_per_month INTEGER DEFAULT 1000,
    billing_email TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Teams within organizations (optional grouping for larger orgs)
CREATE TABLE teams (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(org_id, name) -- Team names must be unique within an org
);

CREATE INDEX idx_teams_org ON teams(org_id);

-- Users table
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    team_id UUID REFERENCES teams(id) ON DELETE SET NULL, -- Optional team membership
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL, -- bcrypt hash
    full_name TEXT,
    role TEXT CHECK (role IN ('org_owner', 'team_admin', 'member')) DEFAULT 'member',
    is_active BOOLEAN DEFAULT TRUE,
    last_login_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_users_org ON users(org_id);
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_team ON users(team_id);

-- API keys for programmatic access
CREATE TABLE api_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    key_hash TEXT NOT NULL, -- SHA256 hash of the API key
    name TEXT NOT NULL, -- User-friendly name: "Production API Key"
    last_used_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_api_keys_hash ON api_keys(key_hash);
CREATE INDEX idx_api_keys_user ON api_keys(user_id);
CREATE INDEX idx_api_keys_org ON api_keys(org_id);
