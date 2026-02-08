"""Signal evaluation engine for intent data platform.

This module contains the core logic for evaluating intent signals:
1. Check if signal conditions are met based on scraped data
2. Execute actions when signals fire (Slack, webhook, email)
3. Log execution results
"""

import json
from datetime import UTC, datetime, timedelta
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


async def evaluate_signal(
    pool: Pool, signal: Signal, org_id: UUID
) -> dict[str, Any] | None:
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
    elif trigger_type == "pricing_change":
        return await check_pricing_change_signal(pool, signal, org_id)
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
    """Check if job change conditions match recent data."""
    filters = signal.trigger_config.get("filters", {})
    job_title = filters.get("job_title_contains", "")

    # Query scraped data for recent job changes (last 24 hours)
    query = """
        SELECT * FROM scraped_data
        WHERE org_id = $1
        AND data_type = 'contact'
        AND metadata->>'job_title' ILIKE $2
        AND scraped_at > NOW() - INTERVAL '24 hours'
        ORDER BY scraped_at DESC
        LIMIT 100
    """

    rows = await pool.fetch(query, org_id, f"%{job_title}%")

    if rows:
        matches = [dict(row) for row in rows]
        return {
            "matches": matches,
            "match_count": len(matches),
            "signal_type": "job_change",
            "trigger": f"Found {len(matches)} contacts with job title containing '{job_title}'",
        }

    return None


async def check_funding_signal(
    pool: Pool, signal: Signal, org_id: UUID
) -> dict[str, Any] | None:
    """Check if funding round conditions match recent data."""
    filters = signal.trigger_config.get("filters", {})

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


async def check_pricing_change_signal(
    pool: Pool, signal: Signal, org_id: UUID
) -> dict[str, Any] | None:
    """Check if pricing page changes match conditions."""
    # Query for pricing page changes
    query = """
        SELECT * FROM scraped_data
        WHERE org_id = $1
        AND data_type = 'pricing'
        AND scraped_at > NOW() - INTERVAL '7 days'
        LIMIT 50
    """

    rows = await pool.fetch(query, org_id)

    if rows:
        matches = [dict(row) for row in rows]
        return {
            "matches": matches,
            "match_count": len(matches),
            "signal_type": "pricing_change",
            "trigger": f"Found {len(matches)} pricing updates",
        }

    return None


async def check_hiring_spike_signal(
    pool: Pool, signal: Signal, org_id: UUID
) -> dict[str, Any] | None:
    """Check if hiring volume spike conditions match."""
    filters = signal.trigger_config.get("filters", {})
    department = filters.get("department", "All")
    spike_threshold = filters.get("spike_threshold", 3)  # 3x normal

    # Query for recent job postings
    query = """
        SELECT domain, COUNT(*) as job_count
        FROM scraped_data
        WHERE org_id = $1
        AND data_type = 'job_posting'
        AND scraped_at > NOW() - INTERVAL '7 days'
        GROUP BY domain
        HAVING COUNT(*) >= $2
    """

    rows = await pool.fetch(query, org_id, spike_threshold * 2)  # Simplified threshold

    if rows:
        matches = [dict(row) for row in rows]
        return {
            "matches": matches,
            "match_count": len(matches),
            "signal_type": "hiring_spike",
            "trigger": f"Found {len(matches)} companies with hiring spikes",
        }

    return None


# ============================================================================
# Action Execution
# ============================================================================


async def execute_signal_action(
    pool: Pool, signal: Signal, matched_data: dict[str, Any]
) -> None:
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
                    "text": f"*Signal:* {signal.name}\n*Matches:* {matched_data.get('match_count', 0)}\n*Trigger:* {matched_data.get('trigger', 'N/A')}",
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
    """Send email notification."""
    email_recipients = action_config.get("email_recipients", [])
    if not email_recipients:
        raise ValueError("Email recipients not configured")

    # TODO: Integrate with email service (SendGrid, AWS SES, etc.)
    log.info(
        "email_notification_placeholder",
        signal_id=str(signal.id),
        recipients=email_recipients,
        match_count=matched_data.get("match_count", 0),
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
