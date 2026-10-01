"""Notification channels for signal actions (Slack, webhook, email).

Each channel wraps the pre-existing send logic behind a small common
interface (`NotificationChannel.send`), extracted verbatim from
`signal_evaluator.py` with behavior preserved exactly.
"""

import hashlib
import hmac
import json
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from src.config.settings import get_settings
from src.models.signals import Signal

log = structlog.get_logger()


class NotificationChannel(ABC):
    """Common interface for delivering a fired-signal notification."""

    @abstractmethod
    async def send(
        self, signal: Signal, matched_data: dict[str, Any], action_config: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Send the notification. Returns an optional response payload."""
        raise NotImplementedError


class SlackNotificationChannel(NotificationChannel):
    """Send Slack notification via webhook."""

    async def send(
        self, signal: Signal, matched_data: dict[str, Any], action_config: dict[str, Any]
    ) -> dict[str, Any] | None:
        await send_slack_notification(signal, matched_data, action_config)
        return {"message": "Slack notification sent"}


class WebhookNotificationChannel(NotificationChannel):
    """Send generic webhook notification."""

    async def send(
        self, signal: Signal, matched_data: dict[str, Any], action_config: dict[str, Any]
    ) -> dict[str, Any] | None:
        return await send_webhook_notification(signal, matched_data, action_config)


class EmailNotificationChannel(NotificationChannel):
    """Send email notification via ChampMail engine API."""

    async def send(
        self, signal: Signal, matched_data: dict[str, Any], action_config: dict[str, Any]
    ) -> dict[str, Any] | None:
        await send_email_notification(signal, matched_data, action_config)
        return {"message": "Email sent"}


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


class ChampIQNotificationChannel(NotificationChannel):
    """Publish `signal.matched` onto ChampIQ's event bus.

    Ported from the monolithic signal_evaluator.py on PR #16 into the channel
    registry, so it dispatches through the same path as Slack/webhook/email.
    """

    async def send(
        self, signal: Signal, matched_data: dict[str, Any], action_config: dict[str, Any]
    ) -> dict[str, Any] | None:
        return await publish_signal_to_champiq(signal, matched_data, action_config)


NOTIFICATION_CHANNELS: dict[str, NotificationChannel] = {
    "slack": SlackNotificationChannel(),
    "webhook": WebhookNotificationChannel(),
    "email": EmailNotificationChannel(),
    "champiq": ChampIQNotificationChannel(),
}


async def publish_signal_to_champiq(
    signal: Signal, matched_data: dict[str, Any], action_config: dict[str, Any]
) -> dict[str, Any]:
    """Publish `signal.matched` onto ChampIQ's event bus.

    This is what makes signal-triggered outreach event-driven rather than
    polled. Detection happens here, in LakeStream, against raw scraped
    observations; qualification and action happen downstream in Harbinger and
    ChampMail. Those are different layers of one pipeline, not two competing
    signal engines: LakeStream never needs to know about campaigns, and
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
