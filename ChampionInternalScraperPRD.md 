# Overview

The Lake B2B Internal Scraping System is an intelligent, template-based web scraping platform designed to enrich B2B data at scale. The system will collect blog URLs, articles, resource pages, pricing information, and contact endpoints from tens of thousands of domains monthly, feeding directly into Lake B2B's enrichment pipelines.

## Core Problem

Lake B2B needs to enrich client data at scale by collecting public web information from tens of thousands of B2B domains monthly. Current manual approaches are slow, expensive, and don't scale. Most scraping tools (like Appify) are either too rigid for our use cases or prohibitively expensive at scale.

## Solution

Build an internal, flexible scraping platform that:

- Uses template-based architecture for rapid deployment across similar site types
- Implements intelligent fallback strategies (basic HTTP → headless browser → residential proxy)
- Provides cost optimization through adaptive scraping strategies
- Integrates seamlessly with existing n8n workflows and Supabase data stores
- Enables ops teams to configure scrapers without engineering intervention

# Key Features & Requirements

## 1. Core Data Outputs

The system must extract the following data points from target domains:

### Content Discovery

- **Blog URLs**: Main blog landing page URLs
- **Article Metadata**: Titles, publish dates, author names, URLs
- **Content Categories**: Tags, categories, topic classifications

### Resource Intelligence

- **Whitepapers & Case Studies**: Download URLs and titles
- **Webinar Pages**: Registration URLs and event details
- **Pricing Information**: Tier names, feature lists, pricing signals

### Contact & People Signals

- **Leadership Information**: Names and titles from about/team pages
- **Contact Endpoints**: Support emails, demo request forms
- **Careers Page URLs**: Job listings and hiring signals

### Tech Stack Signals

- **JavaScript Libraries**: Analytics tools, CRM integrations
- **Platform Detection**: WordPress, HubSpot, Webflow, etc.

## 2. Template-Based Architecture

### Initial Template Library (Launch)

1. **WordPress Blog Scraper**
2. **HubSpot Resource Center Scraper**
3. **Webflow Marketing Site Scraper**
4. **Generic Article List Scraper** (fallback)
5. **Simple Directory Scraper**

### Template Configuration

Each template defines:

- **Selectors**: CSS/XPath selectors for data extraction
- **Pagination Strategy**: URL-based, click-based, or infinite scroll
- **Fallback Logic**: When to escalate from basic to headless
- **Data Schema**: Expected output structure

## 3. Adaptive Scraping Strategy

### Three-Tier Approach

**Tier 1: Basic HTTP Scraper**

- Cost: ~$0.0001 per request
- Speed: 200-500ms per page
- Success Rate: 60-70% of B2B sites
- Use Case: Server-rendered HTML sites

**Tier 2: Headless Browser**

- Cost: ~$0.001-0.003 per request
- Speed: 2-5 seconds per page
- Success Rate: 90% of sites
- Use Case: JavaScript-heavy SPAs

**Tier 3: Headless + Residential Proxy**

- Cost: ~$0.003-0.005 per request
- Speed: 3-5 seconds per page
- Success Rate: 95%+ of sites
- Use Case: Sites with aggressive bot detection

### Automatic Escalation Logic

```
1. Start with Basic HTTP scraper
2. If empty results OR 403/429 error → Escalate to Headless
3. If Headless fails OR CAPTCHA detected → Escalate to Residential Proxy
4. If all tiers fail → Mark for manual review

```

# Technical Architecture

## Tech Stack Recommendation: Node.js + Puppeteer

### Why Node.js/Puppeteer?

1. **Performance**: Event loop handles IO-bound scraping excellently
2. **n8n Compatibility**: Node.js is n8n's native runtime
3. **Type Safety**: TypeScript reduces runtime errors
4. **Ecosystem**: Vast scraping library ecosystem
5. **Lower Memory**: More efficient than Python for equivalent workloads

### System Components

```
┌─────────────────────────────────────────────────────────────┐
│                     API Layer (Fastify/Express)              │
│              POST /scrape/execute, GET /scrape/status       │
└───────────────────────────────┬─────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────┐
│                   Job Queue (BullMQ + Redis)                 │
│                   Manages scrape job queuing                 │
└───────────────────────────────┬─────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────┐
│                      Worker Pool                              │
│    ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│    │ Basic HTTP    │  │   Headless   │  │  Headless +  │   │
│    │   Workers     │  │   Workers    │  │  Proxy       │   │
│    └──────────────┘  └──────────────┘  └──────────────┘   │
└───────────────────────────────┬─────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────┐
│            Proxy Service (Bright Data / Smartproxy)          │
│                  Residential + Datacenter IPs                │
└─────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────┐
│                    Data Storage (Supabase)                    │
│      scrape_jobs | scraped_data | domain_metadata           │
└───────────────────────────────┬─────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────┐
│            Enrichment Pipeline (n8n Workflows)               │
│       Triggered on new scraped_data inserts                  │
└─────────────────────────────────────────────────────────────┘

```

## Database Schema (Supabase)

### scrape_jobs

```sql
CREATE TABLE scrape_jobs (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  domain TEXT NOT NULL,
  template_id TEXT NOT NULL,
  status TEXT CHECK (status IN ('pending', 'running', 'completed', 'failed')),
  strategy_used TEXT,
  error_message TEXT,
  cost_usd DECIMAL(10, 6),
  duration_ms INTEGER,
  pages_scraped INTEGER,
  created_at TIMESTAMP DEFAULT NOW(),
  completed_at TIMESTAMP
);

```

### scraped_data

```sql
CREATE TABLE scraped_data (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  job_id UUID REFERENCES scrape_jobs(id),
  domain TEXT NOT NULL,
  data_type TEXT, -- 'blog_url', 'article', 'resource', 'contact'
  url TEXT,
  title TEXT,
  published_date DATE,
  metadata JSONB,
  scraped_at TIMESTAMP DEFAULT NOW()
);

```

### domain_metadata

```sql
CREATE TABLE domain_metadata (
  domain TEXT PRIMARY KEY,
  last_successful_strategy TEXT,
  block_count INTEGER DEFAULT 0,
  last_scraped_at TIMESTAMP,
  success_rate DECIMAL(5, 2),
  avg_cost_usd DECIMAL(10, 6),
  notes TEXT
);

```

# Cost Model & Projections

## Target Economics (50K domains/month)

### Blended Average Cost Per Domain: **$0.01**

**Breakdown**:

- 70% succeed with Basic HTTP ($0.0001 × 35K = $3.50)
- 20% require Headless Browser ($0.002 × 10K = $20)
- 10% require Residential Proxy ($0.004 × 5K = $20)
- **Total: ~$43.50 or $0.00087/domain**

With overhead (infrastructure, monitoring):

- **Compute (Railway)**: $150/month
- **Proxies**: $200/month
- **Storage**: $10/month
- **Monitoring**: $40/month
- **Total**: $400-500/month for 50K domains

## Scaling Economics

| Monthly Volume | Cost/Domain | Total Cost |
| --- | --- | --- |
| 10K domains | $0.015 | $150 |
| 50K domains | $0.01 | $500 |
| 100K domains | $0.008 | $800 |
| 200K domains | $0.007 | $1,400 |

# Success Metrics

## 6-Month Goals

### Volume & Reliability

- ✅ **50,000 domains/month** enriched automatically
- ✅ **90% success rate** across monthly workload
- ✅ **< 5% manual intervention** required
- ✅ **85%+ success rate** on first attempt

### Cost Efficiency

- ✅ **$0.008/domain** or less at 50K scale
- ✅ **< $0.02/domain** for complex sites

### Template Coverage

- ✅ **10-15 production templates** covering 80% of B2B sites
- ✅ **WordPress, HubSpot, Webflow, Squarespace** fully supported

### Self-Service

- ✅ **Ops teams can create** new template variants in < 2 hours
- ✅ **Zero engineering intervention** for routine scraper configs

### Integration

- ✅ **Seamless n8n integration** with auto-triggered enrichment workflows
- ✅ **Real-time data availability** in Supabase for client deliverables

# Implementation Roadmap

## Phase 1: MVP (Month 0-1)

**Goal**: Validate core scraping capabilities

### Deliverables

- ✅ Node.js API with Fastify
- ✅ BullMQ job queue + Redis
- ✅ 2 basic scraper workers
- ✅ 1-2 templates (WordPress blog, Generic article list)
- ✅ Datacenter proxy integration (Bright Data)
- ✅ Supabase tables created
- ✅ Manual API triggers (no scheduling yet)

### Success Criteria

- 100 test domains scraped successfully
- API deployed and accessible internally
- Data quality validated

## Phase 2: Template Expansion (Month 2-3)

**Goal**: Cover major B2B site types

### Deliverables

- ✅ 5-7 scraper templates (add HubSpot, Webflow, Squarespace, directory)
- ✅ Scheduled scraping (cron-based)
- ✅ 2 headless browser workers for JS-heavy sites
- ✅ Basic Grafana monitoring dashboard
- ✅ Sentry error tracking integration
- ✅ Residential proxy integration (opt-in)

### Success Criteria

- 5,000 real client domains scraped monthly
- 80% success rate
- Adaptive fallback logic working

## Phase 3: Scaling & Reliability (Month 4-5)

**Goal**: Handle 20K+ domains monthly

### Deliverables

- ✅ 10 production templates
- ✅ Auto-scaling workers (queue depth triggers)
- ✅ Proxy health monitoring service
- ✅ Stealth plugins (Puppeteer anti-fingerprinting)
- ✅ Optional CAPTCHA solving (2Captcha)
- ✅ Per-domain rate limiting
- ✅ Cost tracking dashboard

### Success Criteria

- 20,000 domains/month
- 85% success rate
- Cost per domain < $0.01
- Integration with 3-5 client enrichment workflows

## Phase 4: Self-Service & Optimization (Month 6)

**Goal**: Enable ops teams, optimize costs

### Deliverables

- ✅ Template editor UI or JSON config tool
- ✅ Internal documentation & playbooks
- ✅ Performance optimization (p95 latency < 5s)
- ✅ Per-client cost reporting
- ✅ Anomaly detection & alerts
- ✅ Template testing framework

### Success Criteria

- 50,000 domains/month
- 90% success rate
- Ops team creates 5+ templates independently
- Cost per domain $0.008
- Production-ready for client-facing work

# Risks & Mitigation

## Technical Risks

### Risk 1: Sites blocking our scrapers

**Mitigation**:

- Adaptive proxy rotation (residential IPs)
- Respectful rate limiting (1-3 sec delays)
- Stealth browser fingerprinting
- Accept 5-10% of sites will be unscrape-able

### Risk 2: Cost overruns

**Mitigation**:

- Real-time cost tracking per domain
- Budget alerts and automatic throttling
- Prefer cheaper strategies, escalate only when needed
- Cache unchanged pages

### Risk 3: Data quality issues

**Mitigation**:

- Template testing framework with sample domains
- Human QA spot-checks on 5% of scraped data
- Client feedback loop for continuous improvement

## Operational Risks

### Risk 4: Ops teams can't self-serve

**Mitigation**:

- Comprehensive documentation and training
- Template gallery with copy-paste examples
- Engineering support for first 5 template creations

### Risk 5: Scaling bottlenecks

**Mitigation**:

- Horizontal scaling from day one (stateless workers)
- Load testing at 2x target volume before launch
- Auto-scaling triggers based on queue depth

# Competitive Advantages vs Appify

| Feature | Appify | Lake B2B Platform |
| --- | --- | --- |
| **Cost at 50K domains/month** | ~$2,500+ | $400-500 |
| **Adaptive fallback** | Manual config | Automatic |
| **API-first** | Limited | Full REST API |
| **Template customization** | UI-only, limited | Code + UI config |
| **n8n integration** | None | Native |
| **Cost transparency** | Opaque | Real-time dashboards |
| **Residential proxies** | Enterprise-only | Available on-demand |
| **White-label** | No | Internal platform |
| **CAPTCHA solving** | Limited | Opt-in per domain |
| **Self-hosted** | No | Yes (Railway/K8s) |

# Next Steps

## Immediate (Next 2 Weeks)

### For Product/Ops Lead

1. **Prioritize 3-5 initial use cases** for scraper (e.g., blog URLs for 10K SaaS companies)
2. **Approve $500/month trial budget** for proxies, compute, tooling
3. **Define MVP success criteria** with team
4. **Schedule kickoff meeting** with engineering and ops

### For Engineering

1. **Set up dev environment** (Railway, Supabase, GitHub)
2. **Build MVP API** (Fastify, BullMQ integration)
3. **Implement first worker** (basic scraper with Puppeteer)
4. **Integrate datacenter proxy** (Bright Data trial)
5. **Test on 10 domains**, validate end-to-end flow

### For Researcher

1. **Validate proxy provider choice** (trial Bright Data, Smartproxy, Oxylabs)
2. **Prototype 2 scraper templates** (WordPress blog, generic article list)
3. **Test on 50 real B2B domains**, measure success rate
4. **Document findings** and share with team
5. **Draft competitive analysis** comparing to Appify

---

**Document Owner**: Product Team
**Last Updated**: February 4, 2026
**Status**: Execution-Ready