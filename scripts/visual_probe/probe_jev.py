"""Visual-scraping probe, step 3: can Jev pick the next bounded browser action?

Jev is TypeSafe's decision model (https://api.typesafe.ai/v1/systemone, the
same endpoint jev-ultrafast uses). It is not a chat model: it takes a page
STATE (url, title, text, elements, recent actions) plus QUESTIONS of type
"choice" and returns one choice per question with probabilities and a
confidence. The request schema has no image field, so this probe feeds Jev
the observations produced by probe_vision.py and records what it decides.

Two modes, matching checklist L1:
  text mode   (default)  vision observations -> Jev -> one allowed action
  image mode  (--image)  sends a screenshot inside the state to document how
                         the endpoint responds to image input (expected: rejected)

Usage (repo root, inside .venv):
    python scripts/visual_probe/probe_jev.py --observations <run>/vision_record.<model>.json
    python scripts/visual_probe/probe_jev.py --image <run>/<page>.viewport.png

Env: TYPESAFE_API_KEY (or JEV_API_KEY), optional TYPESAFE_MODEL (default jev-latest).
Read from the environment, falling back to the repo .env.

Exit codes: 0 valid action returned, 2 not configured, 1 call failed (error recorded).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENDPOINT = "https://api.typesafe.ai/v1/systemone"

# The bounded action set from checklist L2. Jev must pick exactly one.
ACTIONS = {
    "SCROLL": "Scroll down to reveal more of the page (posts, activity, about text).",
    "CLICK_ACTIVITY": "Click the link or tab that opens the public posts/activity.",
    "BACK": "Go back to the previous page.",
    "STOP": "Stop: a login wall, CAPTCHA, access-denied page is shown, or the goal is already met.",
}
GOAL = (
    "Reach the publicly visible recent posts/activity of this profile or company page, "
    "then stop. Never try to log in, sign up, or get past an access wall."
)
RULES = [
    "If login_or_access_wall is true and no page content is readable, choose STOP.",
    "If recent_posts already contains items, choose STOP.",
    "If page content is readable but no posts are visible yet, "
    "prefer CLICK_ACTIVITY, else SCROLL.",
]


def _read_env_file(key: str) -> str | None:
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'") or None
    return None


def _api_key() -> str | None:
    for key in ("TYPESAFE_API_KEY", "JEV_API_KEY"):
        value = os.environ.get(key) or _read_env_file(key)
        if value:
            return value
    return None


def build_body(page: dict, observations: dict | None, image_b64: str | None) -> dict:
    state: dict = {
        "page": {
            "url": page.get("url", ""),
            "title": page.get("title", ""),
            "text": json.dumps(observations, ensure_ascii=False) if observations else "",
        },
        "elements": [],
        "recent_actions": [],
    }
    if image_b64:
        state["page"]["image_png_base64"] = image_b64
    return {
        "model": os.environ.get("TYPESAFE_MODEL", "jev-latest"),
        "state": state,
        "questions": {
            "next_action": {
                "type": "choice",
                "criteria": ACTIONS,
                "instructions": {"goal": GOAL, "rules": RULES},
            }
        },
    }


def call_jev(api_key: str, body: dict) -> dict:
    import httpx

    t0 = time.time()
    with httpx.Client(timeout=25) as client:
        resp = client.post(ENDPOINT, json=body, headers={"Authorization": f"Bearer {api_key}"})
    latency_ms = int((time.time() - t0) * 1000)
    record: dict = {"http_status": resp.status_code, "latency_ms": latency_ms}
    try:
        data = resp.json()
    except ValueError:
        record["raw_body"] = resp.text[:500]
        return record
    if resp.is_error:
        record["error_body"] = data if len(json.dumps(data)) < 2000 else str(data)[:2000]
        return record
    answer = (data.get("answers") or {}).get("next_action") or {}
    record.update({
        "model_returned": data.get("model"),
        "usage": data.get("usage"),
        "decision": answer.get("choice"),
        "confidence": answer.get("confidence"),
        "probabilities": answer.get("probabilities"),
        "action_allowed": answer.get("choice") in ACTIONS,
    })
    return record


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--observations", type=Path, help="vision_record.*.json from probe_vision.py")
    src.add_argument("--image", type=Path, help="screenshot PNG, to test direct image input")
    ap.add_argument("--browser-record", type=Path, default=None,
                    help="browser_record.json for url/title; defaults to the one beside the input")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    base = args.image or args.observations
    browser_record = args.browser_record or base.with_name("browser_record.json")
    page = json.loads(browser_record.read_text(encoding="utf-8")) if browser_record.exists() else {}

    record: dict = {
        "captured_at": datetime.now(UTC).isoformat(),
        "mode": "image" if args.image else "text",
        "endpoint": ENDPOINT,
        "page_url": page.get("url"),
        "input": str(base),
    }
    exit_code = 0
    api_key = _api_key()
    if not api_key:
        record["outcome"] = "not_configured"
        record["reason"] = "TYPESAFE_API_KEY / JEV_API_KEY not set; no call made"
        exit_code = 2
    else:
        if args.image:
            image_b64 = base64.b64encode(args.image.read_bytes()).decode()
            body = build_body(page, None, image_b64)
            record["image_bytes"] = args.image.stat().st_size
        else:
            obs = json.loads(args.observations.read_text(encoding="utf-8")).get("observations")
            body = build_body(page, obs, None)
            record["observations_present"] = obs is not None
        record["request_bytes"] = len(json.dumps(body))
        try:
            record.update(call_jev(api_key, body))
            if record.get("action_allowed"):
                record["outcome"] = "ok"
            elif record.get("http_status", 0) >= 400:
                record["outcome"] = "rejected"
                exit_code = 1
            else:
                record["outcome"] = "partial"
        except Exception as exc:
            record["outcome"] = "error"
            record["error"] = f"{type(exc).__name__}: {exc}"
            exit_code = 1

    out = args.out or base.with_name(f"jev_record.{record['mode']}.json")
    out.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps(record, indent=2))
    print(f"\nrecord: {out}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
