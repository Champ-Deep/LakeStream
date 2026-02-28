from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import sys


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_logs_database_error(self):
        # Import first to populate the module
        from src.api.routes import health as health_module
        from src.api.routes.health import health

        with (
            patch(
                "src.api.routes.health.get_pool",
                new_callable=AsyncMock,
                side_effect=ConnectionError("pg down"),
            ),
            patch("src.api.routes.health.get_settings") as mock_settings,
            patch("src.api.routes.health.log") as mock_log,
        ):
            settings = MagicMock()
            settings.redis_url = "redis://localhost:6379"
            settings.lakecurrent_enabled = False
            mock_settings.return_value = settings

            with patch(
                "src.api.routes.health.create_arq_pool",
                new_callable=AsyncMock,
                side_effect=ConnectionError("redis down"),
            ):
                result = await health()

            assert result.database == "disconnected"
            mock_log.warning.assert_called()

    @pytest.mark.asyncio
    async def test_health_logs_redis_error(self):
        from src.api.routes.health import health

        with (
            patch("src.api.routes.health.get_pool", new_callable=AsyncMock) as mock_pool,
            patch("src.api.routes.health.get_settings") as mock_settings,
            patch(
                "src.api.routes.health.create_arq_pool",
                new_callable=AsyncMock,
                side_effect=ConnectionError("redis down"),
            ),
            patch("src.api.routes.health.log") as mock_log,
        ):
            pool_instance = AsyncMock()
            pool_instance.fetchval = AsyncMock(return_value=1)
            mock_pool.return_value = pool_instance
            settings = MagicMock()
            settings.redis_url = "redis://localhost:6379"
            settings.lakecurrent_enabled = False
            mock_settings.return_value = settings

            result = await health()

            assert result.redis == "disconnected"
            mock_log.warning.assert_called()
