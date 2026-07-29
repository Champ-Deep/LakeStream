# Technology Detection Catalog

How LakeStream detects a site's technology stack, and — importantly — why the
large fingerprint catalog is **not** committed to this repository.

## Architecture

Detection is deterministic and regex-based. The LLM never performs primary
extraction.

```
  homepage HTML + response headers          DNS records          TLS handshake
              │                                  │                     │
              ▼                                  ▼                     ▼
   extract_page_signals()  ──────────────────────────────────────────────
     script srcs · meta tags · cookies · headers · url · dns · certIssuer
              │
              ▼
   detect()  — precompiled catalog, targeted matching, literal prefilter
              │  → name, category, confidence (high|medium), version, evidence
              ▼
   judge_detections()  — OPTIONAL. Reviews only medium-confidence body matches.
                         Can REMOVE a false positive. Can never add one.
              │
              ▼
   TechStackMetadata / POST /api/tech
```

### Why it is fast

A naive "run every pattern over the whole HTML" loop costs ~25 s/page with a
few thousand signatures — about 690 hours per 100K pages. Three properties
bring that to ~24 ms/page (≈0.7 h per 100K on one core, minutes across cores):

1. **Precompiled once.** The catalog compiles at first use into a process-wide
   singleton, never per page and never per signature.
2. **Targeted matching.** Each signature declares what it matches against.
   `scriptSrc` patterns run over ~2 KB of extracted script URLs, not 200 KB of
   document. Only `html` signatures touch the full body.
3. **Literal prefilter.** The leading literal run of each pattern is checked
   with a plain substring test before the regex engine is invoked. The
   lowercased haystacks are computed once per page, not once per pattern.

Measure it on your own hardware:

```bash
python -m benchmarks.tech_engine_benchmark 100000
```

### Confidence

| Confidence | Meaning |
|---|---|
| `high` | Structural match — a response header, cookie, script URL, meta tag, DNS record, or TLS cert issuer. The site is running it. |
| `medium` | Body-text match, or a technology inferred via `implies`. The string appeared in the page, which is weaker: it may be a customer logo, an integrations page, or a blog post. |

Medium-confidence body matches are exactly what the LLM judge reviews.

### The judge is strictly subtractive

`src/services/tech_judge.py` is given only the candidate names, categories, and
their evidence snippets — never the raw page. It answers one question: does the
site *use* this, or merely *mention* it. It can only remove detections that
were sent for review, so it cannot delete a high-confidence structural match
and cannot invent a technology. Any failure (no API key, timeout, malformed
JSON) leaves the regex output untouched.

Enable with `ENABLE_TECH_JUDGE=true`, or per request with `{"judge": true}`.

---

## The catalog — and the licensing position

### What ships in this repo

`src/data/tech_signatures.py` — an original, hand-curated set (~130
signatures) written for this project. It is ours, with no third-party
provenance, and is always active.

### What does not ship, and why

The large community catalogs descended from Wappalyzer are the obvious way to
get to thousands of technologies. Wappalyzer itself went closed source in
August 2023; the maintained continuations are:

| Fork | License |
|---|---|
| [`enthec/webappanalyzer`](https://github.com/enthec/webappanalyzer) | **GPL-3.0** |
| [`dochne/wappalyzer`](https://github.com/dochne/wappalyzer) | **GPL-3.0** |
| [`HTTPArchive/wappalyzer`](https://github.com/HTTPArchive/wappalyzer) | see its LICENSE (fork of the last open version) |

Both forks we checked are **GPL-3.0**, not MIT — despite MIT being claimed in
some secondary write-ups. That distinction is the whole reason for this
document.

GPL-3.0 is a copyleft license whose obligations attach on **distribution**.
LakeStream is distributed — it is self-hosted, shipped as a Docker image, and
handed to clients — so vendoring GPL-3.0 material into this repository would
raise a real question about whether the combined work must also be offered
under GPL-3.0. (GPL-3.0 has no network/SaaS clause — that is AGPL — so a
purely hosted deployment is a different situation. LakeStream is not purely
hosted.)

**So the catalog is loaded at runtime from a path you supply, and is never
committed here.** Each operator obtains the catalog themselves, under whatever
terms they accept, and points LakeStream at it. The engine is
format-compatible; it holds no third-party data.

> This is engineering risk-reduction, not legal advice. If you intend to
> redistribute LakeStream *bundled* with a GPL catalog, or to resell output in
> a way that depends on it, get that reviewed by counsel first. The safe
> default — and the one this design implements — is: don't bundle it.

### Using an external catalog

```bash
# 1. Obtain a catalog (your call, under its own license) and inspect it
python scripts/import_tech_catalog.py --path ./src/technologies

# 2. Optionally merge many per-letter files into one directory
python scripts/import_tech_catalog.py --path ./src/technologies --merge ./catalog

# 3. Point LakeStream at it
TECH_CATALOG_PATH=/absolute/path/to/catalog
```

Verify what loaded:

```bash
curl -s localhost:7100/api/tech/catalog -H "X-API-Key: $KEY"
```

The external catalog is **merged on top of** the built-in set; on a name
collision the richer record (higher confidence, or one carrying a version)
wins, matched case-insensitively so `nginx` and `Nginx` never both appear.

### Supported fingerprint fields

Evaluated: `scriptSrc`, `scripts`, `html`, `url`, `headers`, `cookies`,
`meta`, `dns`, `certIssuer`, plus `implies`, `cats`, and the
`\;version:\1` / `\;confidence:50` modifiers.

Not evaluated: `js` and `dom` require executing page JavaScript and inspecting
a live DOM. Signatures relying *only* on those will never fire — the import
tool reports how many of those your catalog contains, so the number isn't a
surprise.

`categories.json`, if present next to the catalog, is authoritative for
category names. Without it a built-in numeric map is used, which is best-effort
and can mis-label categories added upstream after it was written.

---

## Known limitations

- **Coverage is only as good as the catalog.** With the built-in set alone,
  expect long-tail misses. That is the reason the external-catalog path exists.
- **Substring/regex fingerprinting has an inherent false-positive floor.** Any
  detector — ours or a commercial one — can mistake a mention for usage. Two
  mitigations are in place: confidence tiers that mark body matches as weaker,
  and the LLM judge. Real examples caught during development: a bare `shopify`
  signal firing on a customer-logo strip, and a `content/themes` signal for
  Ghost also matching WordPress's `wp-content/themes`.
- **`js`/`dom` signatures don't fire** (above).
- **Detection ≠ the whole run.** At 100K domains, network fetch and politeness
  limits dominate wall-clock, not this stage.
