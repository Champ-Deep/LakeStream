-- Migration 016: Add user-level data ownership and admin role
-- Adds user_id to scrape_jobs and scraped_data so each user only sees their own data.
-- Adds is_admin flag: admins see ALL data across all users/orgs.

-- 1. Add user_id columns
ALTER TABLE scrape_jobs ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE scraped_data ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_scrape_jobs_user ON scrape_jobs(user_id);
CREATE INDEX IF NOT EXISTS idx_scraped_data_user ON scraped_data(user_id);

-- 2. Add is_admin flag to users (only admins see all data)
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN DEFAULT FALSE;

-- 3. Make the default admin user an admin
UPDATE users SET is_admin = TRUE WHERE email = 'admin@lakeb2b.internal';

-- 4. Backfill: assign existing data to the default admin user
DO $$
DECLARE
    admin_uid UUID;
BEGIN
    SELECT id INTO admin_uid FROM users WHERE email = 'admin@lakeb2b.internal' LIMIT 1;
    IF admin_uid IS NOT NULL THEN
        UPDATE scrape_jobs SET user_id = admin_uid WHERE user_id IS NULL;
        UPDATE scraped_data SET user_id = admin_uid WHERE user_id IS NULL;
    END IF;
END $$;
