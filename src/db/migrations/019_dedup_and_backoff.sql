-- Step 1a: Add failure backoff counter to tracked_domains
ALTER TABLE tracked_domains
    ADD COLUMN IF NOT EXISTS consecutive_failures INTEGER DEFAULT 0;

-- Step 3a: Deduplicate existing scraped_data before adding unique constraint
-- Keep the most recent record per (domain, url, data_type), delete older dupes
DELETE FROM scraped_data a
    USING scraped_data b
WHERE a.domain = b.domain
  AND a.url = b.url
  AND a.data_type = b.data_type
  AND a.url IS NOT NULL
  AND a.scraped_at < b.scraped_at;

-- Step 3a: Add unique partial index for deduplication
-- Partial: only where url IS NOT NULL (tech_stack records may have NULL url)
CREATE UNIQUE INDEX IF NOT EXISTS idx_scraped_data_dedup
    ON scraped_data (domain, url, data_type)
    WHERE url IS NOT NULL;
