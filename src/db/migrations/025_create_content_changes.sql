-- Migration 025: content_changes — page-level change log (v2)
--
-- One row per detected content change (old_hash != new_hash on re-scrape),
-- driving the change-monitoring UI and webhook notifications. Independent of
-- scraped_data; keyed per-user like page_content.

CREATE TABLE IF NOT EXISTS content_changes (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID,
    domain      TEXT NOT NULL,
    url         TEXT NOT NULL,
    old_hash    TEXT,
    new_hash    TEXT NOT NULL,
    changed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_content_changes_domain ON content_changes(domain, changed_at DESC);
CREATE INDEX IF NOT EXISTS idx_content_changes_user ON content_changes(user_id, changed_at DESC);
