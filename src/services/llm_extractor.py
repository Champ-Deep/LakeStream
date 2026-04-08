"""LLM-powered structured extraction via OpenRouter.

Uses the OpenAI-compatible SDK pointed at OpenRouter to extract structured
data from page content using any supported model. Same ExtractionSchema
as the CSS extractor — schema defines fields, LLM fills them.

Model flexibility: configure via OPENROUTER_API_KEY + LLM_EXTRACTION_MODEL.
Test with cheap models (haiku, gpt-4o-mini, gemini-flash), upgrade as needed.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import structlog

from src.config.settings import get_settings
from src.models.extraction import ExtractionResult, ExtractionSchema

log = structlog.get_logger()


def _schema_to_json_spec(schema: ExtractionSchema) -> str:
    """Convert ExtractionSchema to a JSON spec string for the LLM prompt."""
    fields_spec = []
    for f in schema.fields:
        spec = {"name": f.name, "type": f.type}
        if f.required:
            spec["required"] = True
        fields_spec.append(spec)

    if schema.list_selector:
        return json.dumps({
            "type": "array",
            "items": {"type": "object", "properties": {
                f["name"]: {"type": f["type"]} for f in fields_spec
            }},
            "description": schema.description or "Extract one object per item.",
        }, indent=2)

    return json.dumps({
        "type": "object",
        "properties": {
            f["name"]: {"type": f["type"]} for f in fields_spec
        },
        "description": schema.description or "Extract these fields from the content.",
    }, indent=2)


def _strip_html_to_text(html: str, max_chars: int = 30000) -> str:
    """Strip HTML to plain text, truncate to max_chars."""
    from selectolax.parser import HTMLParser

    tree = HTMLParser(html)
    # Remove script and style tags
    for tag in tree.css("script, style, noscript"):
        tag.decompose()

    text = tree.text(separator="\n", strip=True)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n[... truncated]"
    return text


async def get_openrouter_config(org_id: str | None = None) -> tuple[str, str]:
    """Get OpenRouter API key and model, checking org-level config first.

    Priority: org DB record → env var fallback.
    Returns (api_key, model) or raises ValueError if no key configured.
    """
    settings = get_settings()

    # Check org-level config first
    if org_id:
        try:
            from src.db.pool import get_pool

            pool = await get_pool()
            row = await pool.fetchrow(
                "SELECT openrouter_api_key, llm_model FROM organizations WHERE id = $1",
                org_id,
            )
            if row and row["openrouter_api_key"]:
                return row["openrouter_api_key"], row["llm_model"] or settings.llm_extraction_model
        except Exception:
            log.debug("openrouter_org_lookup_failed", org_id=org_id)

    # Fall back to env var
    if settings.openrouter_api_key:
        return settings.openrouter_api_key, settings.llm_extraction_model

    raise ValueError("No OpenRouter API key configured — set one in Settings → AI Extraction or via OPENROUTER_API_KEY env var")


class LLMExtractor:
    """Extract structured data from content using LLMs via OpenRouter."""

    def __init__(self, org_id: str | None = None) -> None:
        self._client = None
        self._org_id = org_id

    def _get_client(self, api_key: str | None = None):
        """Lazy-init OpenAI client pointed at OpenRouter."""
        if api_key:
            # Org-specific key — create a fresh client (don't cache)
            from openai import AsyncOpenAI

            return AsyncOpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")

        if self._client is None:
            from openai import AsyncOpenAI

            settings = get_settings()
            if not settings.openrouter_api_key:
                raise ValueError(
                    "OPENROUTER_API_KEY not set — AI extraction disabled"
                )

            self._client = AsyncOpenAI(
                api_key=settings.openrouter_api_key,
                base_url="https://openrouter.ai/api/v1",
            )
        return self._client

    async def extract(
        self,
        content: str,
        schema: ExtractionSchema,
        instructions: str = "",
    ) -> ExtractionResult:
        """Extract structured data from text content using an LLM.

        Args:
            content: Plain text content to extract from.
            schema: Extraction schema defining the fields.
            instructions: Optional additional instructions for the LLM.

        Returns:
            ExtractionResult with extracted data.
        """
        # Resolve API key and model (org-level → env var fallback)
        try:
            api_key, model_used = await get_openrouter_config(self._org_id)
        except ValueError:
            settings = get_settings()
            api_key = settings.openrouter_api_key
            model_used = settings.llm_extraction_model
            if not api_key:
                raise

        settings = get_settings()
        client = self._get_client(api_key if self._org_id else None)

        json_spec = _schema_to_json_spec(schema)

        system_prompt = (
            "You are a precise data extraction assistant. "
            "Extract the requested fields from the content and return ONLY valid JSON. "
            "Do not include any other text, explanation, or markdown formatting. "
            "If a field cannot be found, use null."
        )

        user_prompt = f"""Extract data according to this JSON schema:

{json_spec}

{f"Additional instructions: {instructions}" if instructions else ""}

Content to extract from:
{content[:30000]}"""

        data: dict | list = {}

        for attempt in range(2):  # Retry once on invalid JSON
            try:
                response = await client.chat.completions.create(
                    model=model_used,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    max_tokens=settings.llm_extraction_max_tokens,
                    temperature=0.0,
                )

                raw = response.choices[0].message.content or ""

                # Strip markdown fences if present
                raw = raw.strip()
                if raw.startswith("```"):
                    raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
                if raw.endswith("```"):
                    raw = raw[:-3]
                raw = raw.strip()

                data = json.loads(raw)
                break  # Success

            except json.JSONDecodeError:
                if attempt == 0:
                    log.warning(
                        "llm_extraction_invalid_json",
                        model=model_used,
                        attempt=attempt + 1,
                        raw_preview=raw[:200] if raw else "",
                    )
                    continue
                # Second attempt also failed — return what we have
                log.error(
                    "llm_extraction_json_failed",
                    model=model_used,
                    raw_preview=raw[:200] if raw else "",
                )
                data = {"_extraction_errors": ["LLM returned invalid JSON"]}

            except Exception as e:
                log.error(
                    "llm_extraction_error",
                    model=model_used,
                    error=str(e),
                    error_type=type(e).__name__,
                )
                data = {"_extraction_errors": [str(e)]}
                break

        # Count fields
        if isinstance(data, list):
            all_found = set()
            for item in data:
                if isinstance(item, dict):
                    all_found.update(item.keys())
            fields_found = len(all_found)
        elif isinstance(data, dict):
            fields_found = len([
                v for v in data.values() if v is not None
            ])
        else:
            fields_found = 0

        found_names = set()
        if isinstance(data, dict):
            found_names = set(data.keys())
        elif isinstance(data, list) and data:
            for item in data:
                if isinstance(item, dict):
                    found_names.update(item.keys())

        fields_missing = [
            f.name for f in schema.fields
            if f.required and f.name not in found_names
        ]

        return ExtractionResult(
            schema_name=schema.name,
            data=data,
            url="",
            extracted_at=datetime.now(UTC),
            fields_found=fields_found,
            fields_missing=fields_missing,
            mode="ai",
        )

    async def extract_from_html(
        self,
        html: str,
        schema: ExtractionSchema,
        instructions: str = "",
    ) -> ExtractionResult:
        """Extract from HTML by stripping to text first."""
        text = _strip_html_to_text(html)
        return await self.extract(text, schema, instructions)

    async def extract_freeform(self, content: str, prompt: str) -> dict:
        """Extract data using only a natural language prompt — no schema needed.

        The LLM decides the output structure based on the prompt.
        Returns a plain dict (parsed JSON from the LLM).
        """
        # Resolve API key and model (org-level → env var fallback)
        try:
            api_key, model_used = await get_openrouter_config(self._org_id)
        except ValueError:
            settings = get_settings()
            api_key = settings.openrouter_api_key
            model_used = settings.llm_extraction_model
            if not api_key:
                raise

        settings = get_settings()
        client = self._get_client(api_key if self._org_id else None)

        system_prompt = (
            "You are a precise data extraction assistant. "
            "Extract information from the content based on the user's request "
            "and return ONLY valid JSON. No explanation, no markdown, no code fences. "
            "If a requested piece of information cannot be found, use null."
        )

        user_prompt = f"Request: {prompt}\n\nContent:\n{content[:30000]}"
        raw = ""

        for attempt in range(2):
            try:
                response = await client.chat.completions.create(
                    model=model_used,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    max_tokens=settings.llm_extraction_max_tokens,
                    temperature=0.0,
                )
                raw = (response.choices[0].message.content or "").strip()
                if raw.startswith("```"):
                    raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
                if raw.endswith("```"):
                    raw = raw[:-3]
                return json.loads(raw.strip())

            except json.JSONDecodeError:
                if attempt == 0:
                    log.warning("llm_freeform_invalid_json", model=model_used, raw_preview=raw[:200])
                    continue
                log.error("llm_freeform_json_failed", model=model_used, raw_preview=raw[:200])
                return {"_extraction_errors": ["LLM returned invalid JSON"], "_raw": raw[:500]}

            except Exception as e:
                log.error("llm_freeform_error", model=model_used, error=str(e))
                return {"_extraction_errors": [str(e)]}
