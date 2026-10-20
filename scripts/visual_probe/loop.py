"""Visual-scraping probe, step L2: the smallest screenshot-guided browser loop.

    screenshot -> vision observations -> Jev picks ONE bounded action
    -> browser executes it -> new screenshot -> progress check -> repeat

Actions Jev may choose from (only the ones that exist on the page are offered):
    SCROLL          scroll down one screen
    CLICK_ACTIVITY  click a visible "Posts" / "Activity" link
    CLOSE_POPUP     click the Dismiss button of an overlay sitting on top of public content
    BACK            browser back
    STOP            goal met, or a wall is shown

Hard limits: --max-steps, --budget-s, --max-model-calls. The loop also stops on
a login wall, CAPTCHA or block (classify() from probe_browser), and after two
consecutive steps with no progress (same URL, same scroll position, same
screenshot). No cookies, no retries past a wall, no IP changes.

Usage (repo root, inside .venv):
    python scripts/visual_probe/loop.py https://www.linkedin.com/company/microsoft/
    python scripts/visual_probe/loop.py <url> --max-steps 6 --budget-s 180 --seed 42

Writes data/visual_probe/loop_<timestamp>/trace.json plus step_N.before/after.png.
Exit codes: 0 reached posts/activity, 2 stopped at a wall or no progress, 3 budget
exhausted, 1 error.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import probe_browser  # noqa: E402
import probe_jev  # noqa: E402
import probe_vision  # noqa: E402

ACTIVITY_LINKS_JS = """
() => {
  const out = [];
  for (const a of document.querySelectorAll('a[href]')) {
    const r = a.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    const href = a.href || '';
    const attr = a.getAttribute('href') || '';
    const text = (a.innerText || '').trim().slice(0, 60);
    const byHref = /\\/posts\\/?(\\?|$)|recent-activity/i.test(href);
    const byText = /^(posts|activity|show all posts)$/i.test(text);
    if (byHref || byText) {
      out.push({href, attr, text, visible: r.top >= 0 && r.top < window.innerHeight});
    }
  }
  return out.slice(0, 10);
}
"""
DISMISS_SELECTORS = [
    "button.sign-in-modal__dismiss",  # LinkedIn's contextual sign-in modal
    "button.modal__dismiss",
    'button[aria-label="Dismiss"]',
    'button[aria-label="Close"]',
]
GOAL_URL_HINT = ("/posts", "recent-activity")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


async def page_state(page) -> dict:
    links = await page.evaluate(ACTIVITY_LINKS_JS)
    # Several hidden Dismiss buttons can precede the visible one in DOM order
    # (LinkedIn's app-upsell toast), so every match is checked for visibility.
    dismiss = None
    for sel in DISMISS_SELECTORS:
        loc = page.locator(sel)
        for i in range(await loc.count()):
            if await loc.nth(i).is_visible():
                dismiss = f"{sel} >> nth={i}"
                break
        if dismiss:
            break
    return {
        "url": page.url,
        "title": await page.title(),
        "scroll_y": await page.evaluate("window.scrollY"),
        "scroll_max": await page.evaluate(
            "Math.max(0, document.documentElement.scrollHeight - window.innerHeight)"
        ),
        "activity_links": links,
        "dismiss_selector": dismiss,
        "body_text": (await page.evaluate("document.body ? document.body.innerText : ''")
                      or "")[:2000],
    }


def offered_actions(state: dict, observations: dict | None) -> dict[str, str]:
    pct = int(100 * state["scroll_y"] / state["scroll_max"]) if state["scroll_max"] else 100
    actions = {
        "SCROLL": (
            f"Scroll down one screen to find the Updates/posts section further down "
            f"(currently at {pct}% of the page)."
        )
    }
    if state["activity_links"]:
        labels = ", ".join(repr(a["text"] or a["href"][-40:]) for a in state["activity_links"][:3])
        actions["CLICK_ACTIVITY"] = f"Click the visible posts/activity link ({labels})."
    if state["dismiss_selector"]:
        actions["CLOSE_POPUP"] = (
            "Close the sign-in pop-up overlay that sits on top of already visible public content."
        )
    actions["BACK"] = probe_jev.ACTIONS["BACK"]
    actions["STOP"] = (
        "Stop ONLY if the page content is replaced by a login wall, CAPTCHA or access-denied "
        "page, or recent posts are already visible. A sign-in pop-up over readable content "
        "is not a reason to stop."
    )
    return actions


def jev_body(state: dict, observations: dict | None, history: list[dict], actions: dict,
             feedback: str | None = None) -> dict:
    pct = int(100 * state["scroll_y"] / state["scroll_max"]) if state["scroll_max"] else 100
    obs = observations or {}
    text = (
        f"facts: wall={bool(obs.get('login_or_access_wall'))} "
        f"signin_popup={bool(obs.get('signin_popup_present'))} "
        f"posts_visible={len(obs.get('recent_posts') or [])} "
        f"content_readable={bool(obs.get('name') or obs.get('about_or_bio'))} "
        f"scroll={pct}% ({state['scroll_y']}/{state['scroll_max']} px)\n"
    )
    if observations:
        text += "vision observations: " + json.dumps(observations, ensure_ascii=False)[:2500] + "\n"
    text += "--- page text (may start with pop-up copy) ---\n" + state["body_text"]
    rules = probe_jev.RULES + [
        "login_or_access_wall=true together with a readable name, bio or "
        "about text means a sign-in overlay on public content, not a wall: "
        "keep going.",
        "If scroll position is below 100% and no posts are visible, SCROLL.",
        "If a CLOSE_POPUP option exists and the page content is readable, "
        "closing the pop-up is allowed before scrolling.",
        "Never choose an action that is not offered.",
    ]
    if feedback:
        rules.append(feedback)
    return {
        "model": os.environ.get("TYPESAFE_MODEL", "jev-latest"),
        "state": {
            "page": {"url": state["url"], "title": state["title"], "text": text},
            "elements": [
                {"index": str(i + 1), "label": a["text"] or a["href"], "operations": ["CLICK"]}
                for i, a in enumerate(state["activity_links"])
            ],
            "recent_actions": [
                {"action": h["decision"], "page_changed": h["progress"]} for h in history[-5:]
            ],
        },
        "questions": {
            "next_action": {
                "type": "choice",
                "criteria": actions,
                "instructions": {"goal": probe_jev.GOAL, "rules": rules},
            }
        },
    }


async def execute(page, decision: str, state: dict) -> str:
    """Run the chosen action. Returns a short note for the trace."""
    if decision == "SCROLL":
        await page.evaluate("window.scrollBy(0, Math.round(window.innerHeight * 0.9))")
        await page.wait_for_timeout(1500)
        return "scrolled one screen"
    if decision == "CLICK_ACTIVITY":
        target = state["activity_links"][0]
        # Match the raw href attribute (LinkedIn's are relative); a.href is absolute.
        attr = target["attr"].replace("\\", "\\\\").replace('"', '\\"')
        await page.locator(f'a[href="{attr}"]').first.click(timeout=10_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            pass
        return f"clicked {target['text'] or target['href']}"
    if decision == "CLOSE_POPUP":
        await page.click(state["dismiss_selector"], timeout=5_000)
        await page.wait_for_timeout(800)
        return "closed pop-up"
    if decision == "BACK":
        await page.go_back(timeout=15_000)
        return "went back"
    return "stopped"


async def run(url: str, out_dir: Path, *, seed: int | None, headless: bool, vision_model: str,
              max_steps: int, budget_s: float, max_model_calls: int,
              html_sink: dict | None = None) -> dict:
    """Run the loop; ``html_sink["html"]`` receives the latest page HTML when given
    (the LakeStream fetcher needs it; the trace file deliberately omits it)."""
    from invisible_playwright.async_api import InvisiblePlaywright

    openrouter_key = probe_vision._api_key()
    jev_key = probe_jev._api_key()
    out_dir.mkdir(parents=True, exist_ok=True)
    trace: dict = {
        "url": url, "started_at": datetime.now(UTC).isoformat(), "seed": seed,
        "vision_model": vision_model, "limits": {
            "max_steps": max_steps, "budget_s": budget_s, "max_model_calls": max_model_calls,
        },
        "steps": [], "model_calls": 0, "vision_cost_usd": 0.0, "jev_tokens": 0,
    }
    t0 = time.time()
    no_progress = 0
    try:
        if not openrouter_key or not jev_key:
            raise ValueError("OPENROUTER_API_KEY and TYPESAFE_API_KEY are required")
        ip = InvisiblePlaywright(seed=seed, headless=headless)
        async with ip as browser:
            trace["seed"] = ip.seed
            page = await browser.new_page()
            resp = await page.goto(url, timeout=60_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:
                pass
            html = await page.content()
            if html_sink is not None:
                html_sink["html"] = html
            outcome, signals = probe_browser.classify(
                resp.status if resp else 0, page.url, await page.title(), html
            )
            trace["landing"] = {"status": resp.status if resp else 0, "outcome": outcome,
                                "signals": signals}
            if outcome != "ok":
                trace["outcome"] = "stopped_wall"
                trace["reason"] = f"landing page classified {outcome}: {signals}"
                return trace

            # Pre-step: close a dismissible sign-in overlay sitting on public
            # content so the screenshots show the page, not the pop-up. This is
            # UI hygiene (a click on Dismiss), not a navigation decision, so it
            # is done deterministically and recorded rather than asked of Jev.
            pre = await page_state(page)
            if pre["dismiss_selector"]:
                try:
                    await page.click(pre["dismiss_selector"], timeout=5_000)
                    await page.wait_for_timeout(800)
                    trace["pre_step"] = {"closed_popup": pre["dismiss_selector"]}
                except Exception as exc:
                    trace["pre_step"] = {"close_failed": f"{type(exc).__name__}: {exc}"[:160]}

            for step_no in range(1, max_steps + 1):
                if time.time() - t0 > budget_s:
                    trace["outcome"], trace["reason"] = "budget_exhausted", "time budget"
                    return trace
                if trace["model_calls"] + 2 > max_model_calls:
                    trace["outcome"], trace["reason"] = "budget_exhausted", "model-call budget"
                    return trace

                step: dict = {"step": step_no, "t_ms": int((time.time() - t0) * 1000)}
                before_png = out_dir / f"step_{step_no}.before.png"
                before_bytes = await page.screenshot()
                before_png.write_bytes(before_bytes)
                state = await page_state(page)
                step["before"] = {"url": state["url"], "scroll_y": state["scroll_y"],
                                  "png": before_png.name, "sha": _sha(before_bytes),
                                  "activity_links": len(state["activity_links"]),
                                  "popup": bool(state["dismiss_selector"])}

                # 1. vision
                vision = await probe_vision.observe(before_png, vision_model, openrouter_key, 1200)
                trace["model_calls"] += 1
                cost = ((vision.get("usage") or {}).get("cost_details") or {}).get(
                    "upstream_inference_cost") or 0.0
                trace["vision_cost_usd"] += cost
                obs = vision.get("observations")
                step["vision"] = {"latency_ms": vision["latency_ms"], "cost_usd": cost,
                                  "parse_ok": vision["json_parse_ok"],
                                  "page_type": (obs or {}).get("page_type"),
                                  "wall": (obs or {}).get("login_or_access_wall"),
                                  "popup": (obs or {}).get("signin_popup_present"),
                                  "posts_seen": len((obs or {}).get("recent_posts") or []),
                                  "observations": obs}

                # goal check: posts visible or on a posts URL
                if step["vision"]["posts_seen"] or any(h in state["url"] for h in GOAL_URL_HINT):
                    step["decision"], step["note"] = "STOP", "goal reached"
                    trace["steps"].append(step)
                    trace["outcome"] = "reached"
                    trace["reason"] = "posts/activity visible"
                    return trace

                # 2. Jev
                actions = offered_actions(state, obs)

                def ask_jev(feedback: str | None = None) -> dict:
                    body = jev_body(state, obs, trace["steps"], actions, feedback)
                    jev = probe_jev.call_jev(jev_key, body)
                    trace["model_calls"] += 1
                    trace["jev_tokens"] += (jev.get("usage") or {}).get("input_tokens", 0)
                    return {k: jev.get(k) for k in
                            ("http_status", "latency_ms", "decision", "confidence",
                             "probabilities", "error_body")}

                step["jev"] = ask_jev()
                decision = step["jev"].get("decision")

                # A STOP that contradicts the evidence (no wall, nothing scrolled, no
                # posts) gets one re-ask with that feedback. A second STOP is accepted.
                scroll_max = state["scroll_max"]
                pct = int(100 * state["scroll_y"] / scroll_max) if scroll_max else 100
                contradicts = (decision == "STOP" and not (obs or {}).get("login_or_access_wall")
                               and step["vision"]["posts_seen"] == 0 and pct < 100)
                if contradicts and trace["model_calls"] < max_model_calls:
                    step["jev_first_answer"] = step["jev"]
                    step["jev"] = ask_jev(
                        f"Your previous answer STOP was rejected: no wall is shown, no posts "
                        f"are visible yet and the page is only {pct}% scrolled. Choose again."
                    )
                    decision = step["jev"].get("decision")
                if (step["jev"].get("http_status") or 0) >= 400 or step["jev"].get("error_body"):
                    step["decision"] = "STOP"
                    step["note"] = f"Jev HTTP {step['jev'].get('http_status')}"
                    trace["steps"].append(step)
                    trace["outcome"] = "error"
                    trace["error"] = f"{step['note']}: {step['jev'].get('error_body')}"[:300]
                    return trace
                if decision not in actions:
                    step["decision"] = "STOP"
                    step["note"] = f"invalid/unavailable choice {decision!r}"
                    trace["steps"].append(step)
                    trace["outcome"], trace["reason"] = "stopped_no_progress", step["note"]
                    return trace
                step["decision"] = decision
                if decision == "STOP":
                    trace["steps"].append(step)
                    trace["outcome"], trace["reason"] = "stopped_by_model", "Jev chose STOP"
                    return trace

                # 3. act
                try:
                    step["note"] = await execute(page, decision, state)
                except Exception as exc:
                    step["note"] = f"action failed: {type(exc).__name__}: {exc}"[:200]

                # 4. verify
                after_bytes = await page.screenshot()
                after_png = out_dir / f"step_{step_no}.after.png"
                after_png.write_bytes(after_bytes)
                after_url, after_scroll = page.url, await page.evaluate("window.scrollY")
                html = await page.content()
                if html_sink is not None:
                    html_sink["html"] = html
                outcome, signals = probe_browser.classify(200, after_url, await page.title(), html)
                step["after"] = {"url": after_url, "scroll_y": after_scroll, "png": after_png.name,
                                 "sha": _sha(after_bytes), "classify": outcome, "signals": signals}
                step["progress"] = (after_url != state["url"] or after_scroll != state["scroll_y"]
                                    or step["after"]["sha"] != step["before"]["sha"])
                step["step_ms"] = int((time.time() - t0) * 1000) - step["t_ms"]
                trace["steps"].append(step)

                if outcome != "ok":
                    trace["outcome"] = "stopped_wall"
                    trace["reason"] = f"after {decision}: {outcome} {signals}"
                    return trace
                no_progress = 0 if step["progress"] else no_progress + 1
                if no_progress >= 2:
                    trace["outcome"] = "stopped_no_progress"
                    at_end = after_scroll >= await page.evaluate(
                        "Math.max(0, document.documentElement.scrollHeight"
                        " - window.innerHeight) - 2"
                    )
                    trace["reason"] = ("end of page reached, no posts section visible" if at_end
                                       else "two steps without change")
                    return trace

            trace["outcome"], trace["reason"] = "budget_exhausted", "max steps"
            return trace
    except Exception as exc:
        trace["outcome"] = "error"
        trace["error"] = f"{type(exc).__name__}: {exc}"
        return trace
    finally:
        trace["total_ms"] = int((time.time() - t0) * 1000)
        (out_dir / "trace.json").write_text(json.dumps(trace, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("url")
    ap.add_argument("--out", default=str(REPO_ROOT / "data" / "visual_probe"))
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--vision-model", default=probe_vision.DEFAULT_MODEL)
    ap.add_argument("--max-steps", type=int, default=8)
    ap.add_argument("--budget-s", type=float, default=180)
    ap.add_argument("--max-model-calls", type=int, default=16)
    args = ap.parse_args()

    out_dir = Path(args.out) / datetime.now().strftime("loop_%Y%m%d_%H%M%S")
    trace = asyncio.run(run(
        args.url, out_dir, seed=args.seed, headless=not args.headed,
        vision_model=args.vision_model, max_steps=args.max_steps,
        budget_s=args.budget_s, max_model_calls=args.max_model_calls,
    ))
    keys = ("url", "outcome", "reason", "total_ms", "model_calls", "vision_cost_usd",
            "jev_tokens", "landing")
    summary = {k: trace.get(k) for k in keys}
    summary["steps"] = [
        {"step": s["step"], "decision": s.get("decision"), "note": s.get("note"),
         "confidence": (s.get("jev") or {}).get("confidence"),
         "progress": s.get("progress"), "posts_seen": (s.get("vision") or {}).get("posts_seen")}
        for s in trace.get("steps", [])
    ]
    print(json.dumps(summary, indent=2))
    print(f"\ntrace: {out_dir / 'trace.json'}")
    return {"reached": 0, "stopped_wall": 2, "stopped_by_model": 2, "stopped_no_progress": 2,
            "budget_exhausted": 3}.get(trace.get("outcome"), 1)


if __name__ == "__main__":
    sys.exit(main())
