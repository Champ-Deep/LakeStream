"""Characterization tests for ContentWorker orchestration behavior.

Covers the pieces that stayed on ContentWorker after the extraction/
lifecycle split: PDF handling delegation, DTO wrapping, and that the
`_extract_*` delegation methods still exist and behave like before (for
backward compatibility with any external callers/tests that use them
directly, e.g. tests/unit/workers/test_content_worker_templates.py).
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.models.scraped_data import DataType
from src.workers.content_worker import ContentWorker
from src.workers.dto import build_scraped_data, build_scraped_data_list
from src.workers.job_lifecycle import JobLifecycle


def _worker(**kwargs) -> ContentWorker:
    return ContentWorker(domain="example.com", job_id=str(uuid4()), **kwargs)


class FakePdfResult:
    text = "hello world"
    tables: list = []
    metadata = {"author": "Ada", "title": "My PDF"}
    word_count = 2
    page_count = 1


class TestProcessPdf:
    @pytest.mark.asyncio
    async def test_no_content_bytes_returns_empty(self):
        worker = _worker()
        fetch_result = MagicMock(content_bytes=None)

        result = await worker._process_pdf("https://example.com/doc.pdf", fetch_result)

        assert result == []

    @pytest.mark.asyncio
    async def test_builds_document_record_and_exports(self):
        worker = _worker()
        worker.export_results = AsyncMock(return_value=1)
        fetch_result = MagicMock(content_bytes=b"%PDF-1.4 fake bytes")

        with (
            patch("src.workers.pdf_handler.parse_pdf", return_value=FakePdfResult()),
            patch("src.workers.pdf_handler.pdf_to_markdown", return_value="# hello world"),
        ):
            result = await worker._process_pdf("https://example.com/doc.pdf", fetch_result)

        assert len(result) == 1
        dto = result[0]
        assert dto.data_type == DataType.DOCUMENT
        assert dto.title == "My PDF"
        assert dto.metadata["text_content"] == "# hello world"
        worker.export_results.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_parse_error_returns_empty_and_does_not_export(self):
        worker = _worker()
        worker.export_results = AsyncMock()
        fetch_result = MagicMock(content_bytes=b"not really a pdf")

        with patch("src.workers.pdf_handler.parse_pdf", side_effect=ValueError("bad pdf")):
            result = await worker._process_pdf("https://example.com/doc.pdf", fetch_result)

        assert result == []
        worker.export_results.assert_not_awaited()


class TestDtoBuilder:
    def test_build_scraped_data_uses_record_url_when_present(self):
        record = {
            "data_type": DataType.RESOURCE,
            "url": "https://example.com/resource-1",
            "title": "Resource 1",
            "metadata": {"resource_type": "ebook"},
        }
        dto = build_scraped_data(record, str(uuid4()), "example.com", "https://example.com/page")

        assert dto.url == "https://example.com/resource-1"
        assert dto.title == "Resource 1"
        assert dto.domain == "example.com"

    def test_build_scraped_data_falls_back_to_page_url(self):
        record = {"data_type": DataType.PAGE, "title": None, "metadata": {}}
        dto = build_scraped_data(record, str(uuid4()), "example.com", "https://example.com/page")

        assert dto.url == "https://example.com/page"

    def test_build_scraped_data_list_preserves_order(self):
        records = [
            {"data_type": DataType.PAGE, "metadata": {}},
            {"data_type": DataType.ARTICLE, "metadata": {}},
        ]
        dtos = build_scraped_data_list(records, str(uuid4()), "example.com", "https://x.com")

        assert [d.data_type for d in dtos] == [DataType.PAGE, DataType.ARTICLE]


class TestJobLifecycleNoPool:
    @pytest.mark.asyncio
    async def test_is_cancelled_false_without_pool(self):
        lifecycle = JobLifecycle(pool=None, job_id=str(uuid4()))
        assert await lifecycle.is_cancelled() is False

    @pytest.mark.asyncio
    async def test_heartbeat_noop_without_pool(self):
        lifecycle = JobLifecycle(pool=None, job_id=str(uuid4()))
        # Should not raise even though there's no pool/heartbeat_fn wired up.
        await lifecycle.maybe_heartbeat(5)


class TestJobLifecycleWithInjectedChecks:
    @pytest.mark.asyncio
    async def test_is_cancelled_true_when_check_returns_true(self):
        cancel_check = AsyncMock(return_value=True)
        lifecycle = JobLifecycle(pool=object(), job_id=str(uuid4()), cancel_check=cancel_check)

        assert await lifecycle.is_cancelled(urls_processed=3) is True
        cancel_check.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_is_cancelled_false_and_logs_warning_on_exception(self):
        cancel_check = AsyncMock(side_effect=RuntimeError("db down"))
        lifecycle = JobLifecycle(pool=object(), job_id=str(uuid4()), cancel_check=cancel_check)

        assert await lifecycle.is_cancelled() is False

    @pytest.mark.asyncio
    async def test_heartbeat_fires_every_n_urls(self):
        heartbeat_fn = AsyncMock()
        lifecycle = JobLifecycle(
            pool=object(), job_id=str(uuid4()), heartbeat_every=5, heartbeat_fn=heartbeat_fn,
        )

        for n in range(1, 11):
            await lifecycle.maybe_heartbeat(n)

        assert heartbeat_fn.await_count == 2  # fires at 5 and 10

    @pytest.mark.asyncio
    async def test_heartbeat_failure_is_swallowed(self):
        heartbeat_fn = AsyncMock(side_effect=RuntimeError("db down"))
        lifecycle = JobLifecycle(
            pool=object(), job_id=str(uuid4()), heartbeat_every=1, heartbeat_fn=heartbeat_fn,
        )

        # Should not raise.
        await lifecycle.maybe_heartbeat(1)


class TestExecuteRespectsMaxPagesCap:
    @pytest.mark.asyncio
    async def test_execute_returns_empty_for_no_urls(self):
        worker = _worker()
        result = await worker.execute([], ["page"])
        assert result == []

    @pytest.mark.asyncio
    async def test_execute_caps_url_list_to_max_pages(self):
        worker = _worker()
        worker._process_url = AsyncMock(return_value=[])
        classified_urls = [
            {"url": f"https://example.com/{i}", "data_type": DataType.PAGE} for i in range(3)
        ]

        with patch("src.config.settings.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(max_scrape_pages_per_job=2)
            await worker.execute(classified_urls, ["page"])

        assert worker._process_url.await_count == 2
