# Lake B2B Scraper Project Brain

This document serves as the persistent context and architectural guide for developing a specialized scraping tool targeting Lake B2B data structures.

## Core Objective
Build a robust, scalable, and efficient scraping tool to extract high-quality B2B lead information (emails, phone numbers, company details, job titles) from target sources, following the data standards associated with Lake B2B.

## Technical Stack
- **Engine:** Node.js / TypeScript
- **Scraping Framework:** Firecrawl (via `firecrawl/cli`)
- **Data Format:** JSON / CSV (Lake B2B compatible)
- **Anti-Detection:** Stealth headers, proxy rotation, and browser fingerprinting.

## Lake B2B Data Schema (Target)
The tool should aim to populate the following fields:
- `First Name`, `Last Name`
- `Job Title`
- `Email Address` (Verified)
- `Company Name`
- `Industry`
- `Revenue Range`
- `Employee Count`
- `Direct Dial / Mobile Number`
- `LinkedIn URL`

## Scraping Strategy
1. **Discovery:** Identify search result pages or directory structures. Use `firecrawl map` to discover the site structure if targeting a specific directory.
2. **Extraction:** Use `firecrawl scrape` with `--only-main-content` to bypass LLM-unfriendly structures and extract clean Markdown/JSON.
3. **LLM-Powered Parsing:** 
   - Feed raw markdown into an LLM with the Lake B2B schema.
   - Use the `firecrawl-mcp` tools if deep research is needed to fill missing fields (e.g., finding a company's revenue when not on the main profile).
4. **Data Enrichment:**
   - Use LinkedIn to verify job titles.
   - Use specialized services (or search) to find direct dial numbers.
5. **Validation:** Implement checks for email formats and phone number validity.

## Lake B2B Specific Logic
- **Industry Mapping:** Standardize extracted industry names to Lake B2B's 50+ standard industry categories.
- **Job Function Tagging:** Map job titles to functions (e.g., "Director of Demand Gen" -> "Marketing").
- **Company Hierarchy:** Attempt to identify parent/child company relationships where applicable.

## Project Guidelines
- Always prioritize data accuracy over volume.
- Respect `robots.txt` where possible, but prioritize successful extraction for lead gen purposes.
- Maintain a modular architecture: `fetcher`, `parser`, `validator`, and `exporter`.
