import re
from datetime import UTC, datetime
from uuid import UUID

from src.models.scraped_data import ArticleMetadata, DataType, ScrapedData
from src.workers.base import BaseWorker

# Minimum word count for a page to be considered a real article
_MIN_WORD_COUNT = 50

# Phrases that indicate a soft-block / challenge page (case-insensitive)
_SOFT_BLOCK_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"captcha",
        r"are you a robot",
        r"access denied",
        r"verifying you are human",
        r"please enable javascript",
        r"enable cookies",
        r"checking your browser",
        r"ddos protection by",
        r"ray id",                  # Cloudflare fingerprint
        r"just a moment",           # Cloudflare challenge page title
        r"your ip has been blocked",
        r"too many requests",
    ]
]

# Error-page title markers
_ERROR_TITLE_MARKERS = ("error", "404", "not found", "page not found", "403", "forbidden")


class ArticleParserWorker(BaseWorker):
    """Extracts metadata from individual article pages."""

    def __init__(
        self, domain: str, job_id: str,
        pool: object | None = None, org_id: str | None = None,
        user_id: str | None = None, tier_override: str | None = None,
    ):
        super().__init__(
            domain=domain, job_id=job_id, pool=pool, org_id=org_id,
            user_id=user_id, tier_override=tier_override,
        )

    def _is_soft_blocked(self, html: str, title: str | None) -> bool:
        """Detect soft blocks: 200-status CAPTCHA pages, cookie walls, JS challenges.

        Checks both the raw HTML body and the page title for known challenge phrases.
        """
        # Check title first (cheap)
        if title:
            for pattern in _SOFT_BLOCK_PATTERNS:
                if pattern.search(title):
                    return True

        # Scan a leading slice of the HTML body — challenge pages put markers near the top
        sample = html[:4000]
        for pattern in _SOFT_BLOCK_PATTERNS:
            if pattern.search(sample):
                return True

        return False

    def _is_quality_content(self, word_count: int, title: str | None, excerpt: str | None) -> tuple[bool, str]:
        """Return (passes, reason) for content quality gate.

        Rejects:
        - Pages with fewer than _MIN_WORD_COUNT words
        - Error pages (404, 403, etc.) identified by title
        - Pages with no title AND no excerpt AND low word count
        """
        if title and any(m in title.lower() for m in _ERROR_TITLE_MARKERS):
            return False, f"error_page_title: {title}"

        if word_count < _MIN_WORD_COUNT:
            return False, f"too_short: {word_count} words"

        return True, "ok"

    async def execute(self, urls: list[str]) -> list[ScrapedData]:
        if not urls:
            self.log.info("no_articles_to_parse")
            return []

        self.log.info("parsing_articles", count=len(urls))
        results: list[ScrapedData] = []
        skipped_soft_block = 0
        skipped_quality = 0

        for url in urls:
            try:
                fetch_result = await self.fetch_page(url)

                # Hard block (HTTP 403/429/503 or tiny body)
                if fetch_result.blocked:
                    self.log.debug("skipping_hard_blocked", url=url, status=fetch_result.status_code)
                    continue

                from src.scraping.parser.html_parser import HtmlParser

                parser = HtmlParser(fetch_result.html, url)
                title = parser.extract_title()

                # Soft-block detection (200-status CAPTCHA / challenge pages)
                if self._is_soft_blocked(fetch_result.html, title):
                    self.log.warning(
                        "skipping_soft_blocked",
                        url=url,
                        title=title,
                        html_size=len(fetch_result.html),
                    )
                    skipped_soft_block += 1
                    continue

                content = parser.extract_content()
                word_count = parser.count_words()
                excerpt = parser.extract_meta("description")

                # Content quality gate
                passes, reason = self._is_quality_content(word_count, title, excerpt)
                if not passes:
                    self.log.debug("skipping_low_quality", url=url, reason=reason)
                    skipped_quality += 1
                    continue

                metadata = ArticleMetadata(
                    author=parser.extract_meta("author"),
                    categories=parser.extract_categories(),
                    word_count=word_count,
                    excerpt=excerpt,
                    content=content,
                )

                record = {
                    "job_id": UUID(self.job_id),
                    "domain": self.domain,
                    "data_type": DataType.ARTICLE,
                    "url": url,
                    "title": title,
                    "metadata": metadata.model_dump(),
                }
                await self.export_results([record])

                results.append(
                    ScrapedData(
                        id=UUID(int=0),
                        job_id=UUID(self.job_id),
                        domain=self.domain,
                        data_type=DataType.ARTICLE,
                        url=url,
                        title=title,
                        metadata=metadata.model_dump(),
                        scraped_at=datetime.now(UTC),
                    )
                )

            except Exception as e:
                self.log.error("article_parse_error", url=url, error=str(e))

        self.log.info(
            "articles_parsed",
            count=len(results),
            attempted=len(urls),
            skipped_soft_block=skipped_soft_block,
            skipped_quality=skipped_quality,
            yield_pct=round(len(results) / len(urls) * 100, 1) if urls else 0,
        )
        return results
