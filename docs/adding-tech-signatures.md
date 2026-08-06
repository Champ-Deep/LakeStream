# Adding a tech-detection signature

Target: a new signature should take about 10 minutes, tests included.

## 1. Pick the evidence — strongest first

The engine trusts evidence in this order. Author the strongest signal the
technology actually emits; add a body signal only when nothing structural
exists.

| Evidence | scope / field | Confidence | Example |
|---|---|---|---|
| Response header | `"scope": "header", "header_name": "server"` | high | `cf-ray` → Cloudflare |
| Cookie name | `"scope": "cookie"` | high | `_shopify_s` → Shopify |
| `<meta name=generator>` | `"scope": "meta", "meta_name": "generator"` | high | `WordPress 6.x` |
| DOM selector | `"scope": "dom"` (signals are CSS selectors) | high | `html[data-wf-site]` → Webflow |
| Script URL / asset path | default scope, vendor-unique path | high (structural blob) | `cdn.segment.com/analytics.js` |
| Body text | default scope | medium | asset paths like `wp-content` |
| Bare vendor word | default scope + `"confidence": "low"` | low (audit-only) | never in headline fields |

## 2. Add the entry

One dict in `src/data/tech_signatures.py` (schema documented in its
docstring). Optional keys: `implies` (e.g. WordPress → PHP), `website`,
`confidence` override, version capture via `\;version:\1`.

```python
{
    "name": "Plausible",
    "category": "analytics",
    "signals": [r"plausible\.io/js/(?:script|plausible)"],
},
```

## 3. Respect the false-positive rules

- **cdn/hosting**: a body signal may only be a hostname unique to the
  vendor's serving infrastructure. No bare vendor words, no shared-cloud
  hostnames. (Empirically enforced — see the docstring; the rule removed 103
  bogus detections.)
- **Everywhere else**: prefer markers a real install emits (asset paths,
  query params, data attributes) over the vendor's name. Vendor names appear
  in nav links, comparison pages ("X vs Y"), and customer-logo carousels —
  reproduced live: WordPress on ghost.org, WooCommerce on stripe.com.
- A weak-but-useful bare word goes in with `"confidence": "low"` — it stays
  visible in the `detections` audit trail but never asserts in the headline
  fields.
- Short prefixes collide: `wf-page` (intended Webflow) matched an unrelated
  `.wf-page-header` web-font class on hubspot.com. Anchor the pattern or use
  a `dom` selector instead.

## 4. Category routing

`category` must be routed in `tech_engine.py`: either mapped in
`CATEGORY_TO_FIELD` (renders as a first-class field) or listed in
`INTENTIONALLY_OTHER` (deliberately folded into `other_technologies`). A
guard test fails otherwise — that's intentional, so nothing silently
disappears from the UI. New first-class fields also need a matching list on
`TechStackMetadata` (`src/models/scraped_data.py`).

## 5. Test it

Copy the pattern in
`tests/unit/scraping/test_tech_catalog_detection.py`:

- a positive test with a minimal HTML/header snippet a real install emits;
- if the signal could FP, a negative test naming the real site that would
  have fooled it (see `TestFalsePositiveDiscipline` — every test there is a
  named regression).

```bash
pytest tests/unit/test_tech_engine.py tests/unit/scraping/test_tech_catalog_detection.py -q
python -m benchmarks.tech_engine_benchmark --max-ms-per-page 300
```

## 6. Never copy third-party fingerprint data

The enthec/webappanalyzer ruleset is GPL-3.0 and is used as a *coverage
reference only* (see `docs/TECH_CATALOG.md` and
`scripts/tech_gap_analysis.py`). It tells us **what** to cover; every
signature here is authored from first principles — vendor docs and live-site
inspection. A CI test fails if third-party catalog data lands in the repo.
