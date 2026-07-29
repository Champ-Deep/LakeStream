-- Migration 028: map local users to Clerk identities (v2 auth migration)
--
-- Clerk owns identity; we keep a local users row per Clerk user (lazy-created on
-- first authenticated request) so all existing per-user data scoping keeps
-- working. Nullable + UNIQUE so legacy password users are unaffected.

ALTER TABLE users ADD COLUMN IF NOT EXISTS clerk_user_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_clerk_user_id
    ON users(clerk_user_id) WHERE clerk_user_id IS NOT NULL;
