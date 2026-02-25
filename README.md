# LakeStream

B2B web scraping and data extraction platform by Lake B2B.

## Features

- **Multi-tier adaptive scraping** with automatic escalation (HTTP → Headless → Proxy)
- **Template-based extraction** for WordPress, HubSpot, Webflow, and generic sites
- **Query-to-intelligence pipeline** via LakeCurrent search integration
- **Real-time job monitoring** with HTMX-powered dashboard
- **CSV export** and **n8n webhook integration**
- **Cost tracking** per domain with strategy optimization
- **Tracked domains & searches** for recurring scrape schedules

## Tech Stack

- **Runtime**: Python 3.12
- **API**: FastAPI + uvicorn
- **Job Queue**: arq (async Redis queue)
- **Database**: PostgreSQL + asyncpg (raw SQL)
- **Scraping Engine**: Firecrawl CLI with HTTP fallback
- **Browser Automation**: Playwright (headless)
- **HTML Parsing**: selectolax (fast CSS selectors)
- **Frontend**: HTMX + Alpine.js + Tailwind CSS

## Quick Start (Local Development)

### Prerequisites

- Python 3.12+
- Docker & Docker Compose
- PostgreSQL 16
- Redis 7

### Setup

```bash
# Clone repository
git clone https://github.com/Champ-Deep/lake-b2b-scraper.git
cd lake-b2b-scraper

# Create virtual environment
python3.12 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -e ".[dev]"

# Start PostgreSQL + Redis
make docker-up

# Run migrations
make migrate

# Start development servers (in separate terminals)
make dev      # FastAPI server on port 3001
make worker   # arq worker for job processing
```

Visit http://localhost:3001

## Deployment (Railway)

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/template)

### Manual Railway Setup

1. **Create Railway Project**
   ```bash
   railway login
   railway init
   ```

2. **Add Services**
   ```bash
   # Add PostgreSQL
   railway add --service postgres

   # Add Redis
   railway add --service redis
   ```

3. **Set Environment Variables**
   ```bash
   railway variables set DATABASE_URL=${{Postgres.DATABASE_URL}}
   railway variables set REDIS_URL=${{Redis.REDIS_URL}}
   railway variables set PORT=8000
   ```

4. **Deploy Web + Worker**
   ```bash
   # Deploy web service
   railway up

   # Add worker service (separate service in Railway dashboard)
   # Start command: arq src.queue.worker.WorkerSettings
   ```

### Required Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string | `postgresql://user:pass@host:5432/db` |
| `REDIS_URL` | Redis connection string | `redis://host:6379` |
| `PORT` | Web server port | `8000` |
| `JWT_SECRET` | JWT signing secret | `openssl rand -hex 32` |
| `MAX_CONCURRENT_JOBS` | Worker concurrency | `5` |

## Architecture

```
API (FastAPI) → Job Queue (arq/Redis) → Worker Pool → Proxy Service → PostgreSQL → n8n
```

### Three-tier Escalation Strategy

1. **Basic HTTP** (~$0.0001/req, 60-70% success)
2. **Headless Browser** (~$0.002/req, 90% success)
3. **Headless + Residential Proxy** (~$0.004/req, 95%+ success)

## Common Commands

```bash
make dev          # Start FastAPI dev server
make worker       # Start arq worker
make test         # Run test suite
make lint         # Check code with ruff
make format       # Format code with ruff
make typecheck    # Run mypy type checking
make migrate      # Run database migrations
make docker-up    # Start Postgres + Redis
make docker-down  # Stop containers
```

## API Endpoints

### Web Dashboard
- `GET /` - Dashboard
- `GET /jobs` - Job list
- `GET /jobs/new` - Create job form
- `GET /results` - Browse extracted data
- `GET /domains` - Domain analytics
- `GET /settings` - Webhook configuration

### REST API
- `POST /api/scrape/execute` - Create scrape job
- `GET /api/scrape/status/{job_id}` - Job status
- `POST /api/discover/search` - Search-driven domain discovery
- `GET /api/discover/status/{discovery_id}` - Discovery job status
- `POST /api/discover/tracked` - Set up recurring search
- `GET /api/export/csv/{job_id}` - Export job data as CSV
- `POST /api/webhook/trigger` - Start job via webhook (n8n)
- `GET /api/health` - Health check

## Project Structure

```
src/
  server.py              # FastAPI app
  config/                # Settings, constants
  models/                # Pydantic models
  api/routes/            # API endpoints + web routes
  queue/                 # arq worker settings + jobs
  workers/               # Domain mapper, extractors, parsers
  templates/             # Platform-specific templates
  scraping/              # Fetchers, parsers, validators
  services/              # Scraping engine, escalation, cost tracking
  db/                    # Migrations, queries (raw SQL)
  static/                # CSS, JS for web dashboard
```

## Testing

```bash
# Run all tests
make test

# Run specific test file
pytest tests/unit/scraping/test_url_classifier.py -v

# Run with coverage
pytest --cov=src --cov-report=html
```

## Lake B2B Data Schema

Target fields for enrichment:
- First Name, Last Name, Job Title, Email (verified)
- Company Name, Industry (50+ categories), Revenue Range
- Employee Count, Direct Dial, LinkedIn URL
- Job function mapping (C-Level, IT, Marketing, etc.)

## License

Proprietary - Lake B2B Internal Use Only

---

**Maintained by Champions Group Engineering**
