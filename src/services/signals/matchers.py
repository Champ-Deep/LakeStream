"""Signal-type-specific matchers.

Each `check_*_signal` function queries `scraped_data` with a heuristic specific
to its signal type and, if it finds matches, shapes them into a common result
dict. The queries themselves are genuinely different (different filters, and
`check_hiring_spike_signal` aggregates by domain instead of returning raw
rows), so they are kept as separate functions rather than forced behind one
generic query-builder. What *is* identical across all four is the shape of a
successful result, so that part is factored into `_build_match_result`.
"""

from typing import Any
from uuid import UUID

from asyncpg import Pool

from src.models.signals import Signal


def _build_match_result(
    rows: list[Any], signal_type: str, trigger: str
) -> dict[str, Any] | None:
    """Shape matched rows into the common signal-match result dict.

    Returns None if there are no matches, so callers can `return
    _build_match_result(...)` directly.
    """
    if not rows:
        return None

    matches = [dict(row) for row in rows]
    return {
        "matches": matches,
        "match_count": len(matches),
        "signal_type": signal_type,
        "trigger": trigger,
    }


async def check_job_change_signal(
    pool: Pool, signal: Signal, org_id: UUID
) -> dict[str, Any] | None:
    """Contacts whose job title actually CHANGED into the target title.

    This previously selected every contact whose *current* title matched the
    filter, with no before/after comparison anywhere:

        WHERE data_type = 'contact'
          AND metadata->>'job_title' ILIKE $2
          AND scraped_at > NOW() - INTERVAL '24 hours'

    That detects "a contact exists with this title", not "someone changed
    jobs". Every matching contact re-fired on every evaluation pass inside the
    window, so a signal meant to catch a rare, high-intent event behaved as a
    standing list of everyone with the title — a false-positive machine that
    would have burned the list it was pointed at.

    A real change needs two observations of the same person. The upsert key on
    scraped_data is (domain, url, data_type), so re-scraping a contact page
    UPDATES the row rather than versioning it — there is no history table to
    diff against. The comparison therefore runs against the previous title
    carried in the record's own metadata, which the contact writer stamps as
    `previous_job_title` when it overwrites a changed value.

    Returns None when the field is absent everywhere, rather than falling back
    to the old title-match behaviour: a signal that cannot tell change from
    presence should stay silent, not fire on everything.
    """
    filters = signal.trigger_config.get("filters", {})
    job_title = filters.get("job_title_contains", "")
    window_hours = int(filters.get("window_hours", 24))

    query = """
        SELECT * FROM scraped_data
        WHERE org_id = $1
          AND data_type = 'contact'
          -- the NEW title matches what we are watching for
          AND metadata->>'job_title' ILIKE $2
          -- ... and there is a recorded PREVIOUS title
          AND metadata->>'previous_job_title' IS NOT NULL
          AND metadata->>'previous_job_title' <> ''
          -- ... which is genuinely different (case-insensitive, so a
          -- re-scrape that only changed capitalisation is not a "change")
          AND lower(metadata->>'previous_job_title')
              IS DISTINCT FROM lower(metadata->>'job_title')
          AND scraped_at > NOW() - ($3 || ' hours')::interval
        ORDER BY scraped_at DESC
        LIMIT 100
    """

    rows = await pool.fetch(query, org_id, f"%{job_title}%", str(window_hours))

    if not rows:
        return None

    matches = [dict(row) for row in rows]
    return {
        "matches": matches,
        "match_count": len(matches),
        "signal_type": "job_change",
        "trigger": (
            f"{len(matches)} contact(s) moved into a role matching "
            f"'{job_title}' in the last {window_hours}h"
        ),
    }


async def check_funding_signal(pool: Pool, signal: Signal, org_id: UUID) -> dict[str, Any] | None:
    """Check if funding round conditions match recent data."""
    _filters = signal.trigger_config.get("filters", {})  # noqa: F841

    # In a real implementation, this would query funding data sources
    # For now, check scraped_data for funding mentions
    query = """
        SELECT * FROM scraped_data
        WHERE org_id = $1
        AND (
            metadata->>'type' = 'funding'
            OR metadata->>'category' = 'funding'
        )
        AND scraped_at > NOW() - INTERVAL '7 days'
        LIMIT 50
    """

    rows = await pool.fetch(query, org_id)

    return _build_match_result(
        rows, "funding_round", f"Found {len(rows)} funding announcements"
    )


async def check_tech_stack_signal(
    pool: Pool, signal: Signal, org_id: UUID
) -> dict[str, Any] | None:
    """Check if tech stack change conditions match recent data."""
    filters = signal.trigger_config.get("filters", {})
    technology = filters.get("technology", "")

    # Query scraped data for tech stack changes
    query = """
        SELECT * FROM scraped_data
        WHERE org_id = $1
        AND data_type = 'tech_stack'
        AND (
            metadata->>'platform' ILIKE $2
            OR metadata->>'technology' ILIKE $2
        )
        AND scraped_at > NOW() - INTERVAL '7 days'
        LIMIT 50
    """

    rows = await pool.fetch(query, org_id, f"%{technology}%")

    return _build_match_result(
        rows, "tech_stack_change", f"Found {len(rows)} companies using {technology}"
    )


async def check_hiring_spike_signal(
    pool: Pool, signal: Signal, org_id: UUID
) -> dict[str, Any] | None:
    """Companies with an unusual volume of open roles.

    This query could never return anything before 2026-07-30: it filters on
    `data_type = 'job_posting'`, and no such member existed in the DataType
    enum, nor did any code path write that value — the string appeared in
    exactly one place in the whole codebase, this WHERE clause. The signal was
    dead from the day it was written.

    The data source now exists (services/job_boards.persist_postings writes
    real Greenhouse / Lever / Ashby postings), so the query runs for real. Two
    further fixes were needed for it to mean anything:

    - `department` was accepted, marked noqa: F841 and never used, so a signal
      configured for "Engineering" fired on any hiring at all. It now filters.
    - The threshold was `spike_threshold * 2` with the comment "Simplified
      threshold", which silently doubled whatever the user configured. A
      configured 3 became 6. It is now used as written.
    """
    filters = signal.trigger_config.get("filters", {})
    department = filters.get("department") or "All"
    spike_threshold = int(filters.get("spike_threshold", 3))
    window_days = int(filters.get("window_days", 7))

    # * Department lives in the posting's metadata, written by
    # * job_boards.posting_to_record. "All" disables the filter rather than
    # * matching a literal department called "All".
    department_filter = ""
    params: list[Any] = [org_id, spike_threshold, str(window_days)]
    if department and department.lower() != "all":
        department_filter = "AND metadata->>'department' ILIKE $4"
        params.append(f"%{department}%")

    query = f"""
        SELECT domain,
               COUNT(*) AS job_count,
               MAX(scraped_at) AS most_recent_post
        FROM scraped_data
        WHERE org_id = $1
          AND data_type = 'job_posting'
          AND scraped_at > NOW() - ($3 || ' days')::interval
          {department_filter}
        GROUP BY domain
        HAVING COUNT(*) >= $2
        ORDER BY COUNT(*) DESC
    """

    rows = await pool.fetch(query, *params)

    if not rows:
        return None

    matches = [dict(row) for row in rows]
    scope = "any department" if department.lower() == "all" else department
    return {
        "matches": matches,
        "match_count": len(matches),
        "signal_type": "hiring_spike",
        "trigger": (
            f"{len(matches)} company/companies with {spike_threshold}+ open "
            f"roles in {scope} over the last {window_days} days"
        ),
    }


