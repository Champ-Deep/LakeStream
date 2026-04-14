"""LLM-powered structured extraction via OpenRouter.

Production-grade extractor with:
- Markdown-preserved HTML (tables, headings, links intact for LLM)
- Smart truncation (main content prioritized over nav/footer)
- Content-type-aware prompts (contact, pricing, article, tech_stack)
- Data-type-specific output schemas for consistent, mergeable output
- 60s API timeout to prevent job hangs
- Improved retry with explicit JSON guidance
- Token usage + cost logging
- Model fallback chain (primary → cheap reliable backup)
"""

from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime

import structlog

from src.config.settings import get_settings
from src.models.extraction import ExtractionResult, ExtractionSchema

log = structlog.get_logger()

# Cheap reliable fallback model when the configured model fails
_FALLBACK_MODEL = "google/gemini-2.0-flash-001"

# Max content length sent to LLM (characters)
_MAX_CONTENT_CHARS = 30000

# API call timeout in seconds
_API_TIMEOUT_SECONDS = 60


# --------------------------------------------------------------------------
# Data-type-specific prompts and output schemas
# --------------------------------------------------------------------------

_TYPE_PROMPTS: dict[str, str] = {
    "contact": (
        "Extract ALL people/contacts mentioned on this page. "
        "Look for names, job titles, email addresses, phone numbers, and LinkedIn URLs. "
        "Check headers, team sections, about sections, footers, and author bylines. "
        "Return every person found even if some fields are missing."
    ),
    "pricing": (
        "Extract ALL pricing plans and tiers from this page. "
        "For each plan find: plan name, price, billing cycle (monthly/annual/one-time), "
        "list of features, whether a free trial exists, and the CTA button text. "
        "Look in pricing tables, cards, comparison grids, and FAQ sections."
    ),
    "article": (
        "Extract the article content from this page. "
        "Find: author name, publication date, categories/tags, word count estimate, "
        "a short excerpt (first 2-3 sentences), and the full article text. "
        "Ignore navigation, ads, sidebar, and footer content."
    ),
    "tech_stack": (
        "Analyze this page to detect the technology stack. "
        "Identify: CMS/platform (WordPress, Shopify, etc.), JavaScript libraries, "
        "analytics tools (Google Analytics, Hotjar, etc.), marketing tools (HubSpot, Mailchimp, etc.), "
        "and frameworks (React, Next.js, etc.). "
        "Look at script references, meta tags, class naming patterns, and generator tags."
    ),
    "resource": (
        "Extract downloadable resources from this page. "
        "Find: resource title, type (whitepaper, ebook, case study, webinar, etc.), "
        "description, whether it's gated (requires form fill), and the download/access URL. "
        "Look for download buttons, resource cards, and CTA sections."
    ),
    "blog_url": (
        "Extract all blog article links from this page. "
        "Find every link that points to a blog post, article, or news item. "
        "Return the full URL and title for each article found. "
        "Look in article cards, post lists, recent posts sections, and archive links."
    ),
}

_TYPE_SCHEMAS: dict[str, dict] = {
    "contact": {
        "type": "object",
        "properties": {
            "people": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "first_name": {"type": "string"},
                        "last_name": {"type": "string"},
                        "job_title": {"type": "string"},
                        "email": {"type": "string"},
                        "phone": {"type": "string"},
                        "linkedin_url": {"type": "string"},
                    },
                },
            }
        },
    },
    "pricing": {
        "type": "object",
        "properties": {
            "plans": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "plan_name": {"type": "string"},
                        "price": {"type": "string"},
                        "billing_cycle": {"type": "string"},
                        "features": {"type": "array", "items": {"type": "string"}},
                        "has_free_trial": {"type": "boolean"},
                        "cta_text": {"type": "string"},
                    },
                },
            }
        },
    },
    "article": {
        "type": "object",
        "properties": {
            "author": {"type": "string"},
            "published_date": {"type": "string"},
            "categories": {"type": "array", "items": {"type": "string"}},
            "word_count": {"type": "integer"},
            "excerpt": {"type": "string"},
            "content": {"type": "string"},
        },
    },
    "tech_stack": {
        "type": "object",
        "properties": {
            "platform": {"type": "string"},
            "js_libraries": {"type": "array", "items": {"type": "string"}},
            "analytics": {"type": "array", "items": {"type": "string"}},
            "marketing_tools": {"type": "array", "items": {"type": "string"}},
            "frameworks": {"type": "array", "items": {"type": "string"}},
        },
    },
    "resource": {
        "type": "object",
        "properties": {
            "resources": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "resource_type": {"type": "string"},
                        "description": {"type": "string"},
                        "gated": {"type": "boolean"},
                        "download_url": {"type": "string"},
                    },
                },
            }
        },
    },
    "blog_url": {
        "type": "object",
        "properties": {
            "articles": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "url": {"type": "string"},
                    },
                },
            }
        },
    },
}


# --------------------------------------------------------------------------
# HTML → Markdown conversion (preserves structure for LLM)
# --------------------------------------------------------------------------

def _html_to_markdown(html: str, max_chars: int = _MAX_CONTENT_CHARS) -> str:
    """Convert HTML to clean Markdown, preserving tables/headings/links."""
    from selectolax.parser import HTMLParser

    try:
        tree = HTMLParser(html)

        for tag in tree.css("script, style, noscript, nav, footer, header, aside, "
                           ".sidebar, .ads, .cookie-banner, .popup, iframe"):
            tag.decompose()

        main = None
        for selector in ["main", "article", "[role='main']", "#content", ".content",
                         ".main-content", "#main-content", ".post-content", ".entry-content"]:
            main = tree.css_first(selector)
            if main:
                break

        source_html = main.html if main else (tree.body.html if tree.body else "")
        if not source_html:
            text = tree.text(separator="\n", strip=True)
            return text[:max_chars] if len(text) > max_chars else text

        from markdownify import markdownify as md

        markdown = md(
            source_html,
            heading_style="ATX",
            bullets="-",
            strip=["script", "style", "nav", "footer", "header", "aside", "img"],
        )

        markdown = re.sub(r"\n{3,}", "\n\n", markdown)
        markdown = markdown.strip()

        if len(markdown) > max_chars:
            markdown = markdown[:max_chars] + "\n\n[... truncated]"

        return markdown

    except Exception as e:
        log.warning("html_to_markdown_failed", error=str(e))
        try:
            tree = HTMLParser(html)
            for tag in tree.css("script, style, noscript"):
                tag.decompose()
            text = tree.text(separator="\n", strip=True)
            return text[:max_chars] if len(text) > max_chars else text
        except Exception:
            return html[:max_chars]


def _strip_html_to_text(html: str, max_chars: int = _MAX_CONTENT_CHARS) -> str:
    """Strip HTML to plain text (legacy)."""
    from selectolax.parser import HTMLParser

    tree = HTMLParser(html)
    for tag in tree.css("script, style, noscript"):
        tag.decompose()

    text = tree.text(separator="\n", strip=True)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n[... truncated]"
    return text


# --------------------------------------------------------------------------
# OpenRouter config
# --------------------------------------------------------------------------

async def get_openrouter_config(org_id: str | None = None) -> tuple[str, str]:
    """Get OpenRouter API key and model (org-level → env var fallback)."""
    settings = get_settings()

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

    if settings.openrouter_api_key:
        return settings.openrouter_api_key, settings.llm_extraction_model

    raise ValueError(
        "No OpenRouter API key configured — set one in Settings → AI Extraction "
        "or via OPENROUTER_API_KEY env var"
    )


# --------------------------------------------------------------------------
# Prompt builders
# --------------------------------------------------------------------------

def _build_extraction_prompt(
    content: str,
    data_type: str | None = None,
    instructions: str = "",
) -> tuple[str, str]:
    """Build system + user prompts, optionally tailored to data_type."""
    base_system = (
        "You are a precise data extraction assistant. "
        "Extract the requested information from the content and return ONLY valid JSON. "
        "No explanation, no markdown fences, no code blocks, no trailing text. "
        "If a field cannot be found, use null."
    )

    if data_type and data_type in _TYPE_PROMPTS:
        task = _TYPE_PROMPTS[data_type]
        schema_spec = json.dumps(_TYPE_SCHEMAS.get(data_type, {}), indent=2)
        user_prompt = (
            f"{task}\n\n"
            f"Return JSON matching this schema:\n{schema_spec}\n\n"
            f"{f'Additional instructions: {instructions}' if instructions else ''}\n\n"
            f"Content:\n{content}"
        )
    else:
        user_prompt = (
            f"Extract all structured data from this content and return as JSON.\n\n"
            f"{f'Instructions: {instructions}' if instructions else ''}\n\n"
            f"Content:\n{content}"
        )

    return base_system, user_prompt


def _schema_to_json_spec(schema: ExtractionSchema) -> str:
    """Convert ExtractionSchema to JSON spec for LLM prompt."""
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


def _clean_llm_json(raw: str) -> str:
    """Strip markdown fences and surrounding text from LLM JSON output."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    return raw.strip()


# --------------------------------------------------------------------------
# Main extractor
# --------------------------------------------------------------------------

class LLMExtractor:
    """Extract structured data from content using LLMs via OpenRouter."""

    def __init__(self, org_id: str | None = None) -> None:
        self._client = None
        self._org_id = org_id

    def _get_client(self, api_key: str | None = None):
        if api_key:
            from openai import AsyncOpenAI
            return AsyncOpenAI(
                api_key=api_key,
                base_url="https://openrouter.ai/api/v1",
                timeout=_API_TIMEOUT_SECONDS,
            )

        if self._client is None:
            from openai import AsyncOpenAI

            settings = get_settings()
            if not settings.openrouter_api_key:
                raise ValueError("OPENROUTER_API_KEY not set — AI extraction disabled")

            self._client = AsyncOpenAI(
                api_key=settings.openrouter_api_key,
                base_url="https://openrouter.ai/api/v1",
                timeout=_API_TIMEOUT_SECONDS,
            )
        return self._client

    async def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        api_key: str,
        model: str,
    ) -> dict:
        """Make an LLM call with retry, JSON cleaning, and model fallback."""
        client = self._get_client(api_key)
        settings = get_settings()

        models_to_try = [model]
        if model != _FALLBACK_MODEL:
            models_to_try.append(_FALLBACK_MODEL)

        last_error: Exception | None = None

        for current_model in models_to_try:
            for attempt in range(2):
                start = time.time()
                try:
                    retry_system = system_prompt
                    if attempt > 0:
                        retry_system += (
                            "\n\nIMPORTANT: Your previous response was not valid JSON. "
                            "Return ONLY a raw JSON object or array. "
                            "No markdown, no code fences, no explanation text."
                        )

                    response = await client.chat.completions.create(
                        model=current_model,
                        messages=[
                            {"role": "system", "content": retry_system},
                            {"role": "user", "content": user_prompt},
                        ],
                        max_tokens=settings.llm_extraction_max_tokens,
                        temperature=0.0,
                    )

                    duration_ms = int((time.time() - start) * 1000)

                    usage = response.usage
                    if usage:
                        log.info(
                            "llm_call",
                            model=current_model,
                            prompt_tokens=usage.prompt_tokens,
                            completion_tokens=usage.completion_tokens,
                            total_tokens=usage.total_tokens,
                            duration_ms=duration_ms,
                            attempt=attempt + 1,
                        )

                    raw = (response.choices[0].message.content or "").strip()
                    raw = _clean_llm_json(raw)
                    return json.loads(raw)

                except json.JSONDecodeError:
                    log.warning(
                        "llm_invalid_json",
                        model=current_model,
                        attempt=attempt + 1,
                        raw_preview=raw[:200] if raw else "",
                    )
                    last_error = ValueError(f"Invalid JSON from {current_model}")
                    continue

                except Exception as e:
                    duration_ms = int((time.time() - start) * 1000)
                    log.warning(
                        "llm_call_failed",
                        model=current_model,
                        error=str(e),
                        error_type=type(e).__name__,
                        duration_ms=duration_ms,
                        attempt=attempt + 1,
                    )
                    last_error = e
                    break

            if current_model == model and len(models_to_try) > 1:
                log.info("llm_model_fallback", from_model=model, to_model=_FALLBACK_MODEL)

        raise last_error or ValueError("LLM extraction failed")

    # ------------------------------------------------------------------
    # Schema-based extraction
    # ------------------------------------------------------------------

    async def extract(
        self,
        content: str,
        schema: ExtractionSchema,
        instructions: str = "",
    ) -> ExtractionResult:
        api_key, model_used = await get_openrouter_config(self._org_id)

        json_spec = _schema_to_json_spec(schema)

        system_prompt = (
            "You are a precise data extraction assistant. "
            "Extract the requested fields from the content and return ONLY valid JSON. "
            "No explanation, no markdown, no code fences. "
            "If a field cannot be found, use null."
        )
        user_prompt = (
            f"Extract data according to this JSON schema:\n\n{json_spec}\n\n"
            f"{f'Additional instructions: {instructions}' if instructions else ''}\n\n"
            f"Content to extract from:\n{content[:_MAX_CONTENT_CHARS]}"
        )

        try:
            data = await self._call_llm(system_prompt, user_prompt, api_key, model_used)
        except Exception as e:
            log.error("llm_schema_extraction_failed", error=str(e))
            data = {"_extraction_errors": [str(e)]}

        if isinstance(data, list):
            all_found = set()
            for item in data:
                if isinstance(item, dict):
                    all_found.update(item.keys())
            fields_found = len(all_found)
        elif isinstance(data, dict):
            fields_found = len([v for v in data.values() if v is not None])
        else:
            fields_found = 0

        found_names: set[str] = set()
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
        markdown = _html_to_markdown(html)
        return await self.extract(markdown, schema, instructions)

    # ------------------------------------------------------------------
    # Freeform extraction
    # ------------------------------------------------------------------

    async def extract_freeform(self, content: str, prompt: str) -> dict:
        api_key, model_used = await get_openrouter_config(self._org_id)

        system_prompt = (
            "You are a precise data extraction assistant. "
            "Extract information from the content based on the user's request "
            "and return ONLY valid JSON. No explanation, no markdown, no code fences. "
            "If a requested piece of information cannot be found, use null."
        )
        user_prompt = f"Request: {prompt}\n\nContent:\n{content[:_MAX_CONTENT_CHARS]}"

        try:
            return await self._call_llm(system_prompt, user_prompt, api_key, model_used)
        except Exception as e:
            log.error("llm_freeform_failed", error=str(e))
            return {"_extraction_errors": [str(e)]}

    # ------------------------------------------------------------------
    # Content-type-aware extraction
    # ------------------------------------------------------------------

    async def extract_by_type(
        self,
        html: str,
        data_type: str,
        instructions: str = "",
    ) -> dict:
        """Extract data using content-type-aware prompts with Markdown input."""
        api_key, model_used = await get_openrouter_config(self._org_id)

        markdown = _html_to_markdown(html)
        system_prompt, user_prompt = _build_extraction_prompt(
            markdown, data_type=data_type, instructions=instructions,
        )

        try:
            return await self._call_llm(system_prompt, user_prompt, api_key, model_used)
        except Exception as e:
            log.error(
                "llm_type_extraction_failed",
                data_type=data_type,
                error=str(e),
            )
            return {"_extraction_errors": [str(e)]}
