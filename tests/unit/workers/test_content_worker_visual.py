"""ContentWorker handling of the visual tier's ``FetchResult.facts``.

No browser, DB or network: ``fetch_page`` and ``export_results`` are mocked.
Covers: facts ride along as one EXTRACTED record next to the page record; a
wall still produces an explicit blocked record; results without facts are
untouched (no visual record, blocked pages still return nothing).
"""

from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.models.scraped_data import DataType
from src.models.scraping import FetchResult, ScrapingTier
from src.workers.content_worker import ContentWorker

FIXTURES = Path(__file__).parents[2] / "fixtures" / "visual"
URL = "https://example.test/company/acme-corp/"
COMPANY_HTML = (FIXTURES / "company_page.html").read_text(encoding="utf-8")


def _facts(status: str, name: str | None = "Acme Corp") -> dict:
    return {
        "status": status, "reason": None, "producer": "visual_probe",
        "facts": {"name": {"value": name, "source_url": URL,
                           "captured_at": "2026-10-19T10:00:00+00:00",
                           "screenshot": "step_1.before.png"} if name else None},
    }


def _result(**kw) -> FetchResult:
    base = dict(url=URL, status_code=200, html=COMPANY_HTML, tier_used=ScrapingTier.VISUAL,
                cost_usd=0.005, duration_ms=1000)
    base.update(kw)
    return FetchResult(**base)


def _worker(result: FetchResult) -> ContentWorker:
    worker = ContentWorker(domain="example.test", job_id=str(uuid4()), raw_only=True)
    worker.fetch_page = AsyncMock(return_value=result)
    worker.export_results = AsyncMock(return_value=1)
    worker._handle_page_content = AsyncMock(return_value=False)
    return worker


class TestVisualFacts:
    @pytest.mark.asyncio
    async def test_success_adds_extracted_record_next_to_page(self):
        worker = _worker(_result(facts=_facts("success")))

        out = await worker._process_url(URL, DataType.PAGE, ["article"])

        types = [d.data_type for d in out]
        assert DataType.PAGE in types and DataType.EXTRACTED in types
        visual = next(d for d in out if d.data_type == DataType.EXTRACTED)
        assert visual.title == "Acme Corp"
        assert visual.metadata["source"] == "visual"
        assert visual.metadata["status"] == "success"
        assert visual.metadata["facts"]["name"]["screenshot"] == "step_1.before.png"
        worker.export_results.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_wall_produces_explicit_blocked_record(self):
        worker = _worker(_result(status_code=999, html="", blocked=True,
                                 facts=_facts("blocked", name=None)))

        out = await worker._process_url(URL, DataType.PAGE, ["article"])

        assert len(out) == 1
        assert out[0].data_type == DataType.EXTRACTED
        assert out[0].title is None
        assert out[0].metadata["status"] == "blocked"
        worker.export_results.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_partial_without_html_is_still_stored(self):
        worker = _worker(_result(html="", facts=_facts("partial")))

        out = await worker._process_url(URL, DataType.PAGE, ["article"])

        assert [d.data_type for d in out] == [DataType.EXTRACTED]
        assert out[0].metadata["status"] == "partial"


class TestOtherTiersUnchanged:
    @pytest.mark.asyncio
    async def test_no_facts_no_visual_record(self):
        worker = _worker(_result(tier_used=ScrapingTier.PLAYWRIGHT))

        out = await worker._process_url(URL, DataType.PAGE, ["article"])

        assert [d.data_type for d in out] == [DataType.PAGE]

    @pytest.mark.asyncio
    async def test_blocked_without_facts_returns_nothing(self):
        worker = _worker(_result(tier_used=ScrapingTier.PLAYWRIGHT, status_code=403,
                                 html="", blocked=True))

        out = await worker._process_url(URL, DataType.PAGE, ["article"])

        assert out == []
        worker.export_results.assert_not_awaited()
