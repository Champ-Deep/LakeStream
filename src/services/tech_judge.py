"""LLM judge for technology detections (v2.2).

Deliberately **not** an extractor. The regex catalog does all primary
detection; the judge only reviews what the catalog already produced and
answers one question per detection: given this evidence, is this technology
really in use on this site?

That split matters:
  - the catalog is deterministic, auditable, and cheap;
  - the judge sees only a short list of candidates plus their evidence
    snippets, never the raw page, so it cannot invent technologies that the
    catalog did not detect;
  - a judge failure (timeout, no API key, bad JSON) degrades to "keep the
    regex result", never to an empty or fabricated one.

Only lower-confidence detections are sent for review. A high-confidence
structural match (a `Server` header, a script URL) is not worth spending a
token on; a body-text match ("we love Shopify" in a blog post) is exactly the
outlier case the judge exists to catch.
"""

from __future__ import annotations

import json
from uuid import UUID

import structlog

from src.config.settings import get_settings

log = structlog.get_logger()

_SYSTEM = (
    "You audit website technology detections. For each candidate you are given "
    "the technology name, the category, and the exact evidence snippet that a "
    "regex matched on the page. Decide whether the site actually USES that "
    "technology, versus merely mentioning it (a customer logo, a blog post, a "
    "job posting, a comparison table, an integrations directory). "
    "Never add technologies that are not in the candidate list. "
    "Return ONLY JSON: {\"verdicts\":[{\"name\":\"...\",\"uses\":true|false,"
    "\"reason\":\"short\"}]}"
)


def _needs_review(det: dict) -> bool:
    """Only medium-confidence, body-derived detections are worth judging."""
    return det.get("confidence") != "high" and det.get("source") in ("html", "url")


async def judge_detections(
    detections: list[dict],
    *,
    url: str = "",
    org_id: UUID | None = None,
    force: bool = False,
) -> tuple[list[dict], list[dict]]:
    """Review low-confidence detections.

    `force` enables the judge for this call regardless of the global flag (used
    by the per-request `judge` override on the API). It is passed explicitly
    rather than mutating the shared settings singleton, which would race
    across concurrent requests.

    Returns (kept_detections, rejected_detections). On any failure the input is
    returned unchanged — the judge can only ever remove clear false positives,
    never block the pipeline.
    """
    settings = get_settings()
    if not (settings.enable_tech_judge or force) or not detections:
        return detections, []

    candidates = [d for d in detections if _needs_review(d)]
    if not candidates:
        return detections, []

    # Bound the cost: judge the most suspicious ones, keep the rest as-is.
    candidates = candidates[: settings.tech_judge_max_detections]

    from src.services.llm_extractor import LLMExtractor, get_openrouter_config

    try:
        await get_openrouter_config(org_id)
    except ValueError:
        # No API key configured — regex results stand.
        return detections, []

    payload = [
        {
            "name": d.get("name"),
            "category": d.get("category"),
            "evidence": (d.get("evidence") or "")[:200],
        }
        for d in candidates
    ]
    prompt = (
        f"Site: {url or 'unknown'}\n"
        f"Candidate detections to audit:\n{json.dumps(payload, indent=2)}\n\n"
        f"{_SYSTEM}"
    )

    try:
        result = await LLMExtractor(org_id=org_id).extract_freeform("", prompt)
    except Exception as e:
        log.warning("tech_judge_failed", url=url, error=str(e))
        return detections, []

    if not isinstance(result, dict) or result.get("_extraction_errors"):
        return detections, []

    verdicts = result.get("verdicts")
    if not isinstance(verdicts, list):
        return detections, []

    rejected_names = {
        str(v.get("name", "")).strip().lower(): str(v.get("reason", ""))[:120]
        for v in verdicts
        if isinstance(v, dict) and v.get("uses") is False and v.get("name")
    }
    if not rejected_names:
        return detections, []

    # Only ever reject something we actually sent for review — the judge must
    # not be able to remove a high-confidence structural detection.
    reviewable = {str(c["name"]).strip().lower() for c in candidates}

    kept: list[dict] = []
    rejected: list[dict] = []
    for det in detections:
        key = str(det.get("name", "")).strip().lower()
        if key in rejected_names and key in reviewable:
            rejected.append({**det, "rejected_reason": rejected_names[key]})
        else:
            kept.append(det)

    if rejected:
        log.info(
            "tech_judge_rejected",
            url=url,
            rejected=[r.get("name") for r in rejected],
            reviewed=len(candidates),
        )
    return kept, rejected
