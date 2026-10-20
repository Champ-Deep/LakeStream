# Visual scraping probe (checklist L1)

Three small scripts that test whether "screenshot → vision model → action
decision" works with the tools we actually have. Each one writes a JSON
record that is the evidence; nothing here touches LakeStream's job queue,
database or default scraping path. Measured results for 2026-10-19 are in
[RESULTS.md](RESULTS.md).

| Step | Script | Proves | Needs |
|---|---|---|---|
| 1 | `probe_browser.py` | the stealth browser loads a page and we get a screenshot + block/login-wall verdict | nothing (browser is cached locally) |
| 2 | `probe_vision.py` | a vision model turns that screenshot into structured observations | `OPENROUTER_API_KEY` |
| 3 | `probe_jev.py` | Jev picks one bounded action from the observations | `TYPESAFE_API_KEY` |
| L2 | `loop.py` | the whole loop: screenshot → vision → Jev → act → screenshot → verify, with hard budgets and a trace | both keys |
| L3 | `extract.py` | runs the loop on N URLs, merges every step's observations into cited facts (source URL, capture time, screenshot), adds up to 2 outreach hooks tied to evidence, writes `benchmark.csv` | both keys |
| L4 | `proxy_compare.py` | same URLs direct vs one sticky proxy session; time, bytes, outcome, cost floor | `VISUAL_PROBE_PROXY_*` env for the proxy leg |

## The loop (L2)

`loop.py` opens one URL and repeats: viewport screenshot → Gemini observations
→ Jev chooses one action from those actually available on the page
(`SCROLL`, `CLICK_ACTIVITY` if a Posts/Activity link is visible, `CLOSE_POPUP`
if a dismiss button is visible, `BACK`, `STOP`) → the browser executes it →
new screenshot → progress check (URL, scroll position, screenshot hash).

Stops on: posts visible (goal), a login wall / CAPTCHA / block, Jev choosing
STOP, two steps without change, or the budgets (`--max-steps 8`,
`--budget-s 180`, `--max-model-calls 16`). Jev sees a one-line facts header
(wall / pop-up / posts visible / scroll %) above the page text; a STOP that
contradicts those facts is sent back once with the reason, and Jev's second
answer stands. Every step is written to `trace.json` with the before/after
PNGs, the full vision observations, Jev's probabilities and confidence,
latency and cost.

Before the first screenshot the loop closes a dismissible sign-in overlay if
one sits on top of public content (`button.sign-in-modal__dismiss` on
LinkedIn). That is UI hygiene, recorded as `pre_step` in the trace, not a
navigation decision for Jev. Without it the modal covers the middle of every
screenshot and post text under it is read garbled.

```powershell
python scripts/visual_probe/loop.py https://www.linkedin.com/company/microsoft/ --seed 7
```

## Cited extraction and benchmark (L3)

```powershell
python scripts/visual_probe/extract.py URL1 URL2 URL3 URL4 URL5
```

Per URL: `<slug>/` (trace + PNGs) and `<slug>.facts.json`. Every non-empty
fact is `{value, source_url, captured_at, screenshot}`; posts and other facts
get ids (`post1`, `fact2`) that the hooks must reference. Topics are marked
`observed`; no personal traits are inferred. Hooks are labelled
"suggestion, not sent" and carry `evidence_valid`. `benchmark.csv` has one
row per URL with reached / blocked / partial / failed and field counts.
Extraction makes no extra vision calls: it merges the observations the loop
already recorded. One small text call per page writes the hooks.

## Direct vs proxy (L4)

```powershell
$env:VISUAL_PROBE_PROXY_SERVER   = "http://host:port"     # or socks5://
$env:VISUAL_PROBE_PROXY_USERNAME = "..."
$env:VISUAL_PROBE_PROXY_PASSWORD = "..."
python scripts/visual_probe/proxy_compare.py https://example.com https://www.linkedin.com/company/microsoft/ --cost-per-gb 3
```

One sticky session, same seed and pace on both legs, no rotation. Without
the env vars only the direct leg runs and the proxy column says
`not_configured`.

## Browser choice

The brief says Camoufox. This probe uses **invisible_playwright**
(`pip install invisible-playwright`), a patched Firefox with the same
Playwright API, because it drops into the existing Playwright fetcher with a
two-line change. Swapping back to Camoufox is the same two lines.

First launch downloads a 52 MB GeoIP database (used to keep timezone and
locale consistent with the exit IP). On a slow link this takes several
minutes and looks like a hang; let it finish once.

## Jev

Jev is TypeSafe's decision model, reached at
`https://api.typesafe.ai/v1/systemone` (the endpoint `jev-ultrafast` uses).
It is not a chat model: the request carries a page *state* (url, title,
text, elements, recent actions) and *questions* of type `choice`; the reply
is one choice per question with probabilities and a confidence. The schema
has no image field, so the loop is: screenshot → vision model → observations
→ Jev → action. Sending a screenshot inside the state is rejected with
HTTP 400 `max_tokens_exceeded` (recorded in RESULTS.md).

## Run

```powershell
.venv\Scripts\activate
python scripts/visual_probe/probe_browser.py https://example.com
python scripts/visual_probe/probe_vision.py data/visual_probe/<run>/example_com.viewport.png
python scripts/visual_probe/probe_jev.py --observations data/visual_probe/<run>/vision_record.google_gemini-2.5-flash-lite.json
```

Outputs land in `data/visual_probe/<timestamp>/` (gitignored). Keys are read
from the environment or the repo `.env` (gitignored). Records never contain
keys or proxy credentials.

## Inside LakeStream (L5)

The same loop is available as an optional fetch tier, off by default:

```env
ENABLE_VISUAL_FETCHER=true
OPENROUTER_API_KEY=...
TYPESAFE_API_KEY=...
```

Then either path through the existing API:

```bash
# one URL, synchronous: markdown + facts
curl -X POST http://localhost:8000/api/scrape/url -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://www.linkedin.com/company/microsoft/", "tier": "visual"}'

# queued job: facts land in scraped_data as an EXTRACTED record (metadata.source = "visual")
curl -X POST http://localhost:8000/api/scrape/execute -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"domain": "www.linkedin.com/company/microsoft", "tier": "visual", "max_pages": 1, "raw_only": true}'
curl http://localhost:8000/api/export/json/<job_id> -H "Authorization: Bearer $TOKEN"
```

`facts.status` is always one of `success`, `partial`, `blocked`, `failed`;
a wall produces a `blocked` record rather than nothing. The tier is never
part of escalation: without an explicit `tier: "visual"` nothing changes.

## Rules baked in

- A login wall, real challenge page or 403/429/503/999 is recorded as
  `login_wall` / `captcha` / `blocked` and the run ends. No retry, no
  cookies, no IP cycling.
- A reCAPTCHA script on an otherwise rendered page (LinkedIn's sign-in form
  has one on every public page) is noted as a signal, not treated as a block.
- Only URLs on Deep's approved list go through step 1.
- Missing API access is recorded as `blocked` / `not_configured`, not as a
  model failure.
