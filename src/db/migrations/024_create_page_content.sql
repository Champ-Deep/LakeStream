-- Migration 024: page_content — durable per-URL content store (v2)
--
-- Powers markdown-fidelity output, scrape caching (skip unchanged pages), and
-- change monitoring. Deliberately SEPARATE from scraped_data: scraped_data
-- 'page' rows are purged after 7 days by cleanup_stale_data_cron, whereas this
-- cache must persist across jobs to support hash diffing. Keyed per-user to
-- match current application-level isolation; org_id carried for future scoping.

CREATE TABLE IF NOT EXISTS page_content (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id        UUID,
    user_id       UUID,
    domain        TEXT NOT NULL,
    url           TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    markdown      TEXT,
    raw_html      TEXT,
    title         TEXT,
    screenshot_path TEXT,
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- NULLS NOT DISTINCT so shared/unauthenticated rows (user_id IS NULL) still
    -- dedupe by url. Requires PostgreSQL 15+ (we run 16).
    UNIQUE NULLS NOT DISTINCT (user_id, url)
);

CREATE INDEX IF NOT EXISTS idx_page_content_domain ON page_content(domain);
CREATE INDEX IF NOT EXISTS idx_page_content_hash ON page_content(content_hash);
CREATE INDEX IF NOT EXISTS idx_page_content_user ON page_content(user_id);
