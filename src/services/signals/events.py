"""Real-Time Event Streaming (Phase G Preview).

Speculative/preview functionality for streaming fired signals to WebSocket
clients over Redis pub/sub. Not something to expand in this pass.
"""

import json
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as redis
import structlog

from src.config.settings import get_settings
from src.models.signals import Signal

log = structlog.get_logger()


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
