# Tech-detection validation

Three layers keep the detector honest as the catalog grows:

## 1. Labeled corpus (automated, per PR / per wave)

`tests/fixtures/tech_corpus/` holds gzipped fixtures of real pages
(HTML + headers + DNS + cert issuer) with **hand-curated labels**:

- `present` — technologies the site operator verifiably runs. Every label
  cites its evidence in `source`. Labels are ground truth even when the page
  emits no detectable marker (djangoproject.com runs Django but its blog page
  carries no structural Django evidence — recall honestly pays for that).
- `absent` — confirmed false-positive traps: the technology is *mentioned*
  on the page but not run (WordPress on ghost.org, WooCommerce on stripe.com,
  Webflow's `wf-page` collision on hubspot.com, SvelteKit docs links on
  vercel.com). A detection of an absent-label is a proven FP.

Commands:

```bash
python scripts/score_tech_corpus.py                  # report
python scripts/score_tech_corpus.py --check          # gate: 0 violations,
                                                     # recall within 2pts of baseline
python scripts/score_tech_corpus.py --write-baseline # after a deliberate improvement
python scripts/refresh_tech_corpus.py --add <domain> # grow the corpus
```

Label curation rule learned on day one: **labels must be evidence-backed,
not assumed** — ghost.org's marketing site turned out to be Hugo-built
(`<meta name="generator" content="Hugo 0.119.0">`), not Ghost-served.

## 2. Gap analysis vs the reference ruleset (per wave)

`python scripts/tech_gap_analysis.py --refresh` downloads the
enthec/webappanalyzer ruleset (GPL-3.0) into the gitignored
`.tech-reference/` and reports, per corpus sample, technologies it detects
that we miss — ranked by prevalence, grouped by category, with the evidence
type. That output is the authoring queue for each wave.

License boundary (enforced by `tests/unit/test_no_third_party_catalog.py`):
the reference tells us **what** to cover, never **how**. Signatures are
authored from vendor docs and live-site inspection only.

## 3. BuiltWith free-tier spot checks (manual, per release)

No BuiltWith API keys or scraped BuiltWith data enter the repo. Protocol:

1. Pick 10 domains: 5 rotating from the corpus + 5 fresh ones relevant to
   current campaigns.
2. For each, open `builtwith.com/<domain>` in a browser and call our
   `POST /api/tech` for the same domain.
3. Record in the table below: agreements, things BuiltWith has that we miss
   (candidate signatures), things we have that BuiltWith lacks (verify
   manually before celebrating — our DNS/TLS evidence often legitimately
   sees more).
4. File misses as signature-authoring items; file our extras that turn out
   wrong as `absent` corpus labels.

| Date | Domain | Agreements | We missed | We had extra | Action |
|---|---|---|---|---|---|
| — | — | — | — | — | — |

## "Surpass Wappalyzer" — the measurable definition

On our labeled corpus, all three must hold (reported by
`score_tech_corpus.py --catalog .tech-reference/webappanalyzer`):

1. recall ≥ the reference ruleset's recall;
2. absent-label violations ≤ the reference's (target: zero);
3. nonzero detections from evidence classes a browser-bound detector cannot
   produce (DNS records, TLS cert issuer).
