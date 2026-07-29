-- Domain-level tech intel (hosting/email-hosting/CDN via DNS, SSL cert via TLS)
-- on the existing company_profiles cache (migration 029). Additive.

ALTER TABLE company_profiles
    ADD COLUMN IF NOT EXISTS web_hosting_provider TEXT,
    ADD COLUMN IF NOT EXISTS email_hosting_provider TEXT,
    ADD COLUMN IF NOT EXISTS cdn_providers JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS ssl_issuer TEXT,
    ADD COLUMN IF NOT EXISTS ssl_valid_from TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS ssl_valid_to TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS ssl_days_until_expiry INTEGER,
    ADD COLUMN IF NOT EXISTS ssl_protocol TEXT,
    ADD COLUMN IF NOT EXISTS ssl_san_domains JSONB NOT NULL DEFAULT '[]'::jsonb;
