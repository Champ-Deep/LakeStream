import re
from typing import Any, Dict, Optional

import structlog
from markdownify import markdownify as md
from selectolax.parser import HTMLParser

from src.models.scraping import FetchOptions, FetchResult, ScrapingTier
from src.services.escalation import EscalationService
from src.scraping.fetcher.factory import create_fetcher

log = structlog.get_logger()

class ScraperService:
    """Firecrawl-level native scraper for high-quality Markdown extraction."""

    def __init__(self, escalation_service: Optional[EscalationService] = None):
        self.escalation = escalation_service
        self.log = log.bind(service="ScraperService")

    async def scrape(
        self, 
        url: str, 
        tier: Optional[ScrapingTier] = None,
        only_main_content: bool = True
    ) -> Dict[str, Any]:
        """Scrape a page and return Markdown + Metadata."""
        
        # 1. Decide tier if not provided
        if tier is None and self.escalation:
            domain = re.sub(r'^https?://(www\.)?', '', url).split('/')[0]
            tier = await self.escalation.decide_initial_tier(domain)
        else:
            tier = tier or ScrapingTier.BASIC_HTTP

        # 2. Fetch content
        fetcher = create_fetcher(tier)
        result = await fetcher.fetch(url, FetchOptions())

        # 3. Handle Escalation if blocked
        if self.escalation and self.escalation.should_escalate(result):
            next_tier = self.escalation.get_next_tier(tier)
            if next_tier:
                self.log.info("escalating_scrape", url=url, from_tier=tier.value, to_tier=next_tier.value)
                return await self.scrape(url, tier=next_tier, only_main_content=only_main_content)

        if not result.html:
            return {"markdown": "", "metadata": {}, "success": False, "error": "No content found"}

        # 4. Extract Main Content & Metadata
        parser = HTMLParser(result.html)
        metadata = self._extract_metadata(parser, url)
        
        content_node = parser.body
        if only_main_content:
            content_node = self._find_main_content(parser)

        # 5. Convert to Markdown
        markdown = self._html_to_markdown(content_node.html) if content_node else ""

        return {
            "markdown": markdown,
            "metadata": metadata,
            "success": True,
            "tier_used": result.tier_used.value,
            "status_code": result.status_code
        }

    def _find_main_content(self, parser: HTMLParser) -> Any:
        """Find the main content area, stripping noise."""
        # Common main content selectors
        selectors = [
            "main", "article", "[role='main']", 
            "#content", ".content", ".post-content", ".entry-content",
            ".main-content", "#main-content"
        ]
        
        for selector in selectors:
            node = parser.css_first(selector)
            if node:
                # Remove known noise inside main
                for noise in node.css("nav, footer, header, aside, .sidebar, .ads, script, style"):
                    noise.decompose()
                return node
        
        # Fallback: remove global noise and return body
        body = parser.body
        if body:
            for noise in body.css("nav, footer, header, aside, .sidebar, .ads, script, style"):
                noise.decompose()
        return body

    def _html_to_markdown(self, html: str) -> str:
        """Convert HTML to clean Markdown."""
        content = md(
            html,
            heading_style="ATX",
            bullets="-",
            strip=['script', 'style', 'nav', 'footer', 'header', 'aside']
        )
        # Clean up excessive newlines
        content = re.sub(r'
{3,}', '

', content)
        return content.strip()

    def _extract_metadata(self, parser: HTMLParser, url: str) -> Dict[str, Any]:
        """Extract OG, Schema, and meta tags."""
        meta = {
            "url": url,
            "title": "",
            "description": "",
            "og_title": "",
            "og_description": "",
            "og_image": "",
            "canonical": "",
            "author": ""
        }

        title_node = parser.css_first("title")
        if title_node:
            meta["title"] = title_node.text().strip()

        # Meta tags
        for m in parser.css("meta"):
            name = m.attributes.get("name", "").lower()
            prop = m.attributes.get("property", "").lower()
            content = m.attributes.get("content", "")

            if name == "description": meta["description"] = content
            elif prop == "og:title": meta["og_title"] = content
            elif prop == "og:description": meta["og_description"] = content
            elif prop == "og:image": meta["og_image"] = content
            elif name == "author": meta["author"] = content

        link_canonical = parser.css_first("link[rel='canonical']")
        if link_canonical:
            meta["canonical"] = link_canonical.attributes.get("href", "")

        return meta
