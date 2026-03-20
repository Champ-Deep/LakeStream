# LakeStream — Standard Operating Procedures

Internal operations guide for the Lake B2B GTM team.

---

## 1. Getting Started

### 1.1 Logging In

1. Navigate to the LakeStream URL provided by your administrator.
2. Enter your email and password on the login page.
3. If you don't have an account, ask your organization owner to invite you from **Settings > Team**.

> Self-registration is disabled. All new accounts are created by org owners or admins.

### 1.2 Dashboard Overview

After login you land on the **Dashboard**, which shows:

| Section | What It Shows |
|---------|---------------|
| **Stats Row** | Total Jobs, Running Now, Success Rate, Domains Scraped |
| **Quick Start** | Domain input + data type selection — the fastest way to start a scrape |
| **Recent Jobs** | Last 5 jobs with live status (auto-refreshes every 10s) |
| **Top Domains** | Most-scraped domains with success rates |
| **Tracked Sites** | Health summary of domains under recurring monitoring |

### 1.3 First-Time Checklist

- [ ] Run a test scrape on a known domain (e.g., `hubspot.com`)
- [ ] Review the results to confirm data quality
- [ ] Visit the **Help** page for the interactive tour
- [ ] Configure proxy settings if scraping protected sites (Settings > Proxy)
- [ ] Set up a webhook if integrating with n8n or another pipeline (Settings > Webhook)

---

## 2. Running a Scrape

### 2.1 Basic Scrape

1. On the **Dashboard**, enter a domain in the Quick Start field (e.g., `hubspot.com`).
2. The options panel opens showing data type checkboxes.
3. Default selections: Blog URLs, Articles, Contacts, Tech Stack, Resources.
4. Click **Scrape** to start.
5. You are redirected to the job status page automatically.

### 2.2 Data Types

| Type | What It Extracts | Use Case |
|------|-----------------|----------|
| **Blog URLs** | Blog landing pages + article links | Content inventory |
| **Articles** | Full article text, author, date, categories, word count | Content intelligence |
| **Contacts** | Names, titles, emails, phones from team/about pages | Lead generation |
| **Tech Stack** | CMS platform, analytics, marketing tools, frameworks | Technographic data |
| **Resources** | Whitepapers, case studies, ebooks, webinar recordings | Content marketing analysis |
| **Pricing** | Plan names, prices, features from pricing pages | Competitive pricing |

### 2.3 Advanced Options

Expand **Advanced Options** on the Quick Start form to access:

| Option | Description | Default |
|--------|-------------|---------|
| **Max Pages** | Limit how many pages to crawl (10–500) | 100 |
| **Template** | Force a platform template or use auto-detect | Auto-detect |
| **Scraping Tier** | Choose Playwright, Playwright + Proxy, or Auto | Auto (adaptive) |
| **Priority** | Queue priority: Low (1), Normal (5), High (10) | Normal |
| **Raw Only** | Save raw page content without specialized extraction | Off |

### 2.4 Scraping Tiers

| Tier | Cost | When to Use |
|------|------|-------------|
| **Playwright** | ~$0.003/page | Default. Headless browser with session persistence. Handles JavaScript-heavy sites. |
| **Playwright + Proxy** | ~$0.0035/page | For sites that block direct requests (403/429). Adds residential proxy rotation. |
| **Auto (Adaptive)** | Varies | Starts with Playwright, automatically escalates to Playwright + Proxy if blocked. Recommended. |

---

## 3. Monitoring Progress

### 3.1 Job Status Page

After starting a scrape, you're taken to the job status page which shows:

- **Progress Bar** — Indeterminate while running, full green on completion, full red on failure.
- **Phase Indicator**:
  - "Discovering URLs..." — the crawler is finding pages on the site via sitemaps and link following.
  - "Extracting content..." — the content worker is processing discovered URLs.
- **Live Stats** — Pages scraped, Items found, Duration (updates every 2 seconds).
- **Data Breakdown** — After completion, colored chips showing counts per data type (e.g., "Articles 12", "Contacts 5").
- **Strategy Used** — Whether Playwright or Playwright + Proxy was used.

### 3.2 Jobs List

Navigate to **Jobs** in the sidebar to see all your scrape jobs. The table auto-refreshes every 5 seconds when jobs are running. Use the status filter tabs (All, Running, Completed, Failed) to narrow the view.

### 3.3 Interpreting Results

| Status | Meaning | Action |
|--------|---------|--------|
| **Completed** (no warning) | All pages scraped successfully | Review results |
| **Completed** (with warning) | Some pages were blocked or returned errors | Check warning panel for details; data from successful pages is still available |
| **Failed** — "No data extracted" | Site blocked all requests or is empty | Re-run with Playwright + Proxy tier |
| **Failed** — "Job timed out" | Job exceeded the 30-minute limit | Reduce Max Pages |
| **Failed** — with error details | Technical error during scraping | Note the job ID and report to engineering |

---

## 4. Viewing & Exporting Results

### 4.1 Results Page

1. Navigate to **Results** in the sidebar.
2. Use the **Domain** dropdown to filter by domain.
3. Use the **Data Type** dropdown to filter by type (articles, contacts, etc.).
4. Click any row to expand and see full metadata.
5. Use "Load more" at the bottom for pagination.

### 4.2 Export Options

| Format | How to Access | Best For |
|--------|--------------|----------|
| **CSV** | Job status page → "Download CSV" | Spreadsheet import, quick analysis |
| **JSON** | Job status page → "Export JSON" | Programmatic use, full metadata (og:tags, social handles) |
| **Webhook** | Automatic on job completion (if configured) | Real-time pipeline integration (n8n) |
| **Bulk CSV** | Results page → "Export All" | Cross-domain data dump |

### 4.3 CSV vs JSON

- **CSV** flattens metadata into ~30 columns. Good for Excel/Google Sheets.
- **JSON** preserves the full nested structure including OpenGraph tags, social metadata, and rich content. Good for programmatic pipelines.

---

## 5. Troubleshooting

### 5.1 Common Errors

| Error Message | Cause | Solution |
|---------------|-------|----------|
| "No data extracted from X" | Site is empty or fully blocked | Re-run with Playwright + Proxy tier |
| "No data extracted from X. Try escalating to Playwright + Proxy." | Default tier was blocked | Select Playwright + Proxy in Advanced Options |
| "Job timed out (stale recovery)" | Job ran longer than 30 minutes | Reduce Max Pages to 50 or lower |
| "No data extracted. Errors: content_worker: ..." | Technical error | Note the job ID and report to engineering |
| Warning: "(3 blocked, 2 skipped)" | Some pages returned 403/404 | Normal for most sites; check if critical pages were missed |

### 5.2 Site Won't Scrape

Follow this escalation path:

1. **First try**: Run with default settings (Auto tier, 100 pages).
2. **Second try**: Force **Playwright + Proxy** tier in Advanced Options.
3. **Third try**: Reduce **Max Pages** to 20 to test if any data comes through.
4. **Check**: Does the site require login? Has a CAPTCHA? Uses aggressive bot detection?
5. **Report**: If still failing, share the domain name and job ID with engineering.

### 5.3 Missing Data

| Missing Type | Likely Cause |
|-------------|-------------|
| Contacts empty | Site has no public team/about page, or contact info is behind a login |
| Articles empty | Site has no blog, or content is behind a paywall |
| Tech Stack empty | Detection only runs on the homepage; ensure the homepage is accessible |
| Pricing empty | Site has no public pricing page |
| Resources empty | Site doesn't publish downloadable content (whitepapers, case studies) |

---

## 6. Settings

### 6.1 Proxy Configuration

1. Go to **Settings** > Proxy Configuration.
2. Enter a proxy URL (supports HTTP, HTTPS, SOCKS5).
3. Toggle the proxy active/inactive.
4. When active, all Playwright + Proxy tier requests use this proxy.

### 6.2 Webhook Setup

1. Go to **Settings** > Webhook Configuration.
2. Enter your webhook URL (e.g., an n8n workflow trigger).
3. Options:
   - **Auto-send on completion** — automatically POST results when a tracked domain scrape completes.
   - **Include metadata** — include OpenGraph tags, social handles, and other rich metadata in the payload.
4. Use the **Test** button to verify your webhook is reachable.

### 6.3 Team Management (Org Owners Only)

1. Go to **Settings** > Team Members.
2. Click **Invite Member** and fill in name, email, and password.
3. The invited user can log in immediately.
4. Each team member sees only their own jobs and results. Admins see all data.

---

## 7. Glossary

| Term | Definition |
|------|-----------|
| **Domain** | A website being scraped (e.g., `hubspot.com`) |
| **Job** | A single scrape run for one domain |
| **Template** | Platform-specific scraping config (WordPress, HubSpot, Webflow, Generic, Directory) |
| **Tier / Strategy** | The scraping method: Playwright or Playwright + Proxy |
| **Escalation** | Automatic upgrade to a higher tier when the current one is blocked |
| **Tracked Domain** | A domain set up for recurring automatic scraping |
| **Data Type** | Category of extracted data: article, contact, blog_url, tech_stack, resource, pricing |
| **Raw Only** | Mode that saves page HTML without running specialized extractors |
| **Webhook** | An HTTP endpoint that receives scraped data as a POST request |
| **n8n** | Workflow automation tool used for enrichment pipelines |
