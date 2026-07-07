"""Signal evaluation engine for intent data platform.

This module contains the orchestration logic for evaluating intent signals:
1. Check if signal conditions are met based on scraped data
   (delegated to `src.services.signals.matchers`)
2. Execute actions when signals fire, e.g. Slack, webhook, email
   (delegated to `src.services.signals.notifications`)
3. Log execution results and publish real-time events
   (delegated to `src.services.signals.events`)

The signal-type-specific matcher and notification-channel implementations
live in `src.services.signals`. They are re-exported here for backward
compatibility with existing imports/tests.
"""

from typing import Any
from uuid import UUID

import structlog
from asyncpg import Pool

from src.db.pool import get_pool
from src.db.queries.signals import (
    create_signal_execution,
    get_active_signals,
    increment_signal_fire_count,
)
from src.models.signals import Signal
from src.services.signals.events import publish_signal_event
from src.services.signals.matchers import (
    check_funding_signal,
    check_hiring_spike_signal,
    check_job_change_signal,
    check_tech_stack_signal,
)
from src.services.signals.notifications import (
    NOTIFICATION_CHANNELS,
    send_email_notification,
    send_slack_notification,
    send_webhook_notification,
)

log = structlog.get_logger()

__all__ = [
    "evaluate_signals_for_org",
    "evaluate_signal",
    "execute_signal_action",
    "check_job_change_signal",
    "check_funding_signal",
    "check_tech_stack_signal",
    "check_hiring_spike_signal",
    "send_slack_notification",
    "send_webhook_notification",
    "send_email_notification",
    "publish_signal_event",
]


# ============================================================================
# Main Evaluation Loop
# ============================================================================


async def evaluate_signals_for_org(org_id: UUID) -> int:
    """Evaluate all active signals for an organization.

    Returns:
        Number of signals that fired
    """
    pool = await get_pool()

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
# Action Execution
# ============================================================================


async def execute_signal_action(pool: Pool, signal: Signal, matched_data: dict[str, Any]) -> None:
    """Execute the configured action when a signal fires."""
    action_config = signal.action_config
    action_type = action_config.get("type")

    try:
        channel = NOTIFICATION_CHANNELS.get(action_type) if isinstance(action_type, str) else None
        if channel is not None:
            response = await channel.send(signal, matched_data, action_config)
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
