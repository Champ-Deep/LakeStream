"""Offline unit tests for the LLM tech judge.

The judge must be strictly subtractive: it can remove a regex false positive,
but it must never add a technology, never touch a high-confidence structural
detection, and never fail the pipeline.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.services import tech_judge

HIGH = {"name": "nginx", "category": "web_server", "confidence": "high",
        "evidence": "nginx/1.24", "source": "header"}
MEDIUM_BODY = {"name": "Shopify", "category": "cms", "confidence": "medium",
               "evidence": "Trusted by Shopify", "source": "html"}
MEDIUM_BODY_2 = {"name": "Salesforce", "category": "marketing", "confidence": "medium",
                 "evidence": "integrates with Salesforce", "source": "html"}


def _settings(enabled=True, maxn=40):
    s = tech_judge.get_settings()

    class S:
        enable_tech_judge = enabled
        tech_judge_max_detections = maxn

    return S()


class TestGating:
    @pytest.mark.asyncio
    async def test_disabled_returns_input_unchanged(self):
        with patch.object(tech_judge, "get_settings", return_value=_settings(enabled=False)):
            kept, rejected = await tech_judge.judge_detections([MEDIUM_BODY])
        assert kept == [MEDIUM_BODY]
        assert rejected == []

    @pytest.mark.asyncio
    async def test_force_overrides_disabled_flag(self):
        """The per-request override must not mutate shared settings."""
        with patch.object(tech_judge, "get_settings", return_value=_settings(enabled=False)), \
             patch("src.services.llm_extractor.get_openrouter_config", AsyncMock(return_value=("k", "m"))), \
             patch("src.services.llm_extractor.LLMExtractor") as LLM:
            LLM.return_value.extract_freeform = AsyncMock(
                return_value={"verdicts": [{"name": "Shopify", "uses": False, "reason": "logo"}]}
            )
            kept, rejected = await tech_judge.judge_detections([MEDIUM_BODY], force=True)
        assert [r["name"] for r in rejected] == ["Shopify"]

    @pytest.mark.asyncio
    async def test_high_confidence_never_reviewed(self):
        with patch.object(tech_judge, "get_settings", return_value=_settings()), \
             patch("src.services.llm_extractor.get_openrouter_config", AsyncMock(return_value=("k", "m"))) as cfg:
            kept, rejected = await tech_judge.judge_detections([HIGH])
        # nothing reviewable -> the LLM is never even configured/called
        assert kept == [HIGH]
        assert rejected == []
        cfg.assert_not_called()


class TestSubtractiveOnly:
    @pytest.mark.asyncio
    async def test_rejects_only_flagged_detection(self):
        with patch.object(tech_judge, "get_settings", return_value=_settings()), \
             patch("src.services.llm_extractor.get_openrouter_config", AsyncMock(return_value=("k", "m"))), \
             patch("src.services.llm_extractor.LLMExtractor") as LLM:
            LLM.return_value.extract_freeform = AsyncMock(return_value={
                "verdicts": [
                    {"name": "Shopify", "uses": False, "reason": "customer logo"},
                    {"name": "Salesforce", "uses": True, "reason": "real"},
                ]
            })
            kept, rejected = await tech_judge.judge_detections(
                [HIGH, MEDIUM_BODY, MEDIUM_BODY_2]
            )
        assert [k["name"] for k in kept] == ["nginx", "Salesforce"]
        assert [r["name"] for r in rejected] == ["Shopify"]
        assert rejected[0]["rejected_reason"] == "customer logo"

    @pytest.mark.asyncio
    async def test_cannot_reject_something_not_reviewed(self):
        """A judge verdict against a high-confidence item must be ignored."""
        with patch.object(tech_judge, "get_settings", return_value=_settings()), \
             patch("src.services.llm_extractor.get_openrouter_config", AsyncMock(return_value=("k", "m"))), \
             patch("src.services.llm_extractor.LLMExtractor") as LLM:
            LLM.return_value.extract_freeform = AsyncMock(return_value={
                "verdicts": [{"name": "nginx", "uses": False, "reason": "hallucinated"}]
            })
            kept, rejected = await tech_judge.judge_detections([HIGH, MEDIUM_BODY])
        assert {k["name"] for k in kept} == {"nginx", "Shopify"}
        assert rejected == []

    @pytest.mark.asyncio
    async def test_judge_cannot_add_technologies(self):
        with patch.object(tech_judge, "get_settings", return_value=_settings()), \
             patch("src.services.llm_extractor.get_openrouter_config", AsyncMock(return_value=("k", "m"))), \
             patch("src.services.llm_extractor.LLMExtractor") as LLM:
            LLM.return_value.extract_freeform = AsyncMock(return_value={
                "verdicts": [{"name": "Kubernetes", "uses": True, "reason": "invented"}]
            })
            kept, _ = await tech_judge.judge_detections([MEDIUM_BODY])
        assert [k["name"] for k in kept] == ["Shopify"]


class TestFailureModes:
    @pytest.mark.asyncio
    async def test_no_api_key_keeps_regex_results(self):
        with patch.object(tech_judge, "get_settings", return_value=_settings()), \
             patch("src.services.llm_extractor.get_openrouter_config",
                   AsyncMock(side_effect=ValueError("no key"))):
            kept, rejected = await tech_judge.judge_detections([MEDIUM_BODY])
        assert kept == [MEDIUM_BODY]
        assert rejected == []

    @pytest.mark.asyncio
    async def test_llm_exception_keeps_regex_results(self):
        with patch.object(tech_judge, "get_settings", return_value=_settings()), \
             patch("src.services.llm_extractor.get_openrouter_config", AsyncMock(return_value=("k", "m"))), \
             patch("src.services.llm_extractor.LLMExtractor") as LLM:
            LLM.return_value.extract_freeform = AsyncMock(side_effect=RuntimeError("boom"))
            kept, rejected = await tech_judge.judge_detections([MEDIUM_BODY])
        assert kept == [MEDIUM_BODY]
        assert rejected == []

    @pytest.mark.asyncio
    async def test_malformed_llm_response_keeps_regex_results(self):
        with patch.object(tech_judge, "get_settings", return_value=_settings()), \
             patch("src.services.llm_extractor.get_openrouter_config", AsyncMock(return_value=("k", "m"))), \
             patch("src.services.llm_extractor.LLMExtractor") as LLM:
            LLM.return_value.extract_freeform = AsyncMock(return_value={"nonsense": True})
            kept, rejected = await tech_judge.judge_detections([MEDIUM_BODY])
        assert kept == [MEDIUM_BODY]
        assert rejected == []
