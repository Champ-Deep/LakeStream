# LakeStream vs Firecrawl (superseded)

> **This document has been superseded by [`SCRAPER_COMPARISON.md`](./SCRAPER_COMPARISON.md)** (2026-07-10), which covers Firecrawl, Apify, and the open-source field with fresh pricing and live-captured output samples.

The analysis previously in this file predated several LakeStream v2 features and contained claims that are no longer true, including:

- "LakeStream provides raw HTML — you'd need to post-process to get markdown" — v2 has a native markdown scraper (`src/services/scraper.py`).
- "LakeStream uses CSS selectors… less flexible" — v2 has full LLM extraction (`src/services/llm_extractor.py`, `/scrape/extract` modes `css`/`ai`/`auto`/`prompt`).
- Old tier names/prices (`basic_http` $0.0001 …) — current tiers are `lightpanda` $0.001 / `playwright` $0.003 / `playwright_proxy` $0.0035 (`src/config/constants.py`).
- Firecrawl `/v0` API references — Firecrawl is on v2, and its crawl/extract endpoints are job-based.

See `SCRAPER_COMPARISON.md` for the current comparison.
