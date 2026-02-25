# LakeStream Project Brain

This document serves as the persistent context and architectural guide for LakeStream — Lake B2B's B2B web scraping and data extraction platform.

## Core Objective
Build a robust, scalable, and efficient scraping platform to extract high-quality B2B data (blog content, articles, resources, pricing, contact info, tech stack signals) from target domains, feeding into Lake B2B's enrichment pipelines.

## Technical Stack
- **Runtime:** Python 3.12
- **API:** FastAPI + uvicorn
- **Job Queue:** arq (async Redis queue)
- **Browser Automation:** Playwright (headless scraping)
- **HTML Parsing:** selectolax (fast CSS selector extraction)
- **Scraping Engine:** Firecrawl CLI (wrapped via `asyncio.create_subprocess_exec`)
- **Search/Discovery:** LakeCurrent (self-hosted search API via httpx)
- **HTTP Client:** httpx (async)
- **Proxy:** Bright Data / Smartproxy (residential + datacenter)
- **Database:** PostgreSQL 16 + asyncpg (raw SQL, no ORM)
- **Validation:** Pydantic v2
- **Logging:** structlog (structured JSON)
- **Linting:** ruff (replaces black, isort, flake8)
- **Testing:** pytest + pytest-asyncio

## Architecture

```
API (FastAPI) → Job Queue (arq/Redis) → Worker Pool → Proxy Service → PostgreSQL → n8n Enrichment
                                                ↑
Discovery: LakeCurrent Search → Domain Extraction → Scrape Jobs
```

**Three-tier adaptive scraping** with automatic escalation:
1. Basic HTTP (~$0.0001/req) — httpx, server-rendered HTML
2. Headless Browser (~$0.002/req) — Playwright, JS-heavy SPAs
3. Headless + Residential Proxy (~$0.004/req) — Playwright + proxy

**Template-based extraction:** WordPress, HubSpot, Webflow, Generic, Directory templates define platform signals, CSS selectors, pagination, and path patterns.

## Lake B2B Data Schema (Target)
- `First Name`, `Last Name`, `Job Title`, `Email Address` (verified)
- `Company Name`, `Industry` (mapped to 50+ standard categories)
- `Revenue Range`, `Employee Count`
- `Direct Dial / Mobile Number`, `LinkedIn URL`

## Directory Structure
```
src/
  server.py              # FastAPI app creation + lifespan
  config/                # Pydantic Settings, constants
  models/                # Pydantic models (job, scraped_data, template, discovery, etc.)
  api/routes/            # Route handlers (health, scrape, domains, templates, discover)
  queue/                 # arq worker settings + job definitions
  workers/               # BaseWorker + domain_mapper, blog_extractor, article_parser, etc.
  templates/             # BaseTemplate + wordpress, hubspot, webflow, generic, directory
  scraping/
    fetcher/             # http_fetcher, browser_fetcher, proxy_fetcher, factory
    parser/              # html_parser, url_classifier, contact_parser, tech_parser
    validator/           # url_validator, data_validator, email_validator
    exporter/            # pg_exporter, csv_exporter
  services/              # firecrawl, lakecurrent, escalation, cost_tracker, rate_limiter
  db/                    # asyncpg pool, migrations (.sql), queries (raw SQL)
```

## Project Guidelines
- Prioritize data accuracy over volume
- Cost tracking per domain — prefer cheaper scraping tiers, escalate only on failure
- Stateless workers for horizontal scaling
- Raw SQL via asyncpg (no ORM) — queries in `src/db/queries/`
- Pydantic v2 models for all data structures
- ruff for linting (line length 100) and formatting
