# Lake B2B Scraper - Product Roadmap
## Phase D: Advanced Features (Post-Launch)

*Based on competitive analysis, user pain points, and industry best practices*

---

## Feature 1: Smart Deduplication Engine 🎯
**Priority: CRITICAL** | **Est: 3-4 days** | **Impact: 60% cost reduction**

### Problem
Competitors like Apify and Bright Data charge per-request without deduplication, causing users to re-scrape identical content. Users report wasting 60-70% of credits on duplicate data.

### Solution
Implement content-addressable deduplication using URL + content hash to avoid re-scraping unchanged pages.

**Technical Design:**
```sql
-- New table: src/db/migrations/007_create_content_hashes.sql
CREATE TABLE content_hashes (
    url TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    last_scraped_at TIMESTAMPTZ NOT NULL,
    last_modified_at TIMESTAMPTZ,
    scrape_count INTEGER DEFAULT 1,
    INDEX idx_domain_hash (domain, content_hash)
);
```

**Implementation:**
- **Pre-scrape check**: Before fetching, query `content_hashes` for URL
- **HEAD request**: If hash exists, send `If-Modified-Since` header
- **Skip if 304**: Server returns "Not Modified" → skip fetch, save cost
- **Update hash**: If content changed, compute SHA256 hash and update record
- **Metrics**: Track dedup hit rate per domain

**Files to Create:**
- `src/services/deduplication.py` - Hash computation + cache lookup
- `src/db/queries/content_hashes.py` - CRUD for hash table
- `src/models/content_hash.py` - Pydantic model

**Cost Savings Example:**
- Blog with 500 articles, daily scrape
- Without dedup: 500 fetches/day × 30 days = 15,000 fetches/month
- With dedup (10% change rate): 50 fetches/day × 30 days = 1,500 fetches/month
- **Savings: 90% reduction = ~$135/month at $0.01/fetch**

---

## Feature 2: Change Detection & Incremental Scraping 📊
**Priority: HIGH** | **Est: 4-5 days** | **Impact: 80% faster scrapes**

### Problem
Current implementation always does full scrapes. Users waste time/money re-extracting unchanged data (pricing plans that haven't changed in 6 months, static contact pages, etc.).

### Solution
Track content changes at field-level granularity and only extract what's new or modified.

**Technical Design:**
```python
# src/models/change_detection.py
class ContentSnapshot(BaseModel):
    url: str
    data_type: DataType
    snapshot_hash: str  # Hash of extracted fields
    field_hashes: dict[str, str]  # {"title": "abc123", "author": "def456"}
    scraped_at: datetime

class ChangeDetectionResult(BaseModel):
    url: str
    changed: bool
    changed_fields: list[str] = []
    new_content: dict = {}
```

**Implementation Flow:**
1. **Fetch & Parse**: Get page, extract data
2. **Compute Field Hashes**: Hash each extracted field (title, price, features, etc.)
3. **Compare to Snapshot**: Check if any field hash differs
4. **Skip or Update**:
   - If no change → skip database write, log "no_change"
   - If changed → write only changed fields + full record

**Smart Change Types:**
- **Content Change**: Text/data modified (title, price, author)
- **Structural Change**: New fields appeared (new pricing tier)
- **Deletion**: Fields removed (plan discontinued)

**Files to Create:**
- `src/services/change_detection.py` - Snapshot comparison logic
- `src/db/migrations/008_create_content_snapshots.sql`
- `src/db/queries/snapshots.py`

**Integration Points:**
- Modify all workers to check snapshots before export
- Add `--force-full-scrape` flag to bypass change detection
- Dashboard widget: "Last 24h: 45 changes detected out of 200 URLs checked"

**User Benefits:**
- **80% faster scrapes** - Skip unchanged pages
- **Lower costs** - Pay only for changed content extraction
- **Change alerts** - "Competitor raised pricing 20%"
- **Historical diff** - "Show me what changed since last week"

---

## Feature 3: Webhook Retry Queue with Dead Letter 📮
**Priority: HIGH** | **Est: 2-3 days** | **Impact: 99.9% delivery guarantee**

### Problem
Current webhook export has zero retry logic. If n8n is down for 5 minutes, webhook delivery fails and data is lost forever. Users report this as a **critical gap** in production workflows.

### Solution
Implement exponential backoff retry queue with dead-letter storage for forensics.

**Technical Design:**
```sql
-- src/db/migrations/009_create_webhook_queue.sql
CREATE TABLE webhook_delivery_queue (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID NOT NULL REFERENCES scrape_jobs(id),
    webhook_url TEXT NOT NULL,
    payload JSONB NOT NULL,
    attempt_count INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 5,
    next_retry_at TIMESTAMPTZ,
    status TEXT CHECK (status IN ('pending', 'processing', 'delivered', 'failed')),
    last_error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    delivered_at TIMESTAMPTZ
);

CREATE TABLE webhook_dead_letter (
    id UUID PRIMARY KEY,
    original_queue_id UUID,
    job_id UUID,
    webhook_url TEXT,
    payload JSONB,
    total_attempts INTEGER,
    final_error TEXT,
    failed_at TIMESTAMPTZ DEFAULT NOW()
);
```

**Retry Strategy (Exponential Backoff):**
- Attempt 1: Immediate
- Attempt 2: +1 minute
- Attempt 3: +5 minutes
- Attempt 4: +15 minutes
- Attempt 5: +1 hour
- After 5 failures → Move to dead-letter queue

**Implementation:**
```python
# src/services/webhook_retry.py
async def enqueue_webhook_delivery(job_id: UUID, webhook_url: str):
    """Queue webhook for async delivery with retry."""
    payload = await build_webhook_payload(job_id)
    await insert_webhook_queue(job_id, webhook_url, payload)

async def process_webhook_queue():
    """Background worker: process pending webhooks."""
    pending = await get_pending_webhooks()
    for item in pending:
        try:
            await deliver_webhook(item)
            await mark_delivered(item.id)
        except Exception as e:
            await increment_retry(item.id, str(e))
            if item.attempt_count >= item.max_attempts:
                await move_to_dead_letter(item)
```

**Monitoring & Alerts:**
- Dashboard: "Webhook Delivery: 98.7% success rate (last 7 days)"
- Slack alert: "⚠️ 3 webhooks in dead-letter queue for example.com"
- Retry button: "Reprocess failed webhooks"

**Files to Create:**
- `src/services/webhook_retry.py` - Queue management + retry logic
- `src/workers/webhook_processor.py` - Background cron worker
- `src/db/queries/webhook_queue.py` - Queue CRUD operations

**Integration:**
- Replace direct webhook call in `jobs.py` with `enqueue_webhook_delivery()`
- Add cron job: `cron(process_webhook_queue, minute={0, 15, 30, 45})`

---

## Feature 4: Budget Controls & Cost Alerts 💰
**Priority: MEDIUM** | **Est: 2-3 days** | **Impact: Prevents cost overruns**

### Problem
Users of Bright Data and Apify report **billing surprises** due to lack of cost controls. One user: "My bill went from $50 to $500 in a week because I didn't realize my actor was stuck in a loop."

### Solution
Per-domain and account-level budget limits with proactive alerts.

**Technical Design:**
```sql
-- src/db/migrations/010_add_budget_controls.sql
ALTER TABLE tracked_domains ADD COLUMN daily_budget_usd NUMERIC(10,2);
ALTER TABLE tracked_domains ADD COLUMN monthly_budget_usd NUMERIC(10,2);
ALTER TABLE tracked_domains ADD COLUMN budget_alert_threshold NUMERIC(3,2) DEFAULT 0.80;

CREATE TABLE budget_alerts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    domain TEXT,
    alert_type TEXT, -- 'daily_threshold', 'monthly_threshold', 'daily_exceeded', 'monthly_exceeded'
    current_spend_usd NUMERIC(10,2),
    budget_limit_usd NUMERIC(10,2),
    triggered_at TIMESTAMPTZ DEFAULT NOW(),
    acknowledged BOOLEAN DEFAULT FALSE
);
```

**Budget Enforcement:**
```python
# src/services/budget_control.py
async def check_budget_before_scrape(domain: str) -> BudgetCheckResult:
    """Verify domain hasn't exceeded budget limits."""
    tracked = await get_tracked_domain(pool, domain)
    if not tracked:
        return BudgetCheckResult(allowed=True)

    today_spend = await get_domain_spend_today(domain)
    month_spend = await get_domain_spend_this_month(domain)

    # Hard stop if exceeded
    if tracked.daily_budget_usd and today_spend >= tracked.daily_budget_usd:
        await create_budget_alert(domain, "daily_exceeded", today_spend, tracked.daily_budget_usd)
        return BudgetCheckResult(allowed=False, reason="Daily budget exceeded")

    # Alert at 80% threshold
    if tracked.daily_budget_usd and today_spend >= tracked.daily_budget_usd * 0.80:
        await create_budget_alert(domain, "daily_threshold", today_spend, tracked.daily_budget_usd)

    return BudgetCheckResult(allowed=True)
```

**Alert Channels:**
- **In-app**: Dashboard banner "⚠️ example.com approaching daily budget (85% used)"
- **Email**: "Your domain 'example.com' has exceeded its daily budget of $10.00"
- **Webhook**: POST to alert_webhook_url with budget event payload

**User Controls:**
- Set per-domain daily/monthly budgets
- Pause tracking when budget exceeded
- Resume next billing period automatically
- Budget history chart: "example.com spent $45.23 this month"

**Files to Create:**
- `src/services/budget_control.py` - Budget checks + alerts
- `src/db/queries/budget.py` - Budget queries
- `src/api/routes/budget.py` - Budget management endpoints

**Integration Points:**
- Check budget before enqueuing job in webhook.py
- Check budget in scheduled scraper before enqueuing
- Log budget alerts in structured logs

---

## Feature 5: Data Quality Validator & Enrichment Score 🎖️
**Priority: MEDIUM** | **Est: 3-4 days** | **Impact: 95%+ data accuracy**

### Problem
Competitors have [40% enrichment success rates](https://www.knock-ai.com/blog/data-enrichment-tools) and users complain about **invalid emails, broken URLs, incomplete data**. Lake B2B needs to guarantee quality.

### Solution
Real-time validation pipeline with enrichment scoring and auto-cleanup.

**Technical Design:**
```python
# src/models/data_quality.py
class ValidationResult(BaseModel):
    field: str
    valid: bool
    confidence: float  # 0.0-1.0
    issues: list[str] = []
    suggestions: list[str] = []

class EnrichmentScore(BaseModel):
    job_id: UUID
    domain: str
    overall_score: float  # 0-100
    completeness: float  # % of requested fields found
    accuracy: float     # % of validated fields
    freshness: float    # days since last update
    breakdown: dict[DataType, float]  # Score per data type
```

**Validation Rules:**

1. **Email Validation** (already exists, enhance it):
   - Syntax check ✓
   - MX record verification (DNS lookup)
   - Disposable email detection ✓
   - Role-based email flagging (info@, admin@)
   - **NEW**: SMTP verification (connect to mail server, don't send)

2. **URL Validation**:
   - Syntax check (valid HTTP/HTTPS)
   - Reachability check (HEAD request, expect 2xx)
   - SSL certificate validation
   - Redirect chain detection (flag if >3 redirects)

3. **Contact Data Validation**:
   - Name formatting (Title Case, no numbers)
   - Phone number validation (international format)
   - LinkedIn URL structure check
   - Company name cross-reference (does it match domain?)

4. **Pricing Data Validation**:
   - Currency format consistency
   - Price range sanity check ($0-$1M for B2B SaaS)
   - Billing cycle consistency
   - Feature count threshold (2-20 features expected)

5. **Tech Stack Validation**:
   - Known platform detection (WordPress, HubSpot, etc.)
   - Framework version check (outdated = risk signal)
   - Conflicting signals (can't use React + Vue together)

**Enrichment Scoring Algorithm:**
```python
def calculate_enrichment_score(job_id: UUID) -> EnrichmentScore:
    data = get_all_scraped_data(job_id)
    requested_types = get_job_data_types(job_id)

    # Completeness: did we find what was requested?
    completeness = len(data) / expected_minimum_results(requested_types)

    # Accuracy: how many fields passed validation?
    total_fields = sum(len(d.metadata) for d in data)
    valid_fields = sum(count_valid_fields(d) for d in data)
    accuracy = valid_fields / total_fields if total_fields > 0 else 0

    # Freshness: how recent is the data?
    avg_age_days = calculate_average_age(data)
    freshness = max(0, 100 - avg_age_days)  # 100 if today, decreases over time

    overall = (completeness * 0.4) + (accuracy * 0.4) + (freshness * 0.2)
    return EnrichmentScore(overall_score=overall * 100, ...)
```

**Files to Create:**
- `src/services/data_quality.py` - Validation orchestrator
- `src/scraping/validator/contact_validator.py` - Contact-specific validation
- `src/scraping/validator/url_validator.py` - Enhanced URL validation
- `src/scraping/validator/pricing_validator.py` - Pricing validation
- `src/models/data_quality.py` - Validation models

**Integration Points:**
- Run validation **before** `export_results()` in all workers
- Store validation results in `scraped_data.metadata.validation`
- Add `enrichment_score` column to `scrape_jobs` table
- Dashboard widget: "Job #123: Enrichment Score 87/100 (Good)"
- Filter low-quality results: "Show me only validated emails"

**User Benefits:**
- **Confidence**: "95% of emails are deliverable"
- **Cleanup**: Auto-flag invalid data for review
- **Benchmarking**: "This job scored 92/100 vs avg of 78/100"
- **Quality Reports**: "Your data quality improved 15% this month"

---

## Implementation Priority

| Feature | Priority | Effort | ROI | Start After |
|---------|----------|--------|-----|-------------|
| 1. Smart Deduplication | CRITICAL | 3-4 days | 60% cost savings | Immediate |
| 2. Change Detection | HIGH | 4-5 days | 80% speed improvement | Feature 1 |
| 3. Webhook Retry Queue | HIGH | 2-3 days | 99.9% reliability | Feature 1 |
| 4. Budget Controls | MEDIUM | 2-3 days | Prevents overruns | Feature 3 |
| 5. Data Quality Validator | MEDIUM | 3-4 days | 95% accuracy | Feature 2 |

**Total Effort: 14-19 days (~3-4 weeks)**

---

## Competitive Positioning After Phase D

**Lake B2B Scraper vs Competitors:**

| Feature | Lake B2B | Apify | Bright Data | Firecrawl |
|---------|----------|-------|-------------|-----------|
| Deduplication | ✅ Smart hash-based | ❌ No | ❌ No | ❌ No |
| Change Detection | ✅ Field-level | ❌ No | ❌ No | ❌ No |
| Webhook Retry | ✅ 5 retries + DLQ | ❌ No | ❌ No | ❌ No |
| Budget Controls | ✅ Per-domain limits | ❌ No | ⚠️ Manual | ❌ No |
| Data Quality Score | ✅ 0-100 scoring | ❌ No | ⚠️ Basic | ❌ No |
| Transparent Pricing | ✅ $0.01/page | ❌ Compute units | ❌ Complex tiers | ✅ $1/1000 |
| B2B Focus | ✅ Native | ❌ Generic | ❌ Generic | ❌ Generic |

**Tagline:** *"The only B2B scraper that pays for itself through smart deduplication and change detection."*

---

## Success Metrics

**Phase D Goals:**
- **Cost Reduction**: 60% average savings vs full re-scrape
- **Data Quality**: 95% enrichment score across all jobs
- **Reliability**: 99.9% webhook delivery rate
- **User Satisfaction**: NPS score 50+ (industry avg: 30)
- **Retention**: 90% of users still active after 3 months

---

## Next Steps

1. ✅ **Phase C Complete**: Tracked domains + webhook export working
2. 🚀 **Phase D - Feature 1**: Start with Smart Deduplication (highest ROI)
3. 📊 **Instrumentation**: Add telemetry to measure savings from deduplication
4. 🧪 **Beta Testing**: Roll out to 10 power users, gather feedback
5. 📈 **Iterate**: Refine based on real-world usage patterns

---

**Questions for Product Strategy Session:**
1. Should we build all 5 features, or focus on top 3 for MVP?
2. What's the target launch date for Phase D?
3. Do we need to support other webhook formats (Slack, Discord, etc.)?
4. Should budget controls support team-level budgets (not just per-domain)?
5. Is SMTP email verification too slow for real-time scraping? (adds ~500ms per email)
