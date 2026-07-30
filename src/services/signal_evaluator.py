"""Signal evaluation engine for intent data platform.

This module contains the core logic for evaluating intent signals:
1. Check if signal conditions are met based on scraped data
2. Execute actions when signals fire (Slack, webhook, email)
3. Log execution results
"""

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
import redis.asyncio as redis
import structlog
from asyncpg import Pool

from src.config.settings import get_settings
from src.db.pool import get_pool
from src.db.queries.signals import (
    create_signal_execution,
    get_active_signals,
    increment_signal_fire_count,
)
from src.models.signals import Signal

log = structlog.get_logger()


# ============================================================================
# Main Evaluation Loop
# ============================================================================


async def evaluate_signals_for_org(org_id: UUID) -> int:
    """Evaluate all active signals for an organization.

    Returns:
        Number of signals that fired
    """
    pool = await get_pool()

    # Set RLS context
    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.current_org_id = '{org_id}'")

        signals = await get_active_signals(pool, org_id)
        fired_count = 0

        for signal in signals:
            try:
                matched_data = await evaluate_signal(pool, signal, org_id)
                if matched_data:
                    await execute_signal_action(pool, signal, matched_data)
                    fired_count += 1
            except Exception as e:
                log.error(
                    "signal_evaluation_error",
                    signal_id=str(signal.id),
                    org_id=str(org_id),
                    error=str(e),
                )

        return fired_count


async def evaluate_signal(pool: Pool, signal: Signal, org_id: UUID) -> dict[str, Any] | None:
    """Evaluate a single signal to check if conditions are met.

    Returns:
        Matched data if signal should fire, None otherwise
    """
    trigger_type = signal.trigger_config.get("type")

    if trigger_type == "job_change":
        return await check_job_change_signal(pool, signal, org_id)
    elif trigger_type == "funding_round":
        return await check_funding_signal(pool, signal, org_id)
    elif trigger_type == "tech_stack_change":
        return await check_tech_stack_signal(pool, signal, org_id)
    elif trigger_type == "hiring_spike":
        return await check_hiring_spike_signal(pool, signal, org_id)
    else:
        log.warning("unknown_signal_type", signal_type=trigger_type, signal_id=str(signal.id))
        return None


# ============================================================================
# Signal Type Evaluators
# ============================================================================


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

    if rows:
        matches = [dict(row) for row in rows]
        return {
            "matches": matches,
            "match_count": len(matches),
            "signal_type": "funding_round",
            "trigger": f"Found {len(matches)} funding announcements",
        }

    return None


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

    if rows:
        matches = [dict(row) for row in rows]
        return {
            "matches": matches,
            "match_count": len(matches),
            "signal_type": "tech_stack_change",
            "trigger": f"Found {len(matches)} companies using {technology}",
        }

    return None


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


# ============================================================================
# Action Execution
# ============================================================================


async def execute_signal_action(pool: Pool, signal: Signal, matched_data: dict[str, Any]) -> None:
    """Execute the configured action when a signal fires."""
    action_config = signal.action_config
    action_type = action_config.get("type")

    try:
        if action_type == "slack":
            await send_slack_notification(signal, matched_data, action_config)
            status = "success"
            response = {"message": "Slack notification sent"}
            error_msg = None
        elif action_type == "webhook":
            response = await send_webhook_notification(signal, matched_data, action_config)
            status = "success"
            error_msg = None
        elif action_type == "email":
            await send_email_notification(signal, matched_data, action_config)
            status = "success"
            response = {"message": "Email sent"}
            error_msg = None
        elif action_type == "champiq":
            # * The action that closes the loop from detection to outreach.
            # * slack/webhook/email all end at a human; this one publishes the
            # * canonical `signal.matched` event onto ChampIQ's bus, where a
            # * trigger.event DAG can enrich, suppression-check and enrol the
            # * prospect without anyone reading a notification first.
            response = await publish_signal_to_champiq(signal, matched_data, action_config)
            status = "success"
            error_msg = None
        else:
            log.warning("unknown_action_type", action_type=action_type)
            status = "failed"
            response = None
            error_msg = f"Unknown action type: {action_type}"

        # Log execution
        await create_signal_execution(
            pool,
            signal_id=signal.id,
            org_id=signal.org_id,
            trigger_data=matched_data,
            action_type=action_type,
            action_status=status,
            action_response=response,
            error_message=error_msg,
        )

        # Update signal stats
        await increment_signal_fire_count(pool, signal.id)

        log.info(
            "signal_fired",
            signal_id=str(signal.id),
            signal_name=signal.name,
            action_type=action_type,
            match_count=matched_data.get("match_count", 0),
        )

        # Publish to Redis pub/sub for real-time streaming (Phase G)
        await publish_signal_event(signal, matched_data)

    except Exception as e:
        log.error(
            "signal_action_error",
            signal_id=str(signal.id),
            action_type=action_type,
            error=str(e),
        )

        # Log failed execution
        await create_signal_execution(
            pool,
            signal_id=signal.id,
            org_id=signal.org_id,
            trigger_data=matched_data,
            action_type=action_type,
            action_status="failed",
            action_response=None,
            error_message=str(e),
        )


async def send_slack_notification(
    signal: Signal, matched_data: dict[str, Any], action_config: dict[str, Any]
) -> None:
    """Send Slack notification via webhook."""
    webhook_url = action_config.get("webhook_url")
    if not webhook_url:
        raise ValueError("Slack webhook URL not configured")

    # Build Slack message
    message = {
        "text": f"🔔 Intent Signal Fired: {signal.name}",
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"🔔 {signal.name}"},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Signal:* {signal.name}\n"
                        f"*Matches:* {matched_data.get('match_count', 0)}\n"
                        f"*Trigger:* {matched_data.get('trigger', 'N/A')}"
                    ),
                },
            },
        ],
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(webhook_url, json=message, timeout=10.0)
        response.raise_for_status()


async def send_webhook_notification(
    signal: Signal, matched_data: dict[str, Any], action_config: dict[str, Any]
) -> dict[str, Any]:
    """Send webhook notification."""
    webhook_url = action_config.get("webhook_url")
    if not webhook_url:
        raise ValueError("Webhook URL not configured")

    payload = {
        "signal_id": str(signal.id),
        "signal_name": signal.name,
        "signal_type": matched_data.get("signal_type"),
        "matched_data": matched_data,
        "timestamp": datetime.now(UTC).isoformat(),
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(webhook_url, json=payload, timeout=10.0)
        response.raise_for_status()
        return {"status_code": response.status_code, "response": response.text}


async def send_email_notification(
    signal: Signal, matched_data: dict[str, Any], action_config: dict[str, Any]
) -> None:
    """Send email notification via ChampMail engine API."""
    email_recipients = action_config.get("email_recipients", [])
    if not email_recipients:
        raise ValueError("Email recipients not configured")

    settings = get_settings()
    if not settings.mail_engine_enabled:
        raise RuntimeError(
            "Email notifications are not enabled. "
            "Set MAIL_ENGINE_ENABLED=true and MAIL_ENGINE_API_KEY."
        )

    subject = f"Intent Signal Fired: {signal.name}"
    match_count = matched_data.get("match_count", 0)
    trigger_text = matched_data.get("trigger", "Signal conditions were met")
    signal_type = matched_data.get("signal_type", "unknown")

    html_body = (
        f"<h2>Intent Signal: {signal.name}</h2>"
        f"<p><strong>Type:</strong> {signal_type}</p>"
        f"<p><strong>Matches:</strong> {match_count}</p>"
        f"<p><strong>Trigger:</strong> {trigger_text}</p>"
        f"<hr><p>Automated notification from LakeStream.</p>"
    )

    headers = {"Content-Type": "application/json"}
    if settings.mail_engine_api_key:
        headers["X-API-Key"] = settings.mail_engine_api_key

    async with httpx.AsyncClient() as client:
        for recipient in email_recipients:
            text_body = f"Signal: {signal.name} | Type: {signal_type} | Matches: {match_count}"
            response = await client.post(
                f"{settings.mail_engine_url}/api/v1/send",
                headers=headers,
                json={
                    "recipient": recipient,
                    "subject": subject,
                    "html_body": html_body,
                    "text_body": text_body,
                    "from_address": settings.mail_engine_from_address,
                    "track_opens": False,
                    "track_clicks": False,
                },
                timeout=10.0,
            )
            response.raise_for_status()

    log.info(
        "email_notification_sent",
        signal_id=str(signal.id),
        recipients=email_recipients,
        match_count=match_count,
    )


# ============================================================================
# Real-Time Event Streaming (Phase G Preview)
# ============================================================================


async def publish_signal_event(signal: Signal, matched_data: dict[str, Any]) -> None:
    """Publish signal event to Redis pub/sub for real-time streaming.

    Events are published to org-specific channels that WebSocket clients
    can subscribe to for real-time intent signal notifications.
    """
    settings = get_settings()

    try:
        # Create Redis client
        redis_client = redis.from_url(settings.redis_url)

        # Build event payload
        event = {
            "event_type": "signal_fired",
            "signal_id": str(signal.id),
            "signal_name": signal.name,
            "matched_data": matched_data,
            "timestamp": datetime.now(UTC).isoformat(),
        }

        # Publish to org-specific channel
        channel = f"intent:org:{signal.org_id}"
        await redis_client.publish(channel, json.dumps(event))

        await redis_client.aclose()

        log.debug(
            "signal_event_published",
            signal_id=str(signal.id),
            channel=channel,
        )

    except Exception as e:
        log.warning(
            "signal_event_publish_failed",
            signal_id=str(signal.id),
            error=str(e),
        )


async def publish_signal_to_champiq(
    signal: Signal, matched_data: dict[str, Any], action_config: dict[str, Any]
) -> dict[str, Any]:
    """Publish `signal.matched` onto ChampIQ's event bus.

    This is what makes signal-triggered outreach event-driven rather than
    polled. Detection happens here, in LakeStream, against raw scraped
    observations; qualification and action happen downstream in Harbinger and
    ChampMail. Those are different layers of one pipeline, not two competing
    signal engines — LakeStream never needs to know about campaigns, and
    Harbinger never needs to scrape.

    The payload deliberately carries `company_domain` per match. ChampIQ's
    lead-correlation layer derives its key from that field when no contact
    email is known yet (a `co:`-prefixed company key), so a pre-contact hiring
    signal joins the same lead journey that later email events land on.

    Fire-and-forget by contract: a down or unconfigured ChampIQ must never fail
    signal evaluation, because the signal itself is still true and will be
    re-detected on the next pass.
    """
    base_url = (action_config.get("champiq_url") or "").rstrip("/")
    if not base_url:
        raise ValueError("champiq_url not configured for this signal action")

    matches = matched_data.get("matches") or []
    events = []
    for match in matches:
        domain = match.get("domain") or match.get("company_domain")
        if not domain:
            continue
        events.append(
            {
                "type": "signal.matched",
                "signal_id": str(signal.id),
                "signal_name": signal.name,
                "signal_type": matched_data.get("signal_type"),
                "company_domain": domain,
                "account_name": action_config.get("account_name"),
                "evidence": {k: v for k, v in match.items() if k != "domain"},
                "detected_at": datetime.now(UTC).isoformat(),
            }
        )

    if not events:
        return {"published": 0, "reason": "no match carried a company domain"}

    url = f"{base_url}/api/webhooks/tools/lakestream"
    headers = {"Content-Type": "application/json"}
    secret = action_config.get("champiq_webhook_secret")
    if secret:
        headers["X-ChampIQ-Signature"] = hmac.new(
            secret.encode(),
            json.dumps(events, default=str).encode(),
            hashlib.sha256,
        ).hexdigest()

    published = 0
    async with httpx.AsyncClient(timeout=10.0) as client:
        for event in events:
            try:
                resp = await client.post(url, json=event, headers=headers)
                if resp.status_code < 400:
                    published += 1
                else:
                    log.warning(
                        "champiq_publish_rejected",
                        status=resp.status_code, domain=event["company_domain"],
                    )
            except Exception:
                # ! One company failing must not drop the rest of the batch.
                log.exception(
                    "champiq_publish_failed", domain=event["company_domain"]
                )

    return {"published": published, "attempted": len(events)}
