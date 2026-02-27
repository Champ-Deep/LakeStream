# 🌊 LakeStream

<p align="center">
  <strong>B2B Web Scraping & Data Extraction Platform</strong><br>
  Built for speed, built for scale, built for your business. 🚀
</p>

<p align="center">
  <a href="https://python.org"><img src="https://img.shields.io/badge/Python-3.12+-blue?logo=python" alt="Python"></a>
  <a href="https://github.com/Champ-Deep/LakeStream/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-green" alt="License"></a>
  <a href="https://github.com/Champ-Deep/LakeStream/actions"><img src="https://img.shields.io/badge/Tests-Passing-brightgreen" alt="Tests"></a>
  <a href="https://discord.gg/lakestream"><img src="https://img.shields.io/badge/Community-Discord-purple" alt="Discord"></a>
</p>

---

## ✨ What is LakeStream?

LakeStream is a **powerful B2B web scraping platform** that extracts valuable data from any website — blogs, articles, pricing pages, contact info, tech stacks, and more! 

Whether you're an **SEO team** monitoring competitors or a **data team** building enrichment pipelines, LakeStream handles the heavy lifting so you can focus on insights. 💡

---

## ⚡ Quick Start

```bash
# 1️⃣ Install dependencies
pip install -r requirements.txt

# 2️⃣ Start infrastructure (PostgreSQL + Redis)
make docker-up

# 3️⃣ Run the API & worker
make dev          # API on http://localhost:3001
make worker       # Background job processor
```

**Boom!** You're ready to scrape. 🎉

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         LakeStream                               │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│   ┌──────────────┐     ┌──────────────┐     ┌──────────────┐  │
│   │   FastAPI   │────▶│  Job Queue   │────▶│   Workers    │  │
│   │     API     │     │    (arq)     │     │   (async)    │  │
│   └──────────────┘     └──────────────┘     └──────────────┘  │
│         │                                           │           │
│         │              ┌──────────────┐            │           │
│         └─────────────▶│  PostgreSQL  │◀───────────┘           │
│                        │   Database   │                       │
│                        └──────────────┘                       │
│                               │                                 │
│                               ▼                                 │
│                        ┌──────────────┐                        │
│                        │     n8n      │                        │
│                        │ Enrichment   │                        │
│                        └──────────────┘                        │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 🔥 Three-Tier Adaptive Scraping

| Tier | What It Does | Best For |
|------|--------------|----------|
| 🌐 **Basic HTTP** | Lightning fast requests | Simple pages, APIs |
| 🕵️ **Headless Browser** | Renders JavaScript | SPAs, dynamic content |
| 🛡️ **Stealth + Proxy** | Bypasses protection | Cloudflare, protected sites |

LakeStream **automatically escalates** between tiers when it detects blocks, CAPTCHAs, or empty responses. No manual intervention needed! 

---

## 🎯 Use Cases

### For SEO Teams 🔍

> "I need to monitor my competitors' content strategy."

```python
# Scrape competitor blog posts
from src.services.scraper import ScraperService

scraper = ScraperService()
result = await scraper.scrape("https://competitor.com/blog")

print(result["title"])        # Blog post title
print(result["markdown"])     # Full content in Markdown
print(result["metadata"])     # Author, date, tags
```

| 🎯 Task | 💼 How LakeStream Helps |
|---------|------------------------|
| **Competitor Blogging** | Monitor posting frequency, topics, engagement |
| **Content Gaps** | Find topics competitors cover that you don't |
| **Pricing Monitoring** | Track competitor pricing pages in real-time |
| **Backlink Analysis** | Discover who's linking to competitors |
| **Site Audits** | Extract all pages for technical SEO analysis |

### For Data Teams 📊

> "I need to build B2B enrichment pipelines."

```python
# Enrich company data at scale
from src.services.scraper import ScraperService
from src.models.scraping import ScrapingTier

# Scrape with specific tier
scraper = ScraperService()
result = await scraper.scrape(
    "https://techcompany.com/about",
    tier=ScrapingTier.HEADLESS_BROWSER
)

# Extract structured data
print(result["markdown"])     # Clean Markdown
print(result["metadata"])     # JSON with title, description, etc.
```

| 🎯 Task | 💼 How LakeStream Helps |
|---------|------------------------|
| **Lead Generation** | Extract contact info, job titles, company data |
| **Tech Stack Detection** | Identify tools/tech used on any site |
| **Market Research** | Scrape industry blogs, news, resources |
| **Data Enrichment** | Fill gaps in existing databases |
| **API Alternative** | Get data when APIs don't exist |

---

## 🔧 Configuration

Create a `.env` file:

```bash
# Database
DATABASE_URL=postgresql://user:pass@localhost:5432/lakedb

# Redis
REDIS_URL=redis://localhost:6379

# 🔐 Proxy (optional - for Tier 3)
BRIGHTDATA_PROXY_URL=
SMARTPROXY_URL=

# 🔍 LakeCurrent (search discovery)
LAKECURRENT_BASE_URL=http://localhost:8001

# 🔑 Authentication
JWT_SECRET=your-secret-key
```

---

## 📚 API Examples

### Scrape a Single URL

```python
import asyncio
from src.services.scraper import ScraperService

async def main():
    scraper = ScraperService()
    result = await scraper.scrape("https://example.com")
    
    print(result["markdown"])  # Clean content
    print(result["metadata"])  # {title, author, date, ...}

asyncio.run(main())
```

### Discover Domains from Search

```python
from src.services.lakecurrent import LakeCurrentClient

client = LakeCurrentClient(
    base_url="http://localhost:8001",
    timeout=15.0
)
results = await client.search("B2B SaaS companies", limit=10)

for r in results.results:
    print(r.url, r.title)
```

### Map All URLs on a Domain

```python
from src.services.crawler import CrawlerService

crawler = CrawlerService()
urls = await crawler.map_domain("https://example.com", limit=100)

print(f"Found {len(urls)} URLs")
```

---

## 🧪 Testing

```bash
# Run all tests
make test

# Run specific test
pytest tests/unit/scraping/test_lake_fetcher.py -v

# Run with coverage
pytest --cov=src tests/
```

---

## 📊 Benchmarking

Compare tier performance:

```bash
python benchmarks/scrapling_benchmark.py https://example.com https://python.org
```

---

## 🔌 API Endpoints

### Web Dashboard
| Endpoint | Description |
|----------|-------------|
| `GET /` | Main dashboard |
| `GET /jobs` | Job list |
| `GET /results` | Browse extracted data |
| `GET /domains` | Domain analytics |

### REST API
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/scrape/execute` | POST | Create scrape job |
| `/api/scrape/status/{job_id}` | GET | Job status |
| `/api/discover/search` | POST | Search-driven discovery |
| `/api/export/csv/{job_id}` | GET | Export as CSV |
| `/api/health` | GET | Health check |

---

## 🤝 Contributing

1. 🍴 Fork the repo
2. 🌿 Create a branch (`git checkout -b feature/amazing`)
3. 💻 Make your changes
4. ✅ Run tests (`make test`)
5. 📝 Commit with clear messages
6. 🚀 Submit a PR

---

## 📄 License

**MIT License** — See [LICENSE](LICENSE) for details.

---

## 🙏 Acknowledgments

- 🐍 Python community — For making this all possible
- 💜 **You** — For choosing LakeStream!

---

<p align="center">
  <sub>Built with ❤️ by <a href="https://lakeb2b.com">Lake B2B</a></sub>
</p>
