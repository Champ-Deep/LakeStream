"""Visual-scraping probe, step L3: cited facts + outreach hooks from the loop.

For each URL: run the L2 loop, merge the vision observations of every step
into one record where EVERY non-empty fact carries a source URL, capture
time and screenshot file, then ask a text model for up to two outreach
hooks, each tied to a cited post or fact and labelled as a suggestion.
Nothing is sent anywhere. Missing or private data stays null.

Outputs under data/visual_probe/l3_<timestamp>/:
    <slug>/                 loop trace + step PNGs
    <slug>.facts.json       merged, cited facts + hooks
    benchmark.csv / .json   one row per URL: reached/blocked/failed, counts, cost

Usage (repo root, inside .venv):
    python scripts/visual_probe/extract.py URL [URL ...] [--seed 11] [--hooks-model MODEL]

Needs OPENROUTER_API_KEY and TYPESAFE_API_KEY (env or repo .env).
Exit code: 0 when every URL produced a record (reached or honestly blocked), 1 on error.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import loop  # noqa: E402
import probe_vision  # noqa: E402

SCALAR_FIELDS = ("name", "headline_or_role", "company", "location", "about_or_bio")
HOOKS_PROMPT = """You write short outreach hooks for a B2B salesperson.
Below are facts observed on a public professional page, each with an evidence id.
Write at most 2 hooks. Each hook must reference ONE specific post or fact by its evidence id,
be one or two sentences, professional, and make no claims beyond the cited fact.
Return JSON: {"hooks": [{"text": ..., "evidence": "<id>", "reason": ...}]}
If there is no post or concrete fact worth referencing, return {"hooks": []}.
"""


def slug(url: str) -> str:
    p = urlparse(url)
    return re.sub(r"[^a-z0-9]+", "_", p.netloc + p.path, flags=re.I).strip("_")[:60]


def cite(step: dict, trace: dict, value):
    started = datetime.fromisoformat(trace["started_at"])
    captured = started + timedelta(milliseconds=step["t_ms"])
    return {
        "value": value,
        "source_url": step["before"]["url"],
        "captured_at": captured.isoformat(),
        "screenshot": step["before"]["png"],
    }


def merge_facts(trace: dict) -> dict:
    facts: dict = {f: None for f in SCALAR_FIELDS}
    posts: list[dict] = []
    seen_posts: set[str] = set()
    topics: dict[str, dict] = {}
    others: dict[str, dict] = {}
    for step in trace.get("steps", []):
        obs = (step.get("vision") or {}).get("observations") or {}
        for field in SCALAR_FIELDS:
            if facts[field] is None and obs.get(field):
                facts[field] = cite(step, trace, obs[field])
        for post in obs.get("recent_posts") or []:
            text = (post.get("text_excerpt") or "").strip()
            key = re.sub(r"\W+", " ", text.lower())[:60]
            if text and key not in seen_posts:
                seen_posts.add(key)
                entry = cite(step, trace, text)
                entry["date_label_as_shown"] = post.get("date_label_as_shown")
                entry["id"] = f"post{len(posts) + 1}"
                posts.append(entry)
        for topic in obs.get("topics_observed") or []:
            if topic and topic.lower() not in topics:
                entry = cite(step, trace, topic)
                entry["kind"] = "observed"  # never "certain"; inferred traits are not produced
                topics[topic.lower()] = entry
        for fact in obs.get("other_visible_facts") or []:
            if fact and fact.lower() not in others:
                entry = cite(step, trace, fact)
                entry["id"] = f"fact{len(others) + 1}"
                others[fact.lower()] = entry
    return {
        **facts,
        "recent_posts": posts,
        "topics": list(topics.values()),
        "other_visible_facts": list(others.values()),
    }


async def outreach_hooks(facts: dict, model: str, api_key: str) -> dict:
    from openai import AsyncOpenAI

    evidence = [
        f"[{p['id']}] post ({p.get('date_label_as_shown') or 'date not shown'}): {p['value']}"
        for p in facts["recent_posts"]
    ]
    evidence += [f"[{f['id']}] fact: {f['value']}" for f in facts["other_visible_facts"][:10]]
    if not evidence:
        return {"hooks": [], "note": "no cited post or fact available"}
    header = {k: (facts[k] or {}).get("value") for k in ("name", "headline_or_role", "location")}
    prompt = (HOOKS_PROMPT + "\nPage: " + json.dumps(header)
              + "\nEvidence:\n" + "\n".join(evidence))
    async with AsyncOpenAI(api_key=api_key, base_url=probe_vision.OPENROUTER_BASE_URL,
                           timeout=60) as client:
        resp = await client.chat.completions.create(
            model=model, max_tokens=500, messages=[{"role": "user", "content": prompt}],
        )
    text = (resp.choices[0].message.content or "").strip()
    if text.startswith("```"):
        text = text[text.find("{"):text.rfind("}") + 1]
    try:
        hooks = json.loads(text).get("hooks", [])
    except (json.JSONDecodeError, AttributeError):
        return {"hooks": [], "note": "hook model returned non-JSON", "raw": text[:300]}
    ids = {p["id"] for p in facts["recent_posts"]} | {f["id"] for f in facts["other_visible_facts"]}
    return {
        "hooks": [
            {**h, "label": "suggestion, not sent", "evidence_valid": h.get("evidence") in ids}
            for h in hooks[:2]
        ],
        "model": model,
        "usage": resp.usage.model_dump() if resp.usage else None,
    }


async def process(url: str, out_root: Path, seed: int | None, hooks_model: str) -> dict:
    api_key = probe_vision._api_key()
    run_dir = out_root / slug(url)
    trace = await loop.run(url, run_dir, seed=seed, headless=True,
                           vision_model=probe_vision.DEFAULT_MODEL,
                           max_steps=8, budget_s=180, max_model_calls=16)
    facts = merge_facts(trace)
    status = {"reached": "reached", "stopped_wall": "blocked", "error": "failed"}.get(
        trace.get("outcome"), "partial")
    hooks: dict = {"hooks": []}
    if status != "blocked" and api_key:
        try:
            hooks = await outreach_hooks(facts, hooks_model, api_key)
        except Exception as exc:  # a hook failure must not lose the page's facts
            hooks = {"hooks": [], "error": f"{type(exc).__name__}: {exc}"[:300]}
    record = {
        "url": url,
        "status": status,
        "loop_outcome": trace.get("outcome"),
        "loop_reason": trace.get("reason"),
        "captured_at": trace.get("started_at"),
        "facts": facts,
        "outreach_hooks": hooks,
        "cost": {
            "vision_usd": trace.get("vision_cost_usd"),
            "jev_input_tokens": trace.get("jev_tokens"),
            "model_calls": trace.get("model_calls"),
            "total_ms": trace.get("total_ms"),
        },
        "evidence_dir": str(run_dir),
    }
    facts_path = out_root / f"{slug(url)}.facts.json"
    facts_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def write_benchmark(out_root: Path, rows: list[dict]) -> None:
    with (out_root / "benchmark.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    (out_root / "benchmark.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("urls", nargs="+")
    ap.add_argument("--out", default=str(REPO_ROOT / "data" / "visual_probe"))
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--hooks-model", default=probe_vision.DEFAULT_MODEL)
    args = ap.parse_args()

    out_root = Path(args.out) / datetime.now().strftime("l3_%Y%m%d_%H%M%S")
    out_root.mkdir(parents=True, exist_ok=True)
    rows = []
    for url in args.urls:
        print(f"=== {url}", flush=True)
        rec = asyncio.run(process(url, out_root, args.seed, args.hooks_model))
        f = rec["facts"]
        row = {
            "url": url, "status": rec["status"], "loop_outcome": rec["loop_outcome"],
            "name": (f["name"] or {}).get("value"),
            "role_or_industry": (f["headline_or_role"] or {}).get("value"),
            "location": (f["location"] or {}).get("value"),
            "bio_present": bool(f["about_or_bio"]),
            "posts": len(f["recent_posts"]), "topics": len(f["topics"]),
            "other_facts": len(f["other_visible_facts"]),
            "hooks": len(rec["outreach_hooks"].get("hooks", [])),
            "time_s": round((rec["cost"]["total_ms"] or 0) / 1000, 1),
            "model_calls": rec["cost"]["model_calls"],
            "vision_usd": round(rec["cost"]["vision_usd"] or 0, 5),
            "jev_tokens": rec["cost"]["jev_input_tokens"],
        }
        rows.append(row)
        print(json.dumps(row), flush=True)
        write_benchmark(out_root, rows)  # after every URL, so a crash keeps earlier rows

    statuses = ("reached", "blocked", "partial", "failed")
    counts = {s: sum(r["status"] == s for r in rows) for s in statuses}
    print("\nsummary:", json.dumps(counts), "| out:", out_root)
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
