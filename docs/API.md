# LakeStream API Reference

Base URL: `https://your-domain.com/api`

All endpoints are prefixed with `/api` unless noted otherwise. Web UI routes serve HTML and are not documented here.

---

## Authentication

Protected endpoints require a JWT bearer token:

```
Authorization: Bearer <token>
```

Tokens are obtained via signup or login. The web UI stores the token in an `access_token` cookie. The JWT payload contains `user_id`, `org_id`, and `role`.

---

## Health

### `GET /health`

Full health check of all system dependencies.

**Auth**: None

**Response**:
```json
{
  "status": "ok",
  "database": "connected",
  "redis": "connected",
  "lakecurrent": "enabled"
}
```

### `GET /ping`

Liveness probe (no external calls). Note: this is at root, not under `/api`.

---

## Auth

### `POST /auth/signup`

Create a new organization and user.

**Auth**: None

**Request**:
```json
{
  "email": "john@acme.com",
  "password": "SecurePass123!",
  "full_name": "John Doe",
  "org_name": "Acme Corp"
}
```

**Response** `200`:
```json
{
  "access_token": "eyJhbGc...",
  "token_type": "bearer",
  "user": {
    "id": "uuid",
    "email": "john@acme.com",
    "full_name": "John Doe",
    "org_id": "uuid",
    "org_name": "Acme Corp",
    "role": "org_owner",
    "team_id": null
  }
}
```

**Errors**: `400` email already exists.

### `POST /auth/login`

Authenticate with email and password.

**Auth**: None

**Request**:
```json
{
  "email": "john@acme.com",
  "password": "SecurePass123!"
}
```

**Response** `200`: Same as signup.

**Errors**: `401` invalid credentials, `403` account disabled.

### `GET /auth/me`

Get current user profile.

**Auth**: Required

**Response** `200`:
```json
{
  "id": "uuid",
  "email": "john@acme.com",
  "full_name": "John Doe",
  "org_id": "uuid",
  "org_name": "Acme Corp",
  "role": "org_owner",
  "team_id": null
}
```

### `POST /auth/logout`

Clear JWT cookie. Client should redirect to `/login`.

**Auth**: None

**Response** `200`: `{"message": "Logged out successfully"}`

---

## Scraping

### `POST /scrape/execute`

Submit a scrape job for a domain.

**Auth**: Required

**Request**:
```json
{
  "domain": "example.com",
  "template_id": null,
  "tier": null,
  "max_pages": 100,
  "data_types": ["blog_url", "article", "contact", "tech_stack", "resource", "pricing"],
  "priority": 5
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `domain` | string | required | Domain to scrape |
| `template_id` | string? | null | Template ID (auto-detect if null) |
| `tier` | string? | null | Tier override: `basic_http`, `playwright`, `playwright_proxy` |
| `max_pages` | int | 100 | Max pages to scrape (1-500) |
| `data_types` | string[] | all | Data types to extract |
| `priority` | int | 5 | Job priority (1-10) |

**Response** `202`:
```json
{
  "job_id": "uuid",
  "status": "pending",
  "message": "Scrape job enqueued"
}
```

### `GET /scrape/status/{job_id}`

Get status of a scrape job.

**Auth**: None

**Response** `200`:
```json
{
  "job_id": "uuid",
  "domain": "example.com",
  "status": "completed",
  "strategy_used": "playwright",
  "pages_scraped": 42,
  "cost_usd": 0.084,
  "duration_ms": 15000,
  "created_at": "2026-03-12T00:00:00Z",
  "completed_at": "2026-03-12T00:00:15Z",
  "error_message": null,
  "data_count": 37
}
```

Job statuses: `pending`, `running`, `completed`, `failed`.

---

## Discovery (Search-to-Scrape)

### `POST /discover/search`

Submit a search-driven discovery job. Uses LakeCurrent to find domains from a search query, then scrapes each domain.

**Auth**: Required

**Request**:
```json
{
  "query": "B2B data providers",
  "search_mode": "auto",
  "search_pages": 3,
  "results_per_page": 10,
  "data_types": ["article", "contact", "tech_stack"],
  "template_id": "generic",
  "max_pages_per_domain": 50,
  "priority": 5
}
```

**Response** `202`:
```json
{
  "discovery_id": "uuid",
  "query": "B2B data providers",
  "status": "pending",
  "message": "Discovery job enqueued"
}
```

### `GET /discover/status/{discovery_id}`

Get discovery job status including all child scrape jobs.

**Auth**: Required

**Response** `200`:
```json
{
  "discovery_id": "uuid",
  "query": "B2B data providers",
  "status": "completed",
  "domains_found": 15,
  "domains_scraped": 12,
  "domains_skipped": 3,
  "domains_pending": 0,
  "search_results_count": 30,
  "child_jobs": [
    {
      "job_id": "uuid",
      "domain": "example.com",
      "status": "completed",
      "skip_reason": null,
      "pages_scraped": 42,
      "cost_usd": 0.084
    }
  ],
  "total_cost_usd": 0.42,
  "created_at": "2026-03-12T00:00:00Z",
  "completed_at": "2026-03-12T00:05:00Z"
}
```

### `POST /discover/tracked`

Set up a recurring search-to-scrape schedule.

**Auth**: Required

**Request**:
```json
{
  "query": "B2B data providers",
  "scrape_frequency": "weekly",
  "search_pages": 2,
  "results_per_page": 10,
  "data_types": ["article", "contact"],
  "max_pages_per_domain": 50,
  "webhook_url": "https://hooks.example.com/data"
}
```

**Response** `201`:
```json
{
  "tracked_search_id": "uuid",
  "query": "B2B data providers",
  "scrape_frequency": "weekly",
  "next_run_at": "2026-03-19T00:00:00Z",
  "is_active": true
}
```

### `GET /discover/tracked`

List all tracked searches for the organization.

**Auth**: Required

### `DELETE /discover/tracked/{tracked_search_id}`

Stop a tracked search.

**Auth**: Required

---

## Export

### `GET /export/csv/{job_id}`

Export scraped data from a job as CSV.

**Auth**: None

**Response**: CSV file download. Columns include: domain, data_type, url, title, published_date, scraped_at, author, excerpt, word_count, categories, content, first_name, last_name, job_title, email, phone, linkedin_url, platform, frameworks, js_libraries, analytics, plan_name, price, billing_cycle, features, resource_type, description, download_url.

### `GET /export/csv?domain={domain}`

Export all scraped data as CSV, optionally filtered by domain.

**Auth**: None

### `GET /export/json/{job_id}`

Export scraped data from a job as JSON.

**Auth**: None

**Response**:
```json
{
  "job_id": "uuid",
  "domain": "example.com",
  "exported_at": "2026-03-12T00:00:00Z",
  "total_records": 42,
  "data": [
    {
      "id": "uuid",
      "domain": "example.com",
      "data_type": "tech_stack",
      "url": "https://example.com",
      "title": "Tech Stack: example.com",
      "published_date": null,
      "scraped_at": "2026-03-12T00:00:00Z",
      "metadata": {
        "og_title": "Example - Data Platform",
        "og_description": "Leading data provider...",
        "twitter_site": "@example",
        "favicon": "https://example.com/favicon.png",
        "platform": "WordPress",
        "analytics": ["Google Analytics"],
        "js_libraries": ["jQuery", "React"]
      }
    }
  ]
}
```

### `POST /export/webhook`

Send scraped data to a webhook URL.

**Auth**: None

**Request**:
```json
{
  "url": "https://hooks.example.com/data",
  "job_id": "uuid",
  "domain": null
}
```

Provide either `job_id` or `domain` (at least one required).

**Response** `200`:
```json
{
  "success": true,
  "records_sent": 42,
  "webhook_url": "https://hooks.example.com/data",
  "webhook_status": 200
}
```

---

## Domains

### `GET /domains`

List all scraped domains with metadata.

**Auth**: None

**Query params**: `limit` (default 50), `offset` (default 0), `sort_by` (default `last_scraped_at`).

**Response** `200`: Array of domain metadata objects.

### `GET /domains/{domain}/stats`

Get detailed statistics for a specific domain.

**Auth**: None

**Response** `200`:
```json
{
  "domain": "example.com",
  "last_successful_strategy": "playwright",
  "block_count": 0,
  "last_scraped_at": "2026-03-12T00:00:00Z",
  "success_rate": 95.0,
  "avg_cost_usd": 0.002,
  "notes": null
}
```

---

## Tracked Domains

### `POST /tracked/add`

Add a domain for automated scheduled scraping.

**Auth**: None

**Request**:
```json
{
  "domain": "example.com",
  "data_types": ["article", "contact", "tech_stack"],
  "scrape_frequency": "weekly",
  "max_pages": 100,
  "webhook_url": "https://hooks.example.com/data"
}
```

### `GET /tracked/`

List all actively tracked domains.

**Auth**: None

### `DELETE /tracked/{domain}`

Remove a domain from tracking.

**Auth**: None

---

## Signals (Intent Detection)

### `GET /signals/types`

List available signal types. Optional `category` filter: `people`, `company`, `technology`, `behavior`.

**Auth**: Required

### `POST /signals/`

Create a new intent signal.

**Auth**: Required

**Request**:
```json
{
  "name": "New CTO Detected",
  "description": "Alert when a new CTO is found",
  "is_active": true,
  "trigger_config": {
    "type": "job_title_change",
    "filters": {"title_contains": "CTO"}
  },
  "action_config": {
    "type": "webhook",
    "webhook_url": "https://hooks.example.com/signals"
  }
}
```

### `GET /signals/`

List all signals for the current organization. Optional `is_active` filter.

**Auth**: Required

### `GET /signals/{signal_id}`

Get a signal by ID.

**Auth**: Required

### `PATCH /signals/{signal_id}`

Update a signal. All fields optional.

**Auth**: Required

### `DELETE /signals/{signal_id}`

Delete a signal.

**Auth**: Required | **Response**: `204 No Content`

### `POST /signals/{signal_id}/test`

Test a signal (dry run).

**Auth**: Required

**Response** `200`:
```json
{
  "signal_id": "uuid",
  "would_fire": true,
  "matched_data": {"domain": "example.com", "contact": "..."},
  "match_count": 3,
  "message": "Signal would fire for 3 matches"
}
```

### `GET /signals/{signal_id}/executions`

Get execution history. Optional `limit` param (default 100).

**Auth**: Required

---

## Templates

### `GET /templates`

List all available scraping templates.

**Auth**: None

### `GET /templates/{template_id}`

Get a specific template by ID.

**Auth**: None

Available templates: `wordpress`, `hubspot`, `webflow`, `generic`, `directory`.

---

## Webhook (n8n Integration)

### `POST /webhook/trigger`

Trigger a scrape job via webhook.

**Auth**: None

**Request**:
```json
{
  "domain": "example.com",
  "data_types": ["article", "contact"],
  "max_pages": 100,
  "template_id": null
}
```

**Response** `200`:
```json
{
  "success": true,
  "job_id": "uuid",
  "status_url": "https://your-domain.com/api/scrape/status/uuid"
}
```

### `POST /webhook/test`

Test a webhook URL by sending a test payload.

**Auth**: None

**Request**: `{"url": "https://hooks.example.com/test"}`

### `POST /webhook/callback/{job_id}`

Receive callback data from external services.

**Auth**: None

---

## Settings

### `GET /settings/`

Get organization proxy settings.

**Auth**: None

### `PATCH /settings/`

Update proxy URL.

**Auth**: None

**Request**: `{"proxy_url": "http://user:pass@proxy:port"}`

---

## Data Types

| Type | Description |
|------|-------------|
| `blog_url` | Blog landing pages with article link lists |
| `article` | Individual blog articles with content |
| `contact` | People/team member information |
| `tech_stack` | Technology and framework detection |
| `resource` | Whitepapers, case studies, downloads |
| `pricing` | Pricing plans and features |

---

## Rich Metadata

Every scraped record includes rich metadata extracted from the page:

| Field | Source | Example |
|-------|--------|---------|
| `og_title` | `<meta property="og:title">` | "Acme - Data Platform" |
| `og_description` | `<meta property="og:description">` | "Leading data provider..." |
| `og_image` | `<meta property="og:image">` | "https://example.com/logo.png" |
| `og_url` | `<meta property="og:url">` | "https://example.com" |
| `twitter_card` | `<meta name="twitter:card">` | "summary_large_image" |
| `twitter_site` | `<meta name="twitter:site">` | "@example" |
| `twitter_title` | `<meta name="twitter:title">` | "Acme - Data Platform" |
| `twitter_image` | `<meta name="twitter:image">` | "https://example.com/card.png" |
| `description` | `<meta name="description">` | "Leading data provider..." |
| `keywords` | `<meta name="keywords">` | "data, B2B, analytics" |
| `author` | `<meta name="author">` | "John Doe" |
| `favicon` | `<link rel="icon">` | "https://example.com/favicon.ico" |
| `canonical_url` | `<link rel="canonical">` | "https://example.com/" |

---

## Scraping Tiers & Costs

| Tier | Method | Cost/request | Success Rate |
|------|--------|-------------|--------------|
| 1 | Basic HTTP (httpx) | ~$0.0001 | 60-70% |
| 2 | Headless Browser (Playwright) | ~$0.002 | 90% |
| 3 | Headless + Residential Proxy | ~$0.004 | 95%+ |

Automatic escalation: empty results or 403/429 triggers tier upgrade.

---

## Error Format

All errors return:
```json
{
  "detail": "Error message"
}
```

Standard HTTP status codes: `400`, `401`, `403`, `404`, `500`, `502`, `504`.
