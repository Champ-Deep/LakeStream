# Debugging LakeStream Scraping Failures with Playwright CLI

## Overview

playwright-cli is an **optional debugging tool** for investigating scraping failures. It is **NOT part of the production scraping pipeline**.

Use this tool when:
- Fetch returns empty content despite HTTP 200
- Escalation exhausted (all tiers fail)
- Need to visually inspect what the browser sees
- Want to validate CSS selectors on target pages

## Installation

**Requires Node.js 18+:**

```bash
npm install -g @playwright/cli@latest
playwright-cli install --skills
```

**Verify installation:**

```bash
playwright-cli --version
```

## Common Debugging Scenarios

### Scenario 1: Fetch Returns Empty Content

**Problem:** ArticleParser extracts zero content despite HTTP 200 response.

**Debug steps:**

1. **Take screenshot** to see actual page rendering
2. **Inspect console** for JavaScript errors
3. **Check network requests** for blocked resources
4. **Validate CSS selectors** match page structure

**Commands:**

```bash
# Quick debug script (recommended)
./.firecrawl/scratchpad/debug-fetch.sh https://example.com/article

# Or manual commands:
playwright-cli screenshot https://example.com/article
playwright-cli console https://example.com/article
playwright-cli trace https://example.com/article --output=trace.zip
```

**Expected output:**
- `page.png` - Visual screenshot
- `page.html` - Raw HTML
- `console.log` - JavaScript errors
- `trace.zip` - Detailed trace file

### Scenario 2: Escalation Exhausted (All Tiers Fail)

**Problem:** Page shows as "blocked" even with headless + proxy (Tier 3).

**Debug steps:**

1. **Manual navigation** to identify blocking mechanism
2. **Take snapshots** at each interaction point
3. **Inspect network waterfall** for CAPTCHA indicators
4. **Check if page requires interaction** (clicks, scrolls)

**Commands:**

```bash
# Open page interactively
./.firecrawl/scratchpad/inspect-page.sh https://example.com/article

# Record full interaction trace
playwright-cli open https://example.com/article --headed
# (manually interact, then close)
```

### Scenario 3: CSS Selector Validation

**Problem:** HTML parser can't extract content - selectors don't match.

**Debug steps:**

1. **Test selectors** on actual page
2. **Inspect element structure** in browser
3. **Try alternative selectors**

**Commands:**

```bash
# Test if selector matches
./.firecrawl/scratchpad/test-selectors.sh https://example.com/article ".article-content"

# Try multiple selectors
./.firecrawl/scratchpad/test-selectors.sh https://example.com/article "article"
./.firecrawl/scratchpad/test-selectors.sh https://example.com/article ".entry-content"
./.firecrawl/scratchpad/test-selectors.sh https://example.com/article "#left_content"
```

### Scenario 4: Content Behind Interaction

**Problem:** Content only loads after clicking button or scrolling.

**Debug steps:**

1. **Open page interactively**
2. **Perform required actions** (click, scroll)
3. **Capture content after interaction**
4. **Document steps for automation**

**Commands:**

```bash
# Open in headed mode
playwright-cli open https://example.com/products --headed

# Then manually:
# - Click pagination buttons
# - Scroll to trigger lazy loading
# - Fill forms to reveal content
# - Take notes on required actions
```

## Debug Script Usage

### debug-fetch.sh

Reproduce a failed fetch with comprehensive debugging artifacts.

**Usage:**
```bash
./.firecrawl/scratchpad/debug-fetch.sh <url>
```

**Example:**
```bash
./.firecrawl/scratchpad/debug-fetch.sh https://www.fonada.com/blog/what-is-whatsapp-pay/
```

**Output:**
```
.firecrawl/debug-1234567890/
├── page.png          # Screenshot
├── page.html         # Raw HTML
├── console.log       # Console output
└── trace.zip         # Playwright trace
```

### test-selectors.sh

Test CSS selectors on a page.

**Usage:**
```bash
./.firecrawl/scratchpad/test-selectors.sh <url> <selector>
```

**Example:**
```bash
./.firecrawl/scratchpad/test-selectors.sh https://www.fonada.com/blog/article "#left_content"
# Output: "WhatsApp Pay is a payment feature..."

./.firecrawl/scratchpad/test-selectors.sh https://www.fonada.com/blog/article ".not-found"
# Output: "SELECTOR NOT FOUND"
```

### inspect-page.sh

Open page in interactive browser for manual inspection.

**Usage:**
```bash
./.firecrawl/scratchpad/inspect-page.sh <url>
```

**Example:**
```bash
./.firecrawl/scratchpad/inspect-page.sh https://www.fonada.com/blog
# Opens browser window - inspect DOM, network, console manually
```

## Interpreting Results

### Screenshot Analysis

**If screenshot shows:**
- ✅ Full article content → CSS selectors are wrong
- ❌ Loading spinner → Page needs `wait_for` timeout
- ❌ "Access Denied" → Blocking mechanism detected
- ❌ Blank page → JavaScript error (check console.log)
- ❌ CAPTCHA → Anti-bot protection active

### Console Log Analysis

**Common JavaScript errors:**

```
TypeError: Cannot read property 'textContent' of null
→ Element not found, selector issue

NetworkError when attempting to fetch resource
→ Blocked external request (analytics, fonts)

ReferenceError: jQuery is not defined
→ JavaScript framework loading issue
```

### Trace Analysis

Open trace.zip in Playwright Trace Viewer:

```bash
# View trace in browser
npx playwright show-trace trace.zip
```

**What to look for:**
- Network timeline (blocked requests)
- Console messages (errors, warnings)
- Page snapshots at each step
- Selector queries (which ones failed)

## Limitations

**playwright-cli is NOT:**
- ❌ Part of production scraping (local debugging only)
- ❌ Integrated with Scrapling (separate tool)
- ❌ Automated (requires manual investigation)
- ❌ In Docker images (install locally)

**For production scraping issues:**
- Use worker logs (`docker compose logs worker -f`)
- Check escalation metrics in `domain_metadata` table
- Monitor cost per domain
- Review blocked page patterns

## When to Escalate to Code Changes

**If debugging reveals:**

1. **CSS selectors consistently fail** → Update `src/scraping/parser/html_parser.py`
2. **Page requires interaction** → Consider adding interactive tier
3. **Session persistence needed** → Revisit Tier 2.5 (playwright-cli integration)
4. **Blocking pattern identified** → Update blocking detection logic

## Further Resources

- [Playwright CLI Documentation](https://github.com/microsoft/playwright-cli)
- [Playwright Trace Viewer](https://trace.playwright.dev/)
- [LakeStream Architecture](../CLAUDE.md)
- [Escalation Logic](../src/services/escalation.py)
