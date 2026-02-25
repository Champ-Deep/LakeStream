# PRD: LakeCurrent Integration into LakeStream

## References

- **LakeCurrent repo:** https://github.com/Champ-Deep/LakeCurrent (commit `64d85bc`)
- **Scraper repo:** https://github.com/Champ-Deep/lake-b2b-scraper

---

## 1. Problem Statement

The LakeStream is domain-focused — a user must already know which domain to scrape. There is no way to go from a question ("insurtech startups using React") to scraped business intelligence. Users must manually find domains, then feed them to the scraper one by one.

LakeCurrent is a self-hosted search API (Brave Search alternative) that returns structured web results from multiple engines. By connecting LakeCurrent to the scraper, we create a **query-to-intelligence pipeline**: an agent asks a question, LakeCurrent finds relevant domains, and the scraper extracts business data from them automatically.

This is the missing link that turns the scraper from a manual tool into an autonomous discovery engine for LakeB2B's verification, qualification, sales, and customer service agents.

---

## 2. Goals & Non-Goals

### Goals
- Agents can submit a **search query** and get back scraped business intelligence (contacts, tech stack, pricing, etc.) without knowing domains upfront
- Search-to-scrape jobs run through the existing arq queue, cost tracking, and webhook pipeline
- Tracked searches (recurring) work like tracked domains — periodically re-run, scrape new discoveries
- LakeCurrent runs as a **separate service** — the scraper calls its HTTP API
- Zero changes to LakeCurrent itself — the scraper is the only codebase modified

### Non-Goals
- Merging the two codebases into one
- Replacing Firecrawl for intra-domain URL discovery (Firecrawl maps pages within a domain; LakeCurrent finds domains)
- Building a custom search engine — LakeCurrent already handles that

---

## 3. Architecture Overview

```
Agent / n8n / External System
         |
         v
+--------------------------------------------------+
|           LakeStream (FastAPI)              |
|                                                   |
|  POST /api/discover/search                        |
|    -> LakeCurrentClient.search(query)             |
|    -> extract unique domains from result URLs     |
|    -> enqueue scrape jobs per domain              |
|                                                   |
|  POST /api/discover/tracked                       |
|    -> save search query + schedule                |
|    -> cron re-runs search, scrapes new domains    |
|                                                   |
|  GET  /api/discover/status/{discovery_id}         |
|    -> status of search + all child scrape jobs    |
|                                                   |
|  Existing endpoints unchanged:                    |
|    POST /api/scrape/execute                       |
|    GET  /api/scrape/status/{job_id}               |
|    etc.                                           |
+------------+-------------------------------------+
             | HTTP (internal Docker network)
             v
+----------------------------+
|   LakeCurrent (FastAPI)    |
|   GET /search?q=...        |
|   GET /health              |
|   Port 8001                |
+----------------------------+
```

**Key principle:** LakeCurrent is a service dependency, like PostgreSQL or Redis. The scraper calls it over HTTP. They share a Docker network but are otherwise independent.

---

## 4. LakeCurrent API Contract (reference for client implementation)

The scraper's `LakeCurrentClient` must implement calls against this contract:

### `GET /search`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `q` | string | *required* | Search query (1-500 chars) |
| `mode` | string | `auto` | `auto`, `filter`, or `glimpse` |
| `categories` | string | - | LakeFilter categories (e.g. `news`) |
| `language` | string | - | LakeFilter language filter |
| `pageno` | int | `1` | Page number (1-100) |
| `limit` | int | `5` | Max results (1-50) |

**Response (200):**
```json
{
  "query": "insurtech startups",
  "results": [
    {
      "url": "https://example.com/about",
      "title": "Example InsurTech",
      "snippet": "Leading insurance technology provider...",
      "engine": "google",
      "score": 3.5,
      "published_date": "2024-06-15"
    }
  ],
  "suggestions": ["insurtech companies 2024"],
  "answers": []
}
```

**Error codes:** 502 (LakeFilter down), 503 (LakeGlimpse down), 500 (internal), 422 (invalid params)

### `GET /health`

**Response (200):**
```json
{
  "status": "healthy",
  "components": { "LakeFilter": "ok", "LakeGlimpse": "ok" }
}
```

---

## 5. New API Endpoints (in Scraper)

### `POST /api/discover/search`

Submit a search-driven discovery + scrape job.

**Request:**
```json
{
  "query": "B2B SaaS companies using React",
  "search_mode": "auto",
  "search_pages": 3,
  "results_per_page": 10,
  "data_types": ["contact", "tech_stack", "pricing"],
  "template_id": "generic",
  "max_pages_per_domain": 50,
  "priority": 5
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `query` | string | *required* | Search query for LakeCurrent |
| `search_mode` | string | `"auto"` | LakeCurrent mode: `auto`, `filter`, `glimpse` |
| `search_pages` | int | `3` | Number of search result pages to fetch (1-10) |
| `results_per_page` | int | `10` | Results per page from LakeCurrent (1-50) |
| `data_types` | list[string] | *required* | What to extract from discovered domains |
| `template_id` | string | `"generic"` | Scraper template |
| `max_pages_per_domain` | int | `50` | Max pages to scrape per discovered domain |
| `priority` | int | `5` | Job priority (1-10) |

**Response (202):**
```json
{
  "discovery_id": "uuid",
  "query": "B2B SaaS companies using React",
  "status": "searching",
  "message": "Discovery job queued. Searching for domains..."
}
```

**Processing flow:**
1. Call LakeCurrent `/search` for each page (1 through `search_pages`)
2. Extract unique root domains from all result URLs
3. Deduplicate against `domain_metadata` (skip recently scraped)
4. Create one `scrape_job` per unique domain
5. Enqueue each scrape job via arq
6. Track all child jobs under the parent `discovery_id`

### `GET /api/discover/status/{discovery_id}`

**Response:**
```json
{
  "discovery_id": "uuid",
  "query": "B2B SaaS companies using React",
  "status": "scraping",
  "domains_found": 12,
  "domains_scraped": 5,
  "domains_skipped": 2,
  "domains_pending": 5,
  "search_results_count": 30,
  "child_jobs": [
    {
      "job_id": "uuid",
      "domain": "example.com",
      "status": "completed",
      "pages_scraped": 23,
      "cost_usd": 0.002
    }
  ],
  "total_cost_usd": 0.014,
  "created_at": "2025-...",
  "completed_at": null
}
```

**Status values:** `searching` -> `scraping` -> `completed` / `failed`

### `POST /api/discover/tracked`

Set up a recurring search-to-scrape schedule.

**Request:**
```json
{
  "query": "new fintech startups 2025",
  "search_mode": "auto",
  "search_pages": 2,
  "results_per_page": 10,
  "data_types": ["contact", "tech_stack"],
  "scrape_frequency": "weekly",
  "max_pages_per_domain": 50,
  "webhook_url": "https://hooks.example.com/new-fintechs"
}
```

**Response (201):**
```json
{
  "tracked_search_id": "uuid",
  "query": "new fintech startups 2025",
  "scrape_frequency": "weekly",
  "next_run_at": "2025-03-03T00:00:00Z",
  "is_active": true
}
```

**Behavior:** On each scheduled run:
1. Execute the search query against LakeCurrent
2. Extract domains from results
3. Skip domains already scraped in previous runs (tracked via `discovered_domains` junction)
4. Scrape only newly discovered domains
5. Send results to webhook

### `GET /api/discover/tracked`

List all tracked searches.

### `DELETE /api/discover/tracked/{tracked_search_id}`

Stop a tracked search.

---

## 6. Database Changes

### New table: `discovery_jobs`

```sql
CREATE TABLE discovery_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL,
    query TEXT NOT NULL,
    search_mode TEXT NOT NULL DEFAULT 'auto',
    search_pages INT NOT NULL DEFAULT 3,
    results_per_page INT NOT NULL DEFAULT 10,
    data_types TEXT[] NOT NULL,
    template_id TEXT NOT NULL DEFAULT 'generic',
    max_pages_per_domain INT NOT NULL DEFAULT 50,
    status TEXT NOT NULL DEFAULT 'searching',  -- searching, scraping, completed, failed
    domains_found INT NOT NULL DEFAULT 0,
    domains_skipped INT NOT NULL DEFAULT 0,
    search_results JSONB,  -- raw LakeCurrent results for reference
    error_message TEXT,
    total_cost_usd NUMERIC(10,6) NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX idx_discovery_jobs_org ON discovery_jobs(org_id);
CREATE INDEX idx_discovery_jobs_status ON discovery_jobs(status);
```

### New table: `discovery_job_domains`

Links a discovery job to the scrape jobs it spawned.

```sql
CREATE TABLE discovery_job_domains (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    discovery_id UUID NOT NULL REFERENCES discovery_jobs(id),
    domain TEXT NOT NULL,
    scrape_job_id UUID REFERENCES scrape_jobs(id),
    source_url TEXT NOT NULL,       -- the search result URL this domain came from
    source_title TEXT,
    source_snippet TEXT,
    source_score FLOAT,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending, scraping, completed, skipped
    skip_reason TEXT,               -- e.g. "recently scraped", "blocked domain"
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_djd_discovery ON discovery_job_domains(discovery_id);
CREATE INDEX idx_djd_domain ON discovery_job_domains(domain);
```

### New table: `tracked_searches`

```sql
CREATE TABLE tracked_searches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL,
    query TEXT NOT NULL,
    search_mode TEXT NOT NULL DEFAULT 'auto',
    search_pages INT NOT NULL DEFAULT 2,
    results_per_page INT NOT NULL DEFAULT 10,
    data_types TEXT[] NOT NULL,
    template_id TEXT NOT NULL DEFAULT 'generic',
    max_pages_per_domain INT NOT NULL DEFAULT 50,
    scrape_frequency TEXT NOT NULL DEFAULT 'weekly',  -- daily, weekly, biweekly, monthly
    webhook_url TEXT,
    is_active BOOLEAN NOT NULL DEFAULT true,
    last_run_at TIMESTAMPTZ,
    next_run_at TIMESTAMPTZ,
    total_runs INT NOT NULL DEFAULT 0,
    total_domains_discovered INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_tracked_searches_next ON tracked_searches(next_run_at) WHERE is_active = true;
```

---

## 7. New Modules (in Scraper)

### `src/services/lakecurrent.py` -- LakeCurrentClient

HTTP client for calling LakeCurrent's API. Uses `httpx.AsyncClient`.

```python
import httpx
from urllib.parse import urlparse
from dataclasses import dataclass

@dataclass
class SearchResult:
    url: str
    title: str
    snippet: str
    engine: str | None
    score: float | None
    published_date: str | None
    domain: str  # extracted from url

@dataclass
class SearchResponse:
    query: str
    results: list[SearchResult]
    suggestions: list[str]
    answers: list[str]

class LakeCurrentClient:
    def __init__(self, base_url: str, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout)

    async def search(
        self,
        query: str,
        mode: str = "auto",
        pageno: int = 1,
        limit: int = 10,
        categories: str | None = None,
        language: str | None = None,
    ) -> SearchResponse:
        params = {"q": query, "mode": mode, "pageno": pageno, "limit": limit}
        if categories:
            params["categories"] = categories
        if language:
            params["language"] = language
        r = await self._client.get(f"{self.base_url}/search", params=params)
        r.raise_for_status()
        data = r.json()
        results = []
        for item in data.get("results", []):
            parsed = urlparse(item["url"])
            results.append(SearchResult(
                url=item["url"],
                title=item.get("title", ""),
                snippet=item.get("snippet", ""),
                engine=item.get("engine"),
                score=item.get("score"),
                published_date=item.get("published_date"),
                domain=parsed.netloc.removeprefix("www."),
            ))
        return SearchResponse(
            query=data.get("query", query),
            results=results,
            suggestions=data.get("suggestions", []),
            answers=data.get("answers", []),
        )

    async def health(self) -> dict:
        r = await self._client.get(f"{self.base_url}/health")
        return r.json()

    async def search_pages(
        self,
        query: str,
        pages: int = 3,
        per_page: int = 10,
        mode: str = "auto",
    ) -> list[SearchResult]:
        """Fetch multiple pages and return all results."""
        all_results = []
        for page in range(1, pages + 1):
            resp = await self.search(query, mode=mode, pageno=page, limit=per_page)
            all_results.extend(resp.results)
            if len(resp.results) < per_page:
                break  # no more results
        return all_results

    async def close(self):
        await self._client.aclose()
```

**Key method:** `search_pages()` handles multi-page fetching and returns a flat list of results with `domain` already extracted from each URL.

### `src/services/domain_extractor.py` -- Domain deduplication logic

```python
from urllib.parse import urlparse

def extract_unique_domains(
    results: list[SearchResult],
    skip_domains: set[str] | None = None,
) -> dict[str, SearchResult]:
    """
    Returns a dict of {domain: best_result} with deduplication.
    Picks the highest-scored result per domain.
    Filters out domains in skip_domains set.
    """
    skip = skip_domains or set()
    domain_map: dict[str, SearchResult] = {}
    for result in results:
        if result.domain in skip:
            continue
        existing = domain_map.get(result.domain)
        if existing is None or (result.score or 0) > (existing.score or 0):
            domain_map[result.domain] = result
    return domain_map
```

### `src/api/discover.py` -- New API routes

New router with the three endpoints (`/search`, `/tracked`, `/status/{id}`) described in Section 5. Follows the same patterns as existing `src/api/scrape.py`.

### `src/queue/discover_jobs.py` -- Background job processor

```python
async def process_discovery_job(ctx, discovery_id: str):
    """
    arq job that:
    1. Calls LakeCurrentClient.search_pages()
    2. Extracts unique domains
    3. Checks domain_metadata for recently scraped (skip if < 7 days)
    4. Creates scrape_jobs for each new domain
    5. Enqueues each scrape_job via arq
    6. Updates discovery_jobs status
    """
```

---

## 8. Configuration Changes

### New env vars (scraper `.env`):

```env
# LakeCurrent Search API
LAKECURRENT_BASE_URL=http://lakecurrent-backend:8001
LAKECURRENT_TIMEOUT=15.0
LAKECURRENT_ENABLED=true

# Discovery settings
DISCOVERY_MAX_SEARCH_PAGES=10
DISCOVERY_SKIP_RECENT_DAYS=7
DISCOVERY_MAX_DOMAINS_PER_QUERY=50
```

### Scraper `Settings` class additions:

```python
lakecurrent_base_url: str = "http://lakecurrent-backend:8001"
lakecurrent_timeout: float = 15.0
lakecurrent_enabled: bool = True
discovery_max_search_pages: int = 10
discovery_skip_recent_days: int = 7
discovery_max_domains_per_query: int = 50
```

---

## 9. Infrastructure / Docker

### Shared Docker network (recommended)

The scraper and LakeCurrent each keep their own `docker-compose.yml`. They connect via a shared external Docker network.

**LakeCurrent** `docker-compose.yml` -- add external network:
```yaml
networks:
  lakecurrent:
    driver: bridge
  lakeb2b:
    external: true  # shared with scraper
```
Attach the `backend` service to both networks.

**Scraper** `docker-compose.yml` -- add external network:
```yaml
networks:
  default:
    driver: bridge
  lakeb2b:
    external: true
```

**Setup:** `docker network create lakeb2b` before starting either stack.

The scraper calls LakeCurrent at `http://lakecurrent-backend:8001` via the shared `lakeb2b` network. No ports need to be exposed to the host for this internal communication.

### Health check integration

Add LakeCurrent to the scraper's `/api/health` endpoint:

```python
# In scraper's health check
lakecurrent_status = "disabled"
if settings.lakecurrent_enabled:
    try:
        status = await lakecurrent_client.health()
        lakecurrent_status = status.get("status", "unknown")
    except Exception:
        lakecurrent_status = "unreachable"
```

---

## 10. End-to-End Data Flow

### One-shot discovery flow:

```
1. Agent calls:  POST /api/discover/search
   { "query": "insurtech startups", "data_types": ["contact", "tech_stack"] }

2. Scraper creates discovery_jobs row (status: "searching")

3. arq worker picks up process_discovery_job:
   a. Calls LakeCurrent: GET /search?q=insurtech+startups&pageno=1&limit=10
   b. Calls LakeCurrent: GET /search?q=insurtech+startups&pageno=2&limit=10
   c. Calls LakeCurrent: GET /search?q=insurtech+startups&pageno=3&limit=10
   d. Collects 30 results -> extracts 12 unique domains
   e. Checks domain_metadata: 2 scraped within 7 days -> skip
   f. Inserts 10 rows into discovery_job_domains
   g. Creates 10 scrape_jobs, enqueues each via arq
   h. Updates discovery_jobs: status="scraping", domains_found=12, domains_skipped=2

4. Each scrape_job runs through existing pipeline:
   DomainMapperWorker -> Fetcher -> Extract workers -> scraped_data

5. As scrape_jobs complete, discovery_job_domains rows updated
   When all done -> discovery_jobs status="completed", total_cost summed

6. Agent polls: GET /api/discover/status/{id} -> sees all results
   Or receives webhook with aggregated results
```

### Tracked search flow:

```
1. Agent calls:  POST /api/discover/tracked
   { "query": "new AI startups", "scrape_frequency": "weekly", ... }

2. Scraper creates tracked_searches row, next_run_at = now + 7 days

3. arq cron (check_tracked_searches) runs every 15 minutes:
   a. Finds tracked_searches where next_run_at <= now AND is_active
   b. For each: creates a discovery_job and runs the flow above
   c. Updates last_run_at, next_run_at, total_runs, total_domains_discovered
   d. Only scrapes domains NOT seen in previous runs for this tracked search
```

---

## 11. Agent Use Cases

### Verification Agent
```
Query: "company.com reviews trustpilot glassdoor"
-> Discovers review sites mentioning the company
-> Scrapes: contact info, published dates, content
-> Agent verifies company legitimacy from multiple sources
```

### Qualification Agent
```
Query: "SaaS companies series A funding 2024 healthcare"
-> Discovers recently funded healthtech startups
-> Scrapes: tech stack, pricing, team size, blog content
-> Agent qualifies leads based on extracted data
```

### Sales Agent
```
Tracked search: "companies hiring data engineers" (weekly)
-> Discovers companies actively hiring -> signals growth
-> Scrapes: contact pages, team info, tech stack
-> Sales agent gets fresh leads every week with context
```

### Customer Service Agent
```
Query: "competitor.com vs our-product comparison"
-> Discovers comparison/review pages
-> Scrapes: content, pricing mentions
-> Agent understands competitive positioning
```

---

## 12. YouTube Video Scraping

### Problem

Search results from LakeCurrent will include YouTube URLs (e.g. `youtube.com/watch?v=...`). The current scraper has no way to extract useful data from video content. For B2B agents, YouTube videos contain valuable intelligence: product demos, conference talks, company announcements, hiring pitches, and industry analysis.

### Approach

Add a **YouTubeWorker** that detects YouTube URLs in search results and extracts structured data using `youtube-transcript-api` (primary, lightweight, no API key) with optional `yt-dlp` for rich metadata.

### Data Extracted

| Field | Source | Description |
|-------|--------|-------------|
| `title` | yt-dlp | Video title |
| `description` | yt-dlp | Full video description |
| `channel_name` | yt-dlp | Channel/company name |
| `channel_url` | yt-dlp | Channel URL |
| `transcript` | youtube-transcript-api | Full text transcript (auto-generated or manual captions) |
| `published_date` | yt-dlp | Upload date |
| `duration` | yt-dlp | Video length |
| `view_count` | yt-dlp | Views |
| `tags` | yt-dlp | Video tags |
| `thumbnail_url` | yt-dlp | Thumbnail image |

### New data type: `youtube_video`

Add `"youtube_video"` to the scraper's `data_types` enum. When a discovery job finds YouTube URLs, it routes them to the YouTubeWorker instead of the standard domain scrape pipeline.

### Implementation

**New file: `src/workers/youtube.py`**

```python
from youtube_transcript_api import YouTubeTranscriptApi
import yt_dlp

class YouTubeWorker(BaseWorker):
    """Extracts transcript + metadata from YouTube videos."""

    async def process(self, url: str) -> dict:
        video_id = self._extract_video_id(url)

        # 1. Get transcript (primary value)
        transcript_text = ""
        try:
            transcript = YouTubeTranscriptApi.get_transcript(video_id)
            transcript_text = " ".join(entry["text"] for entry in transcript)
        except Exception:
            pass  # ~15% of videos have no captions

        # 2. Get metadata via yt-dlp (no download)
        ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        return {
            "title": info.get("title"),
            "description": info.get("description"),
            "channel_name": info.get("channel"),
            "channel_url": info.get("channel_url"),
            "transcript": transcript_text,
            "published_date": info.get("upload_date"),
            "duration": info.get("duration"),
            "view_count": info.get("view_count"),
            "tags": info.get("tags", []),
            "thumbnail_url": info.get("thumbnail"),
        }
```

**New dependencies:**
```
youtube-transcript-api>=1.2.0
yt-dlp>=2024.0.0
```

### URL Detection Logic

In the discovery job processor, after extracting search results:

```python
YOUTUBE_DOMAINS = {"youtube.com", "www.youtube.com", "youtu.be", "m.youtube.com"}

def classify_result(result: SearchResult) -> str:
    """Returns 'youtube' or 'domain' based on URL."""
    if result.domain in YOUTUBE_DOMAINS:
        return "youtube"
    return "domain"
```

YouTube results get routed to YouTubeWorker. Domain results go through the normal scrape pipeline. Both are tracked under the same `discovery_job`.

### Rate Limiting

- 1-second delay between YouTube transcript requests
- yt-dlp metadata extraction is lightweight (no video download)
- Respect YouTube's rate limits -- exponential backoff on 429s

### Database Storage

YouTube results stored in `scraped_data` table with `data_type = 'youtube_video'` and the structured data in the `metadata` JSONB column. This requires no schema changes -- the existing table already supports flexible metadata.

### Agent Use Cases

```
Sales Agent:
  Query: "CEO keynote SaaS conference 2025"
  -> Finds conference talks on YouTube
  -> Extracts transcripts for mentions of pain points, budgets, strategy
  -> Agent builds personalized outreach based on what the CEO said publicly

Qualification Agent:
  Query: "company.com product demo"
  -> Finds product demo videos
  -> Extracts transcript to understand features, pricing mentions, positioning
  -> Agent qualifies whether company is a fit based on their own product descriptions

Verification Agent:
  Query: "company.com reviews walkthrough"
  -> Finds user review videos
  -> Extracts sentiment and specific complaints/praise from transcripts
```

---

## 13. LinkedIn Data Enrichment (Phased)

### Problem

LinkedIn is the richest source of B2B intelligence -- company profiles, employee data, job postings, and professional activity. However, LinkedIn aggressively blocks automated access. This is the "hairy audacious goal" -- the hardest integration, requiring a phased approach.

### Legal Context

- **hiQ v. LinkedIn (9th Circuit, 2019):** Scraping publicly accessible LinkedIn data does NOT violate the Computer Fraud and Abuse Act (CFAA)
- **LinkedIn ToS:** Prohibits bots/scrapers via contract law (not criminal law). Consequence is account bans, not lawsuits
- **Defensible approach:** Use search engine cached data + third-party enrichment APIs. Avoid authenticated scraping of private data

### Phased Implementation

#### Phase 1: Search Engine Discovery (Zero risk, implement now)

Use LakeCurrent to search `site:linkedin.com` queries. Google indexes public LinkedIn profiles -- you get profile snippets without hitting LinkedIn directly.

**How it works:**
```
Agent query: "site:linkedin.com CTO insurtech startups"
-> LakeCurrent searches via LakeFilter (Google, Bing, etc.)
-> Returns LinkedIn profile URLs + titles + snippets
-> Scraper stores: name, title, company (from snippet)
-> No LinkedIn auth, no ToS violation
```

**New data type: `linkedin_profile` (search-derived)**

The discovery pipeline already handles this -- LinkedIn URLs appear in search results alongside regular domains. Add classification logic:

```python
LINKEDIN_DOMAINS = {"linkedin.com", "www.linkedin.com"}

def classify_result(result: SearchResult) -> str:
    if result.domain in YOUTUBE_DOMAINS:
        return "youtube"
    if result.domain in LINKEDIN_DOMAINS:
        return "linkedin"
    return "domain"
```

For Phase 1, LinkedIn results are stored as-is from search snippets (title, snippet, URL). The snippet typically contains: name, headline, location, and sometimes current company.

**Stored in `scraped_data`** with `data_type = 'linkedin_profile'`:
```json
{
  "url": "https://www.linkedin.com/in/janedoe",
  "name": "Jane Doe",
  "headline": "CTO at InsurTech Corp",
  "snippet": "Jane Doe - CTO at InsurTech Corp. San Francisco Bay Area. 500+ connections...",
  "source": "search_engine_cache"
}
```

#### Phase 2: Third-Party Enrichment APIs (Low risk, moderate cost)

Once you have LinkedIn profile URLs from Phase 1, enrich them via established third-party APIs:

| Provider | Cost | What You Get |
|----------|------|--------------|
| **Proxycurl** | ~$0.01-0.05/profile | Full work history, skills, education, current company |
| **Apollo.io** | $49-249/mo | 275M+ contacts, email + phone, company data |
| **Hunter.io** | $49-399/mo | Email verification, company domain mapping |
| **Clearbit** | Usage-based | Company firmographics, tech stack, employee count |

**Implementation:**

```python
class LinkedInEnricherWorker(BaseWorker):
    """Enriches LinkedIn profile URLs via third-party APIs."""

    async def process(self, linkedin_url: str) -> dict:
        # Try Proxycurl first (most detailed for individual profiles)
        if settings.proxycurl_api_key:
            return await self._enrich_proxycurl(linkedin_url)
        # Fall back to Apollo (better for bulk)
        if settings.apollo_api_key:
            return await self._enrich_apollo(linkedin_url)
        # Store URL-only if no enrichment API configured
        return {"url": linkedin_url, "source": "unenriched"}
```

**New env vars:**
```env
# LinkedIn Enrichment (Phase 2) -- at least one required for enrichment
PROXYCURL_API_KEY=
APOLLO_API_KEY=
HUNTER_API_KEY=
```

**Cost control:** Add per-org enrichment budget limits. Track cost per lookup in `scraped_data.metadata.enrichment_cost`.

#### Phase 3: Job Posting Intelligence (Low risk, high value)

LinkedIn job postings are more publicly accessible than profiles. They signal:
- **Growth:** Company is hiring -> expanding
- **Tech stack:** Job descriptions list technologies
- **Budget:** Salary ranges indicate company spending power
- **Pain points:** Job descriptions describe problems they're solving

**Approach:** Search LakeCurrent for `site:linkedin.com/jobs "company name" "job title"` and extract job posting data from search engine cached snippets. Alternatively, use Google Jobs structured data.

This feeds directly into the existing `TechDetectorWorker` and signal system:

```
Signal: "Company X posted 5+ engineering jobs this month"
-> Trigger: job_posting count > threshold
-> Action: webhook to sales team
-> Context: tech stack from job descriptions
```

#### Phase 4: Authenticated Scraping (High risk, future)

Direct LinkedIn scraping with residential proxies and anti-detect browsers. This is the most powerful but most legally risky approach. **Only pursue with legal counsel approval.**

Requirements:
- Residential proxy rotation ($50-500/mo)
- Anti-detect browser infrastructure (Multilogin/GoLogin, $50-300/mo)
- Session persistence + human-like behavior patterns
- Rate limiting: max ~2,000 profiles/day across all sessions
- Accept account bans as operational cost

**Recommendation:** Defer Phase 4 until Phases 1-3 are exhausted. Most B2B use cases are well-served by search engine snippets + third-party enrichment.

### LinkedIn Data Model

```json
{
  "data_type": "linkedin_profile",
  "url": "https://www.linkedin.com/in/janedoe",
  "metadata": {
    "name": "Jane Doe",
    "headline": "CTO at InsurTech Corp",
    "location": "San Francisco Bay Area",
    "company": "InsurTech Corp",
    "company_linkedin_url": "https://www.linkedin.com/company/insurtechcorp",
    "source": "proxycurl",
    "enrichment_cost": 0.03,
    "enriched_at": "2025-02-25T12:00:00Z",
    "work_history": ["..."],
    "skills": ["..."],
    "education": ["..."]
  }
}
```

All stored in the existing `scraped_data` table's `metadata` JSONB column -- no schema changes needed.

---

## 14. Updated Architecture with YouTube + LinkedIn

```
Agent Query: "insurtech startups CTO"
         |
         v
+--------------------------------------------------------+
|           LakeStream (FastAPI)                    |
|                                                         |
|  POST /api/discover/search                              |
|    -> LakeCurrentClient.search(query)                   |
|    -> classify results by URL type                      |
|                                                         |
|    +--------------+---------------+---------------+     |
|    | Domain URLs  | YouTube URLs  | LinkedIn URLs |     |
|    | example.com  | youtu.be/...  | linkedin.com  |     |
|    +------+-------+-------+-------+-------+-------+     |
|           |               |               |             |
|    Existing scrape   YouTubeWorker   Phase 1: store     |
|    pipeline          (transcript     snippet data       |
|    (Firecrawl ->     + metadata)    Phase 2: enrich     |
|     extract)                        via Proxycurl       |
|           |               |               |             |
|           +---------------+---------------+             |
|                           |                             |
|                    scraped_data table                    |
|               (unified JSONB metadata)                  |
+---------------------------------------------------------+
```

---

## 15. Testing Strategy

### Unit tests (scraper repo):

1. **`test_lakecurrent_client.py`** -- Mock httpx responses, test `search()`, `search_pages()`, `health()`, error handling (timeout, 502, 503)
2. **`test_domain_extractor.py`** -- Test deduplication, skip logic, domain parsing edge cases (www prefix, subdomains, IP addresses)
3. **`test_result_classifier.py`** -- Test URL classification: YouTube, LinkedIn, regular domains, edge cases (youtu.be short links, linkedin.com/company vs /in/ vs /jobs/)
4. **`test_discover_api.py`** -- Test endpoint validation, response shapes, auth requirements
5. **`test_discover_jobs.py`** -- Mock LakeCurrentClient, test full discovery flow, verify correct routing (domain -> scrape, YouTube -> transcript, LinkedIn -> store)
6. **`test_youtube_worker.py`** -- Mock youtube-transcript-api and yt-dlp, test transcript extraction, metadata parsing, handling of videos without captions
7. **`test_linkedin_enricher.py`** -- Mock Proxycurl/Apollo API calls, test enrichment pipeline, test fallback when no API key configured

### Integration tests:

1. Spin up LakeCurrent stack + scraper stack on shared network
2. Submit a discovery job -> verify scrape jobs are created
3. Verify YouTube URLs route to YouTubeWorker and produce transcript data
4. Verify LinkedIn URLs are classified and stored correctly
5. Verify `/api/discover/status/{id}` returns correct aggregated status across all result types
6. Verify tracked search cron creates new discovery jobs on schedule

### Smoke test (manual):

```bash
# Start both stacks
docker network create lakeb2b
cd LakeCurrent && docker-compose up -d
cd lake-b2b-scraper && docker-compose up -d

# Verify connectivity
curl http://localhost:3001/api/health  # should show lakecurrent: healthy

# Run a discovery (mixed results: domains + YouTube + LinkedIn)
curl -X POST http://localhost:3001/api/discover/search \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "insurtech CTO interview", "data_types": ["contact", "tech_stack", "youtube_video", "linkedin_profile"]}'

# Check status -- should show domain scrapes + YouTube extractions + LinkedIn profiles
curl http://localhost:3001/api/discover/status/{discovery_id} \
  -H "Authorization: Bearer $TOKEN"
```

---

## 16. Configuration Summary (all new env vars)

```env
# LakeCurrent Search API
LAKECURRENT_BASE_URL=http://lakecurrent-backend:8001
LAKECURRENT_TIMEOUT=15.0
LAKECURRENT_ENABLED=true

# Discovery settings
DISCOVERY_MAX_SEARCH_PAGES=10
DISCOVERY_SKIP_RECENT_DAYS=7
DISCOVERY_MAX_DOMAINS_PER_QUERY=50

# YouTube extraction
YOUTUBE_TRANSCRIPT_DELAY_MS=1000
YOUTUBE_ENABLED=true

# LinkedIn enrichment (Phase 2 -- optional, at least one for enrichment)
PROXYCURL_API_KEY=
APOLLO_API_KEY=
HUNTER_API_KEY=
LINKEDIN_ENRICHMENT_ENABLED=false
LINKEDIN_ENRICHMENT_BUDGET_PER_ORG=100.00
```

---

## 17. Implementation Order

### Milestone 1: Core Discovery Pipeline (LakeCurrent integration)
1. Database migration -- Add `discovery_jobs`, `discovery_job_domains`, `tracked_searches` tables
2. `src/services/lakecurrent.py` -- LakeCurrentClient
3. `src/services/result_classifier.py` -- URL classification (domain / youtube / linkedin)
4. `src/services/domain_extractor.py` -- Domain dedup logic
5. `src/config/settings.py` -- Add LakeCurrent + discovery config fields
6. `src/api/discover.py` -- New API routes (`/search`, `/tracked`, `/status`)
7. `src/queue/discover_jobs.py` -- Background job processor with URL classification
8. `src/queue/worker.py` -- Register new job functions + cron
9. `src/api/router.py` -- Include discover router
10. Health check -- Add LakeCurrent to `/api/health`
11. Docker -- Shared `lakeb2b` network, update .env.example
12. Tests + documentation

### Milestone 2: YouTube Scraping
13. `src/workers/youtube.py` -- YouTubeWorker (transcript + metadata)
14. Add `youtube-transcript-api` and `yt-dlp` to dependencies
15. Wire YouTubeWorker into discovery job processor
16. Tests for YouTubeWorker

### Milestone 3: LinkedIn Phase 1 (Search Engine Data)
17. LinkedIn URL classification (already in result_classifier from step 3)
18. Store LinkedIn search snippets as `linkedin_profile` data type
19. Parse name/headline/company from snippet text

### Milestone 4: LinkedIn Phase 2 (Third-Party Enrichment)
20. `src/workers/linkedin_enricher.py` -- LinkedInEnricherWorker
21. Proxycurl/Apollo/Hunter API integrations
22. Cost tracking + per-org budget enforcement
23. Tests for enrichment pipeline

### Milestone 5: LinkedIn Phase 3 (Job Posting Intelligence)
24. Job posting search queries (`site:linkedin.com/jobs`)
25. Job data extraction and tech stack signal generation
26. Signal integration (hiring velocity triggers)
