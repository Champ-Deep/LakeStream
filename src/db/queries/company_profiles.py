"""Queries for company_profiles — the firmographic + tech-intel cache (v2.1).

See migrations 029 (firmographics) and 031 (DNS/SSL tech intel). Keyed
UNIQUE NULLS NOT DISTINCT (user_id, domain).
"""

import json
from datetime import datetime
from uuid import UUID

import asyncpg


async def upsert_company_profile(
    pool: asyncpg.Pool,
    *,
    domain: str,
    name: str | None = None,
    description: str | None = None,
    industry: str | None = None,
    naics_code: str | None = None,
    sic_code: str | None = None,
    employee_range: str | None = None,
    revenue_range: str | None = None,
    logo_url: str | None = None,
    location: str | None = None,
    socials: dict | None = None,
    source: str = "scrape",
    raw: dict | None = None,
    org_id: UUID | None = None,
    user_id: UUID | None = None,
    web_hosting_provider: str | None = None,
    email_hosting_provider: str | None = None,
    cdn_providers: list[str] | None = None,
    ssl_issuer: str | None = None,
    ssl_valid_from: datetime | None = None,
    ssl_valid_to: datetime | None = None,
    ssl_days_until_expiry: int | None = None,
    ssl_protocol: str | None = None,
    ssl_san_domains: list[str] | None = None,
) -> asyncpg.Record:
    """Insert or refresh the profile for (user_id, domain); returns the row."""
    return await pool.fetchrow(
        """
        INSERT INTO company_profiles (
            domain, name, description, industry, naics_code, sic_code,
            employee_range, revenue_range, logo_url, location, socials,
            source, raw, org_id, user_id,
            web_hosting_provider, email_hosting_provider, cdn_providers,
            ssl_issuer, ssl_valid_from, ssl_valid_to, ssl_days_until_expiry,
            ssl_protocol, ssl_san_domains,
            fetched_at, updated_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb,
                $12, $13::jsonb, $14, $15,
                $16, $17, $18::jsonb,
                $19, $20, $21, $22,
                $23, $24::jsonb,
                NOW(), NOW())
        ON CONFLICT (user_id, domain) DO UPDATE SET
            name = COALESCE(EXCLUDED.name, company_profiles.name),
            description = COALESCE(EXCLUDED.description, company_profiles.description),
            industry = COALESCE(EXCLUDED.industry, company_profiles.industry),
            naics_code = COALESCE(EXCLUDED.naics_code, company_profiles.naics_code),
            sic_code = COALESCE(EXCLUDED.sic_code, company_profiles.sic_code),
            employee_range = COALESCE(EXCLUDED.employee_range, company_profiles.employee_range),
            revenue_range = COALESCE(EXCLUDED.revenue_range, company_profiles.revenue_range),
            logo_url = COALESCE(EXCLUDED.logo_url, company_profiles.logo_url),
            location = COALESCE(EXCLUDED.location, company_profiles.location),
            socials = EXCLUDED.socials,
            source = EXCLUDED.source,
            raw = EXCLUDED.raw,
            org_id = COALESCE(EXCLUDED.org_id, company_profiles.org_id),
            web_hosting_provider = COALESCE(EXCLUDED.web_hosting_provider, company_profiles.web_hosting_provider),
            email_hosting_provider = COALESCE(EXCLUDED.email_hosting_provider, company_profiles.email_hosting_provider),
            cdn_providers = CASE WHEN EXCLUDED.cdn_providers = '[]'::jsonb
                THEN company_profiles.cdn_providers ELSE EXCLUDED.cdn_providers END,
            ssl_issuer = COALESCE(EXCLUDED.ssl_issuer, company_profiles.ssl_issuer),
            ssl_valid_from = COALESCE(EXCLUDED.ssl_valid_from, company_profiles.ssl_valid_from),
            ssl_valid_to = COALESCE(EXCLUDED.ssl_valid_to, company_profiles.ssl_valid_to),
            ssl_days_until_expiry = COALESCE(EXCLUDED.ssl_days_until_expiry, company_profiles.ssl_days_until_expiry),
            ssl_protocol = COALESCE(EXCLUDED.ssl_protocol, company_profiles.ssl_protocol),
            ssl_san_domains = CASE WHEN EXCLUDED.ssl_san_domains = '[]'::jsonb
                THEN company_profiles.ssl_san_domains ELSE EXCLUDED.ssl_san_domains END,
            fetched_at = NOW(),
            updated_at = NOW()
        RETURNING *
        """,
        domain,
        name,
        description,
        industry,
        naics_code,
        sic_code,
        employee_range,
        revenue_range,
        logo_url,
        location,
        json.dumps(socials or {}),
        source,
        json.dumps(raw or {}),
        org_id,
        user_id,
        web_hosting_provider,
        email_hosting_provider,
        json.dumps(cdn_providers or []),
        ssl_issuer,
        ssl_valid_from,
        ssl_valid_to,
        ssl_days_until_expiry,
        ssl_protocol,
        json.dumps(ssl_san_domains or []),
    )


async def get_by_domain(
    pool: asyncpg.Pool, domain: str, user_id: UUID | None = None
) -> asyncpg.Record | None:
    """Return the cached profile for a domain scoped to a user."""
    return await pool.fetchrow(
        "SELECT * FROM company_profiles "
        "WHERE domain = $1 AND user_id IS NOT DISTINCT FROM $2",
        domain,
        user_id,
    )
