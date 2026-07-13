"""Shared HTML→Markdown conversion and content hashing.

Consolidates the previously duplicated converters in ScraperService and
LLMExtractor so markdown output, LLM input, and persisted page content all
come from one implementation.
"""

import hashlib
import re

import structlog
from markdownify import markdownify as md
from selectolax.parser import HTMLParser

log = structlog.get_logger()

# Main-content containers, tried in order. Superset of both former lists.
DEFAULT_MAIN_SELECTORS = [
    "main",
    "article",
    "[role='main']",
    "#content",
    ".content",
    ".main-content",
    "#main-content",
    ".post-content",
    ".entry-content",
]

# Noise removed before conversion.
DEFAULT_NOISE_SELECTORS = (
    "nav, footer, header, aside, .sidebar, .ads, .cookie-banner, .popup, "
    "script, style, noscript, iframe"
)

_MARKDOWN_STRIP_TAGS = ["script", "style", "nav", "footer", "header", "aside"]


def find_main_html(
    html: str,
    selectors: list[str] | None = None,
    strip_noise: bool = True,
) -> str:
    """Return the HTML of the main-content container, noise removed.

    Falls back to <body> (then the whole document) when no selector matches.
    """
    tree = HTMLParser(html)

    node = None
    for selector in selectors or DEFAULT_MAIN_SELECTORS:
        node = tree.css_first(selector)
        if node:
            break

    target = node or tree.body
    if target is None:
        return html

    if strip_noise:
        for noise in target.css(DEFAULT_NOISE_SELECTORS):
            noise.decompose()

    return target.html or ""


def html_to_markdown(
    html: str,
    *,
    find_main: bool = True,
    selectors: list[str] | None = None,
    strip_images: bool = False,
    max_chars: int | None = None,
) -> str:
    """Convert HTML to clean Markdown.

    find_main: locate and isolate the main-content container first. Set False
        when the caller has already extracted the target node.
    strip_images: drop <img> tags (used for LLM input to save tokens).
    max_chars: truncate the result, appending a marker.
    """
    try:
        source_html = find_main_html(html, selectors) if find_main else html
        if not source_html:
            text = HTMLParser(html).text(separator="\n", strip=True)
            return _truncate(text, max_chars, marker=False)

        strip_tags = list(_MARKDOWN_STRIP_TAGS)
        if strip_images:
            strip_tags.append("img")

        markdown = md(source_html, heading_style="ATX", bullets="-", strip=strip_tags)
        markdown = re.sub(r"\n{3,}", "\n\n", markdown).strip()
        return _truncate(markdown, max_chars, marker=True)
    except Exception as e:  # pragma: no cover - defensive, mirrors prior behavior
        log.warning("html_to_markdown_failed", error=str(e))
        try:
            tree = HTMLParser(html)
            for tag in tree.css("script, style, noscript"):
                tag.decompose()
            return _truncate(tree.text(separator="\n", strip=True), max_chars, marker=False)
        except Exception:
            return _truncate(html, max_chars, marker=False)


def content_hash(markdown: str) -> str:
    """Stable SHA-256 over normalized markdown for cache/change detection.

    Normalizes whitespace so cosmetic reflow does not register as a change.
    """
    normalized = re.sub(r"\s+", " ", markdown or "").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _truncate(text: str, max_chars: int | None, marker: bool) -> str:
    if max_chars is None or len(text) <= max_chars:
        return text
    return text[:max_chars] + ("\n\n[... truncated]" if marker else "")
