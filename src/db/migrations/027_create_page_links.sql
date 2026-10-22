-- Migration 027: page_links — the site link graph edges (v2 knowledge graph)
--
-- One row per discovered (source_url -> target_url) same-domain link, captured
-- during crawl. Nodes are derived from scraped_data / page_content; this table
-- supplies the edges the graph view renders.

CREATE TABLE IF NOT EXISTS page_links (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id        UUID,
    user_id       UUID,
    domain        TEXT NOT NULL,
    source_url    TEXT NOT NULL,
    target_url    TEXT NOT NULL,
    discovered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (job_id, source_url, target_url)
);

CREATE INDEX IF NOT EXISTS idx_page_links_domain ON page_links(domain);
CREATE INDEX IF NOT EXISTS idx_page_links_user ON page_links(user_id);
