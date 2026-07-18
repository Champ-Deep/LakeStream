"""Queries for company_profiles — the firmographic enrichment cache (v2.1).

See migration 029. Keyed UNIQUE NULLS NOT DISTINCT (user_id, domain).
"""

import json
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
) -> asyncpg.Record:
    """Insert or refresh the profile for (user_id, domain); returns the row."""
    return await pool.fetchrow(
        """
        INSERT INTO company_profiles (
            domain, name, description, industry, naics_code, sic_code,
            employee_range, revenue_range, logo_url, location, socials,
            source, raw, org_id, user_id, fetched_at, updated_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb,
                $12, $13::jsonb, $14, $15, NOW(), NOW())
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
