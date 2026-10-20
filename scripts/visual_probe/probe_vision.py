"""Visual-scraping probe, step 2: screenshot -> vision model -> observations.

Sends ONE screenshot (from probe_browser.py) to a vision model through
OpenRouter, the same gateway LakeStream's llm_extractor already uses, and
records exactly what came back: model id, latency, token usage and the
structured observations. Only visible professional facts are requested;
anything not on the page is null.

No key -> the record says "blocked: OPENROUTER_API_KEY not set". That is the
honest L1 result until Deep provides access, not a verdict on the model.

Usage (repo root, inside .venv):
    python scripts/visual_probe/probe_vision.py data/visual_probe/<run>/<page>.viewport.png
    python scripts/visual_probe/probe_vision.py <png> --model google/gemini-2.5-flash
    python scripts/visual_probe/probe_vision.py <png> --model deepseek/deepseek-chat  # image test

Reads OPENROUTER_API_KEY from the environment, falling back to the repo .env.

Exit codes: 0 observations returned, 2 no key, 1 API error (exact error recorded).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "google/gemini-2.5-flash-lite"  # LakeStream's cheap fallback model

PROMPT = """You are looking at a screenshot of a public web page.
Report ONLY what is visibly on the page. Do not guess or infer anything that is not shown.
Return a single JSON object with these keys (use null when not visible):
  page_type: "person_profile" | "company_page" | "post" | "login_wall" | "captcha" | "other"
  login_or_access_wall: true ONLY when the page content itself is replaced by a sign-in,
    sign-up, CAPTCHA or access-denied page. A pop-up or overlay sitting on top of still
    visible content is NOT a wall: set this false and set signin_popup_present true.
  signin_popup_present: true/false
  name, headline_or_role, company, location
  about_or_bio: visible bio text, at most 300 characters, or null
  recent_posts: up to 5 items of {text_excerpt (max 200 chars), date_label_as_shown}
  topics_observed: up to 10 topics that appear in the visible text
  other_visible_facts: up to 10 short strings (articles, companies, events mentioned)
  confidence_notes: anything cut off, blurry or ambiguous
Never repeat a sentence. Keep the whole answer under 400 words.
"""


def _read_env_file(key: str) -> str | None:
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(f"{key}=") :
            return line.split("=", 1)[1].strip().strip('"').strip("'") or None
    return None


def _api_key() -> str | None:
    import os

    return os.environ.get("OPENROUTER_API_KEY") or _read_env_file("OPENROUTER_API_KEY")


async def observe(png: Path, model: str, api_key: str, max_tokens: int) -> dict:
    from openai import AsyncOpenAI

    data_url = "data:image/png;base64," + base64.b64encode(png.read_bytes()).decode()
    t0 = time.time()
    # Close the client inside the running loop; otherwise its transport is torn
    # down after asyncio.run() exits and logs "Event loop is closed".
    async with AsyncOpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL, timeout=60) as client:
        resp = await client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }],
        )
    latency_ms = int((time.time() - t0) * 1000)
    text = resp.choices[0].message.content or ""
    usage = resp.usage.model_dump() if resp.usage else None

    # Models often wrap JSON in a ```json fence; strip it before parsing.
    body = text.strip()
    if body.startswith("```"):
        body = body.strip("`")
        body = body[body.find("{"):body.rfind("}") + 1]
    try:
        observations = json.loads(body)
        parse_ok = True
    except json.JSONDecodeError:
        observations, parse_ok = None, False

    return {
        "model_requested": model,
        "model_returned": getattr(resp, "model", None),
        "latency_ms": latency_ms,
        "usage": usage,
        "json_parse_ok": parse_ok,
        "observations": observations,
        "raw_text": text if not parse_ok else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("png", type=Path)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--max-tokens", type=int, default=1500)
    ap.add_argument("--out", type=Path, default=None,
                    help="record path; default <png dir>/vision_record.<model>.json")
    args = ap.parse_args()

    record: dict = {
        "screenshot": str(args.png),
        "captured_at": datetime.now(UTC).isoformat(),
        "gateway": OPENROUTER_BASE_URL,
    }
    exit_code = 0
    api_key = _api_key()
    if not api_key:
        record["outcome"] = "blocked"
        record["reason"] = "OPENROUTER_API_KEY not set (requested from Deep); no API call made"
        exit_code = 2
    elif not args.png.exists():
        record["outcome"] = "error"
        record["reason"] = f"screenshot not found: {args.png}"
        exit_code = 1
    else:
        try:
            record.update(asyncio.run(observe(args.png, args.model, api_key, args.max_tokens)))
            record["outcome"] = "ok" if record["json_parse_ok"] else "partial"
        except Exception as exc:  # record the exact failure, that is the evidence
            record["outcome"] = "error"
            record["model_requested"] = args.model
            record["error"] = f"{type(exc).__name__}: {exc}"
            exit_code = 1

    out = args.out or args.png.with_name(f"vision_record.{args.model.replace('/', '_')}.json")
    out.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps(record, indent=2))
    print(f"\nrecord: {out}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
