-- Migration 032: map local organizations to Clerk Organizations (v2 auth migration)
--
-- Clerk Organizations become the identity source of truth for org membership;
-- the local organizations table stays the tenancy anchor (every table FKs
-- org_id). Nullable + partial UNIQUE mirrors 028's users.clerk_user_id, so
-- orgs that never touch Clerk are unaffected.

ALTER TABLE organizations ADD COLUMN IF NOT EXISTS clerk_org_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_organizations_clerk_org_id
    ON organizations(clerk_org_id) WHERE clerk_org_id IS NOT NULL;
