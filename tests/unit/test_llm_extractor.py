"""Tests for LLM extractor service (mocked OpenRouter API)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

from src.models.extraction import ExtractionField, ExtractionSchema
from src.services.llm_extractor import (
    LLMExtractor,
    _schema_to_json_spec,
    _strip_html_to_text,
)


class TestSchemaToJsonSpec:
    def test_object_spec(self):
        schema = ExtractionSchema(
            name="test",
            fields=[
                ExtractionField(name="title", selector="h1", type="string"),
                ExtractionField(name="price", selector=".price", type="number"),
            ],
        )
        spec = _schema_to_json_spec(schema)
        parsed = json.loads(spec)
        assert parsed["type"] == "object"
        assert "title" in parsed["properties"]
        assert "price" in parsed["properties"]

    def test_array_spec_with_list_selector(self):
        schema = ExtractionSchema(
            name="test",
            list_selector=".item",
            fields=[
                ExtractionField(name="name", selector="h3"),
            ],
        )
        spec = _schema_to_json_spec(schema)
        parsed = json.loads(spec)
        assert parsed["type"] == "array"
        assert "name" in parsed["items"]["properties"]


class TestStripHtml:
    def test_strips_tags(self):
        html = "<html><body><h1>Hello</h1><p>World</p></body></html>"
        text = _strip_html_to_text(html)
        assert "Hello" in text
        assert "World" in text
        assert "<h1>" not in text

    def test_removes_scripts(self):
        html = '<html><script>var x=1;</script><body>Content</body></html>'
        text = _strip_html_to_text(html)
        assert "var x" not in text
        assert "Content" in text

    def test_truncates_long_content(self):
        html = f"<p>{'x' * 50000}</p>"
        text = _strip_html_to_text(html, max_chars=100)
        assert len(text) < 200  # 100 + truncation notice
        assert "truncated" in text


def _mock_openrouter_config():
    """Patch get_openrouter_config to return test credentials."""
    return patch(
        "src.services.llm_extractor.get_openrouter_config",
        new_callable=AsyncMock,
        return_value=("sk-test-key", "anthropic/claude-3.5-haiku"),
    )


class TestLLMExtractor:
    async def test_extract_returns_result(self):
        extractor = LLMExtractor()

        # Mock the OpenAI client
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({
            "company": "Acme Inc",
            "employees": 500,
        })

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=mock_response,
        )
        extractor._client = mock_client

        schema = ExtractionSchema(
            name="test",
            fields=[
                ExtractionField(name="company", selector="h1"),
                ExtractionField(name="employees", selector=".count", type="number"),
            ],
        )

        with _mock_openrouter_config():
            result = await extractor.extract("Acme Inc has 500 employees", schema)

        assert result.mode == "ai"
        assert result.data["company"] == "Acme Inc"
        assert result.data["employees"] == 500
        assert result.fields_found == 2

    async def test_extract_handles_markdown_fences(self):
        extractor = LLMExtractor()

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = (
            '```json\n{"name": "Test"}\n```'
        )

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=mock_response,
        )
        extractor._client = mock_client

        schema = ExtractionSchema(
            name="test",
            fields=[ExtractionField(name="name", selector="h1")],
        )

        with _mock_openrouter_config():
            result = await extractor.extract("Test content", schema)
        assert result.data["name"] == "Test"

    async def test_extract_retries_on_invalid_json(self):
        extractor = LLMExtractor()

        # First response is invalid JSON, second is valid
        bad_response = MagicMock()
        bad_response.choices = [MagicMock()]
        bad_response.choices[0].message.content = "not json"

        good_response = MagicMock()
        good_response.choices = [MagicMock()]
        good_response.choices[0].message.content = '{"name": "Fixed"}'

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=[bad_response, good_response],
        )
        extractor._client = mock_client

        schema = ExtractionSchema(
            name="test",
            fields=[ExtractionField(name="name", selector="h1")],
        )

        with _mock_openrouter_config():
            result = await extractor.extract("Content", schema)
        assert result.data["name"] == "Fixed"
        assert mock_client.chat.completions.create.call_count == 2

    async def test_extract_reports_missing_required_fields(self):
        extractor = LLMExtractor()

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"name": "Test"}'

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=mock_response,
        )
        extractor._client = mock_client

        schema = ExtractionSchema(
            name="test",
            fields=[
                ExtractionField(name="name", selector="h1"),
                ExtractionField(name="email", selector=".email", required=True),
            ],
        )

        with _mock_openrouter_config():
            result = await extractor.extract("Content", schema)
        assert "email" in result.fields_missing

    async def test_raises_without_api_key(self):
        extractor = LLMExtractor()

        with patch(
            "src.services.llm_extractor.get_settings"
        ) as mock_settings:
            mock_settings.return_value = MagicMock(openrouter_api_key="")
            try:
                extractor._get_client()
                assert False, "Should have raised ValueError"
            except ValueError as e:
                assert "OPENROUTER_API_KEY" in str(e)
