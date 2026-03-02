from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


def _make_signal():
    from src.models.signals import Signal

    return Signal(
        id=uuid4(),
        org_id=uuid4(),
        name="Test Signal",
        description="Test",
        is_active=True,
        trigger_config={"type": "job_change"},
        condition_config=None,
        action_config={"type": "email"},
        created_by=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        last_fired_at=None,
        fire_count=0,
    )


class TestSendEmailNotification:
    @pytest.mark.asyncio
    async def test_raises_without_recipients(self):
        from src.services.signal_evaluator import send_email_notification

        with pytest.raises(ValueError, match="Email recipients not configured"):
            await send_email_notification(
                _make_signal(), {"match_count": 1}, {"email_recipients": []}
            )

    @pytest.mark.asyncio
    async def test_raises_when_disabled(self):
        from src.services.signal_evaluator import send_email_notification

        with patch("src.services.signal_evaluator.get_settings") as mock_s:
            mock_s.return_value = MagicMock(mail_engine_enabled=False)
            with pytest.raises(RuntimeError, match="not enabled"):
                await send_email_notification(
                    _make_signal(), {"match_count": 1}, {"email_recipients": ["a@b.com"]}
                )

    @pytest.mark.asyncio
    async def test_sends_via_champmail_engine(self):
        from src.services.signal_evaluator import send_email_notification

        with (
            patch("src.services.signal_evaluator.get_settings") as mock_s,
            patch("src.services.signal_evaluator.httpx.AsyncClient") as mock_client_cls,
        ):
            mock_s.return_value = MagicMock(
                mail_engine_enabled=True,
                mail_engine_url="http://mail:8025",
                mail_engine_api_key="test-key",
                mail_engine_from_address="LakeStream <n@lakeb2b.com>",
            )
            mock_client = AsyncMock()
            mock_resp = MagicMock(status_code=200)
            mock_resp.raise_for_status = MagicMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await send_email_notification(
                _make_signal(),
                {"match_count": 5, "signal_type": "job_change", "trigger": "5 matches"},
                {"email_recipients": ["user@example.com"]},
            )
            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args
            assert "http://mail:8025/api/v1/send" in str(call_args)
            assert "user@example.com" in str(call_args)
