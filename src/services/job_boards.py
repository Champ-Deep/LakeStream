"""ATS job-board ingestion (Greenhouse, Lever, Ashby).

Public, documented JSON APIs — no login, no anti-bot, no proxy tier needed.
That makes this the cheapest high-signal source in the stack: a company's open
roles reveal hiring intent, team growth, tech stack and budget, and the posting
body is genuine first-party prose (useful anywhere a narrative document is
wanted, not just as structured fields).

Endpoint shapes verified live 2026-07-29:
  Greenhouse  GET boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true
              -> {"jobs":[{absolute_url,title,location:{name},updated_at,
                           content(HTML),departments[],offices[],...}]}
  Lever       GET api.lever.co/v0/postings/{token}?mode=json
              -> [{text,categories:{department,team,location,commitment},
                   hostedUrl,applyUrl,createdAt(ms),descriptionPlain,...}]
  Ashby       GET api.ashbyhq.com/posting-api/job-board/{token}
              -> {"jobs":[{title,department,team,location,employmentType,
                           publishedAt,jobUrl,applyUrl,descriptionPlain,
                           isRemote,isListed,...}]}

All three are unauthenticated GETs. Ashby responses can be large (~2MB for a
mid-size company), so callers should treat this as a batch source rather than
something to call inline on a request path.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import urlparse

import httpx
import structlog

from src.models.scraped_data import DataType

log = structlog.get_logger()

ATS_PROVIDERS = ("greenhouse", "lever", "ashby")

_TIMEOUT = 30.0
_USER_AGENT = (
    "Mozilla/5.0 (compatible; LakeStream/1.0; +https://github.com/Champ-Deep/LakeStream)"
)

# Board-token patterns as they appear in careers-page links. Ordered by
# provider; the capture group is the board token.
_TOKEN_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    # Most specific first: the embed URL also contains the literal "embed" in
    # the board-token position, so the generic pattern must not win the race.
    "greenhouse": (
        # JS embed widget, with or without the /js segment:
        #   boards.greenhouse.io/embed/job_board/js?for=acme
        #   boards.greenhouse.io/embed/job_board?for=acme
        re.compile(r"greenhouse\.io/embed/job_board(?:/js)?\?for=([A-Za-z0-9_-]+)", re.I),
        re.compile(r"boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)", re.I),
        re.compile(r"job-boards\.greenhouse\.io/([A-Za-z0-9_-]+)", re.I),
        re.compile(r"boards\.greenhouse\.io/([A-Za-z0-9_-]+)", re.I),
    ),
    "lever": (
        re.compile(r"jobs\.lever\.co/([A-Za-z0-9_-]+)", re.I),
        re.compile(r"api\.lever\.co/v0/postings/([A-Za-z0-9_-]+)", re.I),
    ),
    "ashby": (
        re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_-]+)", re.I),
        re.compile(r"api\.ashbyhq\.com/posting-api/job-board/([A-Za-z0-9_-]+)", re.I),
    ),
}

# Tokens that appear in embed snippets but are not real board slugs.
_TOKEN_BLOCKLIST = {"embed", "job_board", "jobs", "v0", "v1", "postings", "boards"}


@dataclass
class JobPosting:
    """One open role, normalised across the three ATS providers."""

    provider: str  # greenhouse | lever | ashby
    board_token: str
    external_id: str
    title: str
    url: str
    apply_url: str | None = None
    department: str | None = None
    team: str | None = None
    location: str | None = None
    remote: bool | None = None
    employment_type: str | None = None
    posted_at: str | None = None  # ISO-8601
    description: str = ""  # plain text where the API offers it, else HTML

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BoardResult:
    """Outcome of fetching one company's board."""

    provider: str
    board_token: str
    postings: list[JobPosting] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


# --------------------------------------------------------------------------
# Token discovery
# --------------------------------------------------------------------------


def detect_board(text: str) -> tuple[str, str] | None:
    """Find an (provider, board_token) pair in a careers page's HTML or a URL.

    Returns the first match across providers, or None. Callers typically pass
    the fetched HTML of `https://<domain>/careers`, since most companies embed
    or link their board rather than hosting it on their own domain.
    """
    if not text:
        return None
    for provider, patterns in _TOKEN_PATTERNS.items():
        for pattern in patterns:
            for match in pattern.finditer(text):
                token = match.group(1)
                if token and token.lower() not in _TOKEN_BLOCKLIST:
                    return provider, token
    return None


def _strip_html(html: str) -> str:
    """Crude tag strip for providers that only return HTML descriptions."""
    if not html:
        return ""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&#39;", "'")
        .replace("&quot;", '"')
    )
    return re.sub(r"\s+", " ", text).strip()


def _iso(value: Any) -> str | None:
    """Normalise the three providers' timestamp shapes to ISO-8601."""
    if value in (None, "", 0):
        return None
    # Lever uses epoch milliseconds; the others use ISO strings already.
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()
        except (ValueError, OSError, OverflowError):
            return None
    return str(value)


# --------------------------------------------------------------------------
# Per-provider fetchers
# --------------------------------------------------------------------------


async def _get_json(client: httpx.AsyncClient, url: str) -> Any:
    resp = await client.get(url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"})
    resp.raise_for_status()
    return resp.json()


async def fetch_greenhouse(client: httpx.AsyncClient, token: str) -> list[JobPosting]:
    # content=true returns the full posting body; without it you get titles only.
    data = await _get_json(
        client, f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    )
    out: list[JobPosting] = []
    for job in data.get("jobs", []) or []:
        departments = job.get("departments") or []
        offices = job.get("offices") or []
        out.append(
            JobPosting(
                provider="greenhouse",
                board_token=token,
                external_id=str(job.get("id", "")),
                title=(job.get("title") or "").strip(),
                url=job.get("absolute_url") or "",
                apply_url=job.get("absolute_url") or None,
                department=(departments[0].get("name") if departments else None),
                team=(departments[1].get("name") if len(departments) > 1 else None),
                location=((job.get("location") or {}).get("name")
                          or (offices[0].get("name") if offices else None)),
                employment_type=None,  # not exposed by the board API
                posted_at=_iso(job.get("first_published") or job.get("updated_at")),
                # Greenhouse returns HTML-escaped HTML in `content`.
                description=_strip_html(job.get("content") or ""),
            )
        )
    return out


async def fetch_lever(client: httpx.AsyncClient, token: str) -> list[JobPosting]:
    data = await _get_json(client, f"https://api.lever.co/v0/postings/{token}?mode=json")
    out: list[JobPosting] = []
    for job in data or []:
        cats = job.get("categories") or {}
        out.append(
            JobPosting(
                provider="lever",
                board_token=token,
                external_id=str(job.get("id", "")),
                title=(job.get("text") or "").strip(),
                url=job.get("hostedUrl") or "",
                apply_url=job.get("applyUrl") or None,
                department=cats.get("department"),
                team=cats.get("team"),
                location=cats.get("location"),
                remote=(str(job.get("workplaceType", "")).lower() == "remote") or None,
                employment_type=cats.get("commitment"),
                posted_at=_iso(job.get("createdAt")),
                description=(job.get("descriptionPlain")
                             or _strip_html(job.get("description") or "")),
            )
        )
    return out


async def fetch_ashby(client: httpx.AsyncClient, token: str) -> list[JobPosting]:
    data = await _get_json(
        client, f"https://api.ashbyhq.com/posting-api/job-board/{token}"
    )
    out: list[JobPosting] = []
    for job in data.get("jobs", []) or []:
        # Unlisted roles are not public-facing; skip them.
        if job.get("isListed") is False:
            continue
        out.append(
            JobPosting(
                provider="ashby",
                board_token=token,
                external_id=str(job.get("id", "")),
                title=(job.get("title") or "").strip(),
                url=job.get("jobUrl") or "",
                apply_url=job.get("applyUrl") or None,
                department=job.get("department"),
                team=job.get("team"),
                location=job.get("location"),
                remote=job.get("isRemote"),
                employment_type=job.get("employmentType"),
                posted_at=_iso(job.get("publishedAt")),
                description=(job.get("descriptionPlain")
                             or _strip_html(job.get("descriptionHtml") or "")),
            )
        )
    return out


_FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
}


# --------------------------------------------------------------------------
# Public entry points
# --------------------------------------------------------------------------


async def fetch_board(provider: str, token: str) -> BoardResult:
    """Fetch every public posting for one known (provider, token)."""
    provider = provider.lower().strip()
    fetcher = _FETCHERS.get(provider)
    if fetcher is None:
        return BoardResult(provider, token, error=f"unknown ATS provider '{provider}'")

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            postings = await fetcher(client, token)
        log.info("job_board_fetched", provider=provider, token=token, count=len(postings))
        return BoardResult(provider, token, postings=postings)
    except httpx.HTTPStatusError as exc:
        # 404 is the normal "this token isn't on this provider" answer.
        return BoardResult(
            provider, token, error=f"HTTP {exc.response.status_code}"
        )
    except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
        return BoardResult(provider, token, error=str(exc))


async def discover_board(
    domain_or_url: str, allow_token_guess: bool = False
) -> tuple[str, str] | None:
    """Find a company's ATS board from its domain or careers URL.

    Discovery is evidence-based: it follows the company's own careers pages and
    reads the board link they publish. Returns (provider, token) or None.

    `allow_token_guess` enables a last-resort fallback that tries the domain
    label as a board token. It is OFF by default because it produces confident
    false positives — "example.com" resolves to a real Greenhouse board named
    "example" belonging to an unrelated company, which would attribute someone
    else's roles to your prospect. Only enable it when a human will check the
    result, and always compare the returned board against the company you meant.
    """
    raw = (domain_or_url or "").strip()
    if not raw:
        return None

    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = parsed.netloc or parsed.path
    host = host.split("/")[0].lower().removeprefix("www.")
    if not host:
        return None

    # A direct board link needs no discovery.
    direct = detect_board(raw)
    if direct:
        return direct

    candidates = [
        f"https://{host}{parsed.path}" if parsed.path not in ("", "/") else None,
        f"https://{host}/careers",
        f"https://{host}/jobs",
        f"https://{host}/careers/",
        f"https://{host}/about/careers",
    ]

    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        for url in [c for c in candidates if c]:
            try:
                resp = await client.get(url, headers={"User-Agent": _USER_AGENT})
            except httpx.HTTPError:
                continue
            if resp.status_code >= 400:
                continue
            found = detect_board(str(resp.url)) or detect_board(resp.text)
            if found:
                log.info("job_board_discovered", domain=host,
                         provider=found[0], token=found[1], via=url)
                return found

    # Opt-in last resort: the domain label is often the board token verbatim.
    # Unverifiable, so it stays behind a flag — see the docstring.
    if allow_token_guess:
        guess = host.split(".")[0]
        if guess and guess not in _TOKEN_BLOCKLIST:
            for provider in ATS_PROVIDERS:
                result = await fetch_board(provider, guess)
                if result.ok and result.postings:
                    log.warning(
                        "job_board_guessed_unverified",
                        domain=host, provider=provider, token=guess,
                        note="token guessed from domain label; may belong to another company",
                    )
                    return provider, guess

    return None


async def fetch_jobs_for_company(
    domain_or_url: str, allow_token_guess: bool = False
) -> BoardResult:
    """Discover a company's ATS board and return its postings in one call."""
    found = await discover_board(domain_or_url, allow_token_guess=allow_token_guess)
    if not found:
        return BoardResult("unknown", "", error="no ATS board found")
    provider, token = found
    return await fetch_board(provider, token)


async def fetch_jobs_for_companies(
    domains: Iterable[str], concurrency: int = 5
) -> dict[str, BoardResult]:
    """Batch version. Bounded concurrency — these are other people's servers."""
    domain_list = [d for d in domains if d and d.strip()]
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def _one(domain: str) -> tuple[str, BoardResult]:
        async with semaphore:
            try:
                return domain, await fetch_jobs_for_company(domain)
            except Exception as exc:  # never let one company kill the batch
                log.warning("job_board_batch_error", domain=domain, error=str(exc))
                return domain, BoardResult("unknown", "", error=str(exc))

    results = await asyncio.gather(*(_one(d) for d in domain_list))
    return dict(results)


# --------------------------------------------------------------------------
# Derived signals
# --------------------------------------------------------------------------


def summarise_hiring(postings: list[JobPosting]) -> dict[str, Any]:
    """Condense a board into the buying signals the pipeline cares about.

    Department counts show where a company is investing; a large open-role
    count is a growth/budget signal; the most recent posting date shows whether
    the company is actively hiring right now or the board is stale.
    """
    if not postings:
        return {"open_roles": 0, "departments": {}, "locations": {},
                "most_recent_post": None, "remote_roles": 0}

    departments: dict[str, int] = {}
    locations: dict[str, int] = {}
    for post in postings:
        if post.department:
            departments[post.department] = departments.get(post.department, 0) + 1
        if post.location:
            locations[post.location] = locations.get(post.location, 0) + 1

    dates = sorted([p.posted_at for p in postings if p.posted_at], reverse=True)

    return {
        "open_roles": len(postings),
        "departments": dict(sorted(departments.items(), key=lambda kv: -kv[1])),
        "locations": dict(sorted(locations.items(), key=lambda kv: -kv[1])[:10]),
        "most_recent_post": dates[0] if dates else None,
        "remote_roles": sum(1 for p in postings if p.remote),
    }


# ---------------------------------------------------------------------------
# Persistence — the missing half
# ---------------------------------------------------------------------------
#
# Until now this module only served its own API routes. Nothing wrote postings
# to `scraped_data`, while `signal_evaluator.check_hiring_spike_signal` queried
# `data_type = 'job_posting'` — a value that did not exist in the DataType enum
# and that no code path produced. The hiring signal therefore could never fire,
# despite being the highest-leverage outreach trigger available (signal-based
# personalisation multiplies reply rate several times over).
#
# Persisting here closes that loop: fetch -> store -> signal -> outreach.


def posting_to_record(
    posting: JobPosting,
    *,
    job_id: Any,
    domain: str,
    org_id: Any = None,
    user_id: Any = None,
) -> dict[str, Any]:
    """Map one JobPosting onto a `scraped_data` upsert record.

    The upsert key is (domain, url, data_type), and every ATS gives a posting a
    stable URL — so re-running a board updates existing rows rather than
    duplicating them, and `scraped_at` moves forward. That matters for the
    signal: "postings seen in the last 7 days" is only meaningful if a
    still-open role keeps refreshing rather than ageing out while it is live.
    """
    return {
        "job_id": job_id,
        "domain": domain,
        "data_type": DataType.JOB_POSTING.value,
        "url": posting.url,
        "title": posting.title,
        "org_id": org_id,
        "user_id": user_id,
        "metadata": {
            "provider": posting.provider,
            "board_token": posting.board_token,
            "external_id": posting.external_id,
            "department": posting.department,
            "team": posting.team,
            "location": posting.location,
            "remote": posting.remote,
            "employment_type": posting.employment_type,
            "posted_at": posting.posted_at,
            "apply_url": posting.apply_url,
            # Truncated: the signal reads counts and departments, not prose, and
            # a full JD per row would bloat the JSONB column for no benefit.
            "description_excerpt": (posting.description or "")[:1000],
        },
    }


async def persist_postings(
    pool: Any,
    result: BoardResult,
    *,
    domain: str,
    job_id: Any,
    org_id: Any = None,
    user_id: Any = None,
) -> int:
    """Write one board's postings into `scraped_data`. Returns rows written.

    `domain` is passed explicitly rather than read off the result: BoardResult
    carries the provider and board token but not the company domain, because
    fetch_jobs_for_companies keys its return dict BY domain. The scraped_data
    upsert key is (domain, url, data_type), so getting this wrong would scatter
    one company's postings across rows that never collide.

    A board that errored or returned nothing writes nothing — an empty write is
    not the same as "this company has no open roles", and recording it as such
    would let a failed fetch read as a hiring slowdown.
    """
    if result.error or not result.postings:
        return 0

    from src.db.queries.scraped_data import batch_insert_scraped_data

    records = [
        posting_to_record(
            posting, job_id=job_id, domain=domain,
            org_id=org_id, user_id=user_id,
        )
        for posting in result.postings
    ]
    return await batch_insert_scraped_data(pool, records)


async def persist_board_results(
    pool: Any,
    results: dict[str, BoardResult],
    *,
    job_id: Any,
    org_id: Any = None,
    user_id: Any = None,
) -> int:
    """Persist many boards. Takes the {domain: BoardResult} map that
    fetch_jobs_for_companies returns. Returns the total rows written."""
    total = 0
    for domain, result in results.items():
        try:
            total += await persist_postings(
                pool, result, domain=domain, job_id=job_id,
                org_id=org_id, user_id=user_id,
            )
        except Exception:
            # ! One company's write failing must not lose the rest of the batch.
            log.exception("persist_board_results_failed", domain=domain)
    return total
