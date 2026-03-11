# LakeStream Production Deployment Guide

## Prerequisites

- PostgreSQL 16+ database
- Redis 7+ instance
- Python 3.12+ runtime (or Docker)
- `JWT_SECRET` generated: `openssl rand -hex 32`

## Quick Deploy (Railway)

Railway auto-detects `nixpacks.toml`. Just connect the repo and set env vars.

```bash
# Required env vars
JWT_SECRET=<your-generated-secret>
DATABASE_URL=<your-postgres-url>
REDIS_URL=<your-redis-url>

# Optional (proxy services)
BRIGHTDATA_PROXY_URL=<if-available>
SMARTPROXY_URL=<if-available>
CUSTOM_PROXY_URL=<if-available>
```

Both `web` and `worker` processes are defined in `Procfile`.

## Docker Deploy

```bash
# Build
docker build -t lakestream .

# Run (migrations auto-run via start.sh)
docker run -p 3000:3000 \
  -e JWT_SECRET=<secret> \
  -e DATABASE_URL=<postgres-url> \
  -e REDIS_URL=<redis-url> \
  lakestream
```

## Manual Deploy

```bash
# 1. Install
pip install -e .
playwright install chromium

# 2. Set environment
cp .env.example .env
# Edit .env with your values (JWT_SECRET is required)

# 3. Run migrations
python -m src.db.migrate

# 4. Start API (terminal 1)
uvicorn src.server:app --host 0.0.0.0 --port 3000

# 5. Start worker (terminal 2)
arq src.queue.worker.WorkerSettings
```

## Post-Deploy Validation

```bash
# 1. Health check
curl https://your-domain/api/ping

# 2. Run QA validation (from dev machine)
PYTHONPATH=. python scripts/qa_production_validation.py

# 3. Test a scrape
curl -X POST https://your-domain/api/scrape/execute \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"domain": "blog.hubspot.com", "data_types": ["blog_url", "article"], "max_pages": 5}'

# 4. Check job status
curl https://your-domain/api/scrape/status/<job_id>
```

## Monitoring

```sql
-- Scrape success rate (last 24h)
SELECT strategy_used, COUNT(*) as total,
  SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
  AVG(cost_usd) as avg_cost
FROM scrape_jobs
WHERE created_at > NOW() - INTERVAL '24 hours'
GROUP BY strategy_used;

-- Error rate
SELECT status, COUNT(*) FROM scrape_jobs
WHERE created_at > NOW() - INTERVAL '24 hours'
GROUP BY status;
```

## Architecture

```
Tier 1: BASIC_HTTP    ($0.0001/req) → httpx, server-rendered HTML
Tier 2: PLAYWRIGHT    ($0.003/req)  → Playwright + session persistence
Tier 3: PLAYWRIGHT_PROXY ($0.0035/req) → Playwright + proxy + session

Escalation: blocked/403/429 → next tier automatically
Session: Redis-backed cookies, 1-hour TTL, 5x speed on reuse
Rate limits: LinkedIn 5s, HubSpot 2s, WordPress 1.5s, default 1s
```

## Known Limitations

- LinkedIn Sales Navigator requires rotating residential proxies for scale (not included)
- Single proxy per request (no rotation pool)
- CAPTCHA solving not implemented (escalates to proxy instead)
- Cloudflare-protected sites may require Tier 3 with good proxy
