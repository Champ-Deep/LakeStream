# LakeStream vs Firecrawl: Output Comparison

## Overview

Both LakeStream and Firecrawl are web scraping platforms, but they serve different purposes:

- **Firecrawl**: Single-page scraper focused on markdown conversion and clean content extraction
- **LakeStream**: Multi-page B2B data extraction platform focused on structured data across entire domains

---

## JSON Output Structure

### Firecrawl Output (Typical)

```json
{
  "success": true,
  "data": {
    "markdown": "# Page Title\n\nClean markdown content here...",
    "html": "<html>...</html>",
    "metadata": {
      "title": "Page Title",
      "description": "Meta description",
      "language": "en",
      "sourceURL": "https://example.com/page",
      "statusCode": 200
    },
    "llm_extraction": {
      // Optional LLM-extracted fields if requested
    }
  }
}
```

**Firecrawl Focus:**
- Clean markdown conversion
- Single page at a time
- LLM-powered extraction (optional)
- Minimal metadata
- No domain-wide mapping

---

### LakeStream Output (Your Export)

```json
{
  "job_id": "35a93ddf-c541-42de-a43a-0de7f8cec347",
  "domain": "blog.hubspot.com",
  "exported_at": "2026-03-12T17:05:00Z",
  "total_records": 148,
  "data": [
    {
      "id": "12820439-4f08-4ab7-90af-a1c049c0be4a",
      "domain": "blog.hubspot.com",
      "data_type": "article",
      "url": "https://blog.hubspot.com/blog/...",
      "title": "Why List Segmentation Matters in Email Marketing",
      "published_date": null,
      "scraped_at": "2026-03-12T17:01:45.894500+00:00",
      "metadata": {
        "author": "Pamela Vaughan",
        "content": "Full article text (1,673 words)...",
        "excerpt": "Learn why marketers must segment...",
        "word_count": 1673,
        "categories": ["Marketing", "Email"]
      }
    },
    // ... 147 more items
  ]
}
```

**LakeStream Focus:**
- Domain-wide data extraction
- Multiple data types (blog_url, article, contact, tech_stack, resource, pricing)
- Structured metadata per type
- Batch processing (100-500 pages per job)
- Full content + structured data

---

## Key Differences

| Feature | Firecrawl | LakeStream |
|---------|-----------|------------|
| **Scope** | Single page | Full domain (100-500 pages) |
| **Output Format** | Markdown + HTML | JSON with structured metadata |
| **Content** | Clean markdown | Full HTML content + extracted fields |
| **Metadata** | Basic (title, description) | Rich (author, word count, categories, etc.) |
| **Data Types** | One type (page) | 6 types (blog, article, contact, tech, resource, pricing) |
| **URL Discovery** | Manual input | Automatic sitemap crawling |
| **Rate Limiting** | Per-page pricing | Domain-level adaptive rate limiting |
| **Escalation** | Single tier | 3-tier adaptive (HTTP → Playwright → Proxy) |
| **Batch Export** | One page at a time | CSV + JSON bulk export |

---

## What LakeStream Captures That Firecrawl Doesn't

### 1. **Domain Mapping**
- Discovers all URLs via sitemap
- Classifies URLs by type (blog, article, contact, resource)
- Maps entire site structure

### 2. **Structured Data Extraction**
- **Articles**: Author, excerpt, word count, categories, full content
- **Contacts**: First name, last name, title, email, phone, LinkedIn
- **Tech Stack**: Frameworks, libraries, analytics tools
- **Resources**: Whitepapers, case studies, ebooks with download URLs
- **Pricing**: Plans, prices, billing cycles, features

### 3. **Multi-Page Processing**
- Processes 100-500 pages in one job
- Bulk export (CSV + JSON)
- Relationship tracking (blog → articles)

### 4. **Cost Optimization**
- Starts with cheap HTTP requests ($0.0001)
- Escalates to Playwright only when needed ($0.003)
- Uses proxy only for anti-bot sites ($0.0035)

---

## What Firecrawl Does Better

### 1. **Clean Markdown**
Firecrawl excels at converting messy HTML to clean markdown:

```markdown
# Why List Segmentation Matters in Email Marketing

By now, most marketers understand the importance of email...
```

LakeStream provides raw HTML content - you'd need to post-process to get markdown.

### 2. **LLM Extraction**
Firecrawl can use LLMs to extract custom schemas:

```json
{
  "llm_extraction": {
    "company_name": "HubSpot",
    "key_takeaways": ["Segmentation improves CTR", ...],
    "sentiment": "positive"
  }
}
```

LakeStream uses CSS selectors and pattern matching (faster, cheaper, but less flexible).

### 3. **Screenshot Capture**
Firecrawl can capture page screenshots - LakeStream doesn't (yet).

### 4. **Simpler API**
Firecrawl: One endpoint, instant results:
```bash
curl -X POST https://api.firecrawl.dev/v0/scrape \
  -H "Authorization: Bearer $API_KEY" \
  -d '{"url": "https://example.com"}'
```

LakeStream: Job-based architecture (submit job → poll status → export results)

---

## Use Cases

### Use Firecrawl When:
- Scraping 1-10 pages
- Need clean markdown for LLM consumption
- Want screenshots
- Need custom LLM extraction schemas
- Don't care about domain-wide data

### Use LakeStream When:
- Scraping 100-500 pages from B2B sites
- Need structured contact/company data
- Want bulk CSV/JSON exports
- Need cost-optimized multi-tier scraping
- Require domain mapping and URL discovery
- Building lead gen pipelines

---

## Example: Same Article in Both Systems

### Firecrawl Output (Simplified)
```json
{
  "markdown": "# Why List Segmentation Matters\n\nBy Pamela Vaughan\n\n...",
  "metadata": {
    "title": "Why List Segmentation Matters",
    "sourceURL": "https://blog.hubspot.com/..."
  }
}
```

### LakeStream Output (Simplified)
```json
{
  "data_type": "article",
  "title": "Why List Segmentation Matters in Email Marketing",
  "url": "https://blog.hubspot.com/...",
  "metadata": {
    "author": "Pamela Vaughan",
    "content": "By now, most marketers understand...",
    "excerpt": "Learn why marketers must segment their lists...",
    "word_count": 1673,
    "categories": ["Marketing", "Email"]
  }
}
```

**Firecrawl**: Clean, simple, markdown-ready
**LakeStream**: Structured, metadata-rich, bulk-exportable

---

## Performance Comparison

**Scraping blog.hubspot.com (148 pages):**

| Metric | Firecrawl | LakeStream |
|--------|-----------|------------|
| **Method** | 148 individual API calls | 1 job (batch) |
| **Time** | ~7-15 minutes (sequential) | 3m 34s (parallel) |
| **Cost** | ~$1.48 ($0.01/page × 148) | ~$0.03 (mostly basic_http) |
| **API Calls** | 148 | 1 (submit job) + 1 (export) |
| **Export** | Manual aggregation needed | One-click CSV/JSON |

---

## Hybrid Approach (Best of Both Worlds)

You could combine both:

1. **LakeStream**: Discover all URLs + extract structured data
2. **Firecrawl**: Convert article content to clean markdown

```python
# 1. Get URLs from LakeStream
lakestream_data = requests.get("http://localhost:8000/api/export/json/job_id").json()

# 2. Process articles with Firecrawl for clean markdown
for item in lakestream_data['data']:
    if item['data_type'] == 'article':
        markdown = firecrawl.scrape(item['url'], format='markdown')
        item['markdown'] = markdown

# 3. Best of both: structured data + clean markdown
```

---

## Conclusion

**Firecrawl** = Single-page scraping tool with premium features (markdown, LLM, screenshots)
**LakeStream** = Multi-page B2B data extraction platform optimized for domain-wide scraping

**Choose based on your use case:**
- Small-scale, clean extraction → Firecrawl
- Large-scale B2B lead gen → LakeStream
- Both → Hybrid approach

---

## Your Next Steps

1. **Add "Export JSON" button** ✅ (Just added!)
2. **Compare outputs** ✅ (This document)
3. **Decide what to improve:**
   - Add markdown conversion? (like Firecrawl)
   - Add screenshot capture?
   - Add LLM extraction schemas?
   - Add content cleaning/normalization?

**Your LakeStream output is production-ready for B2B data extraction!** 🎉
