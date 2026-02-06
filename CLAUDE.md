# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Lake B2B Internal Scraping System — a template-based web scraping platform for enriching B2B data at scale. Extracts blog URLs, articles, resources, pricing, contact info, and tech stack signals from B2B domains, feeding into Lake B2B's enrichment pipelines via n8n workflows and PostgreSQL.

**Status**: Phase A (scaffolding) + Phase B (functionality) complete. All models, workers, templates, API routes, and tests are implemented.

## Tech Stack

- **Runtime**: Python 3.12
- **API**: FastAPI + uvicorn
- **Job Queue**: arq (async Redis queue)
- **Browser Automation**: Playwright (headless scraping)
- **HTML Parsing**: selectolax (fast CSS selector extraction)
- **Scraping Tool**: Firecrawl CLI (wrapped via `asyncio.create_subprocess_exec`)
- **HTTP Client**: httpx (async)
- **Proxy**: Bright Data / Smartproxy (residential + datacenter)
- **Database**: PostgreSQL + asyncpg (raw SQL, no ORM)
- **Validation**: Pydantic v2
- **Logging**: structlog (structured JSON)
- **Orchestration**: n8n workflows triggered via `pg_notify` on new `scraped_data` inserts
- **Linting**: ruff (replaces black, isort, flake8)
- **Testing**: pytest + pytest-asyncio

## Common Commands

```bash
make dev          # uvicorn src.server:app --reload --port 3000
make worker       # arq src.queue.worker.WorkerSettings
make test         # pytest tests/
make lint         # ruff check src/ tests/
make format       # ruff format src/ tests/
make typecheck    # mypy src/
make migrate      # python -m src.db.migrate
make seed         # python -m src.db.seed
make docker-up    # docker-compose up -d (Postgres + Redis)
```

## Architecture

```
API (FastAPI) → Job Queue (arq/Redis) → Worker Pool → Proxy Service → PostgreSQL → n8n Enrichment
```

**Three-tier adaptive scraping** with automatic escalation:
1. **Basic HTTP** (~$0.0001/req, 60-70% success) — httpx, server-rendered HTML
2. **Headless Browser** (~$0.002/req, 90% success) — Playwright, JS-heavy SPAs
3. **Headless + Residential Proxy** (~$0.004/req, 95%+ success) — Playwright + proxy

Escalation logic in `src/services/escalation.py`: empty results or 403/429 → headless → CAPTCHA detected → residential proxy → manual review.

**Template-based design**: WordPress, HubSpot, Webflow, Generic, Directory templates in `src/templates/`. Each defines platform signals, CSS selectors, pagination strategy, and path patterns.

**Modular components**: `src/scraping/fetcher/`, `src/scraping/parser/`, `src/scraping/validator/`, `src/scraping/exporter/`.

## Directory Structure

```
src/
  server.py              # FastAPI app creation + lifespan
  config/                # Pydantic Settings, constants
  models/                # Pydantic models (job, scraped_data, template, scraping, etc.)
  api/routes/            # FastAPI route handlers (health, scrape, domains, templates)
  queue/                 # arq worker settings + job definitions
  workers/               # BaseWorker + domain_mapper, blog_extractor, article_parser,
                         #   contact_finder, tech_detector, resource_finder
  templates/             # BaseTemplate + wordpress, hubspot, webflow, generic, directory
  scraping/
    fetcher/             # http_fetcher, browser_fetcher, proxy_fetcher, factory
    parser/              # html_parser, url_classifier, contact_parser, tech_parser, resource_parser
    validator/           # url_validator, data_validator, email_validator
    exporter/            # pg_exporter, csv_exporter
  services/              # firecrawl, escalation, cost_tracker, rate_limiter, template_detector
  db/                    # asyncpg pool, migrations (.sql), queries (raw SQL)
  data/                  # tech_signatures, industries, job_functions
  utils/                 # logger, errors, url, retry, shell
tests/
  conftest.py            # Shared fixtures (sample HTML)
  unit/                  # URL classifier, email validator, HTML parser, WordPress template
```

## Firecrawl CLI

Primary scraping tool. Wrapped in `src/services/firecrawl.py` as async subprocess calls.

Key commands:
- `firecrawl search "query" -o .firecrawl/results.json --json` — web search
- `firecrawl scrape <url> -o .firecrawl/page.md` — single page extraction
- `firecrawl map <url> -o .firecrawl/urls.txt` — discover all URLs on a site

Store all Firecrawl output in `.firecrawl/` directory.

## Database Schema

Three PostgreSQL tables (migrations in `src/db/migrations/`):
- **scrape_jobs**: job tracking (domain, template_id, status, strategy_used, cost, duration)
- **scraped_data**: extracted content (data_type, url, title, metadata JSONB) + `pg_notify` trigger
- **domain_metadata**: per-domain stats (last strategy, block count, success rate, avg cost)

## Lake B2B Data Schema (Target Fields)

- First Name, Last Name, Job Title, Email Address (verified), Company Name
- Industry (mapped to Lake B2B's 50+ standard categories in `src/data/industries.py`)
- Revenue Range, Employee Count, Direct Dial / Mobile Number, LinkedIn URL
- Job title → function mapping in `src/data/job_functions.py`

## Key Files

- `ChampionInternalScraperPRD.md` — full product requirements document
- `pyproject.toml` — dependencies, tool configs (ruff, pytest, mypy)
- `docker-compose.yml` — Postgres 16 + Redis 7 for local dev
- `Makefile` — common command shortcuts

## Conventions

- Python 3.12+ features (type parameter syntax `def fn[T]()`, `StrEnum`, `X | None`)
- Pydantic v2 models for all data structures
- Raw SQL via asyncpg (no ORM) — queries in `src/db/queries/`
- ruff for linting (line length 100) and formatting
- pytest with `asyncio_mode = "auto"` — no need for `@pytest.mark.asyncio`
- Prioritize data accuracy over volume
- Cost tracking per domain — prefer cheaper tiers, escalate only on failure
- Stateless workers for horizontal scaling
- n8n integration via `pg_notify` trigger on `scraped_data` inserts
