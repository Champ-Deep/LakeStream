"""Shared custom-schema extraction with CSS→AI fallback.

Single implementation of the css / ai / auto extraction ladder, used by both
the synchronous /scrape/extract route and the async ContentWorker job path so
custom-schema extraction behaves identically in both.
"""

from src.models.extraction import ExtractionResult, ExtractionSchema
from src.scraping.parser.schema_extractor import SchemaExtractor

# Coverage below this fraction triggers the AI fallback in "auto" mode.
_AUTO_AI_THRESHOLD = 0.5


class AIUnavailableError(Exception):
    """Raised when AI extraction is required but no OpenRouter key is configured."""


async def extract_with_fallback(
    html: str,
    url: str,
    schema: ExtractionSchema,
    mode: str = "auto",
    org_id: str | None = None,
    instructions: str = "",
) -> ExtractionResult:
    """Run schema extraction using the requested mode.

    - css: CSS selectors only (deterministic).
    - ai: LLM only.
    - auto: CSS first; fall back to LLM when field coverage < 50%.

    Raises AIUnavailableError if AI is needed but not configured, so callers can
    choose to surface an error (sync route) or degrade gracefully (worker).
    """
    css_result: ExtractionResult | None = None

    if mode in ("css", "auto"):
        css_result = SchemaExtractor(html, url).extract(schema)
        css_result.mode = "css"
        if mode == "css":
            return css_result
        coverage = css_result.fields_found / len(schema.fields) if schema.fields else 1.0
        if coverage >= _AUTO_AI_THRESHOLD:
            return css_result

    # mode == "ai", or auto with insufficient CSS coverage
    from src.services.llm_extractor import LLMExtractor, get_openrouter_config

    try:
        await get_openrouter_config(org_id)
    except ValueError as e:
        raise AIUnavailableError(str(e)) from e

    result = await LLMExtractor(org_id=org_id).extract_from_html(html, schema, instructions)
    result.url = url
    result.mode = "ai"
    return result
