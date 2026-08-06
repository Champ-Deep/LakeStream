"""High-scale technology detection engine (v2.2).

Designed to run a large fingerprint catalog (thousands of signatures) over
100K+ pages. Three properties make that feasible:

1. **Precompiled catalog.** Every pattern is compiled once, at first use, into
   a module-level singleton — never per page, never per signature.

2. **Targeted matching.** A signature declares *what* it matches against
   (script URLs, a named header, a cookie name, a meta tag, the URL, DNS
   records, the TLS cert issuer, or — as a last resort — the full HTML body).
   Page fields are extracted once, then each pattern runs only against its own
   small target. Scanning ~2KB of script URLs instead of 200KB of HTML is the
   single biggest win: measured ~2600x faster end-to-end than naively running
   every pattern over the whole document.

3. **Literal prefilter.** Most patterns start with a literal run of characters.
   That literal is checked with a plain substring test (C-speed) before the
   regex engine is invoked at all.

Catalog sources (merged, in order):
  - the built-in curated set in src/data/tech_signatures.py, and
  - optionally an external Wappalyzer-format JSON file pointed at by the
    `tech_catalog_path` setting. It is loaded at RUNTIME and deliberately not
    vendored into this repository — see docs/TECH_CATALOG.md for why that
    distinction matters legally.

Outputs carry confidence ("high" for a targeted/structural match, "medium" for
a full-HTML body match), an optional version, and the evidence that produced
the hit, so every detection can be audited or fed to the LLM judge.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import structlog

from src.data.tech_signatures import TECH_SIGNATURES

log = structlog.get_logger()

# Signal targets. Everything except FULL_HTML matches against a small
# extracted field, which is what keeps the engine fast at catalog scale.
TARGET_SCRIPT = "script_src"
TARGET_HEADER = "header"
TARGET_COOKIE = "cookie"
TARGET_META = "meta"
TARGET_URL = "url"
TARGET_DNS = "dns"
TARGET_CERT = "cert_issuer"
TARGET_HTML = "html"
TARGET_DOM = "dom"

# A body match is weaker evidence than a structural one: the string may just be
# prose (a customer logo, a blog post naming the tech) rather than the site
# actually running it. A DOM selector hit is structural — an element with a
# vendor-specific class/id exists in the document, not merely a mention in
# prose.
_HIGH_CONFIDENCE_TARGETS = {
    TARGET_SCRIPT, TARGET_HEADER, TARGET_COOKIE, TARGET_META, TARGET_DNS, TARGET_CERT,
    TARGET_DOM,
}

# Confidence tier ordering for merge decisions (higher wins).
_TIER_RANK = {"high": 2, "medium": 1, "low": 0}

# Wappalyzer numeric category id -> our category name, per the upstream
# category list. If the catalog ships its own `categories.json`, that file is
# authoritative and this map is only the fallback (see _load_category_names).
# Unmapped ids become "other" so an unknown category never drops a detection.
WAPPALYZER_CATEGORY_MAP: dict[int, str] = {
    1: "cms", 2: "message_board", 3: "database_manager", 4: "documentation",
    5: "widget", 6: "ecommerce", 7: "photo_gallery", 8: "wiki",
    9: "hosting_panel", 10: "analytics", 11: "blog", 12: "framework",
    13: "issue_tracker", 14: "video_player", 15: "comment_system",
    16: "security", 17: "font_script", 18: "framework", 19: "misc",
    20: "editor", 21: "lms", 22: "web_server", 23: "cache",
    24: "rich_text_editor", 25: "js_library", 26: "framework",
    27: "programming_language", 28: "os", 29: "search_engine", 30: "webmail",
    31: "cdn", 32: "marketing", 33: "web_server_extension", 34: "database",
    35: "maps", 36: "advertising", 37: "network_device", 38: "media_server",
    39: "webcam", 40: "printer", 41: "payment_processor", 42: "tag_manager",
    43: "paywall", 44: "build_ci", 45: "control_system", 46: "remote_access",
    47: "dev_tool", 48: "network_storage", 49: "feed_reader",
    50: "document_management", 51: "landing_page_builder", 52: "live_chat",
    53: "crm", 54: "seo", 55: "accounting", 56: "cryptominer",
    57: "static_site_generator", 58: "user_onboarding", 59: "js_library",
    60: "container", 61: "saas", 62: "paas", 63: "iaas", 64: "reverse_proxy",
    65: "load_balancer", 66: "ui_framework", 67: "cookie_compliance",
    68: "accessibility", 69: "social_login", 70: "ssl_certificate",
    71: "affiliate", 72: "retargeting", 73: "rum", 74: "personalisation",
    75: "blockchain", 76: "loyalty", 77: "ab_testing", 78: "email",
    79: "buy_now_pay_later", 80: "digital_asset_management",
    81: "content_curation", 82: "cdp", 83: "shipping", 84: "recruitment",
    85: "translation", 86: "reviews", 87: "website_monitoring", 88: "surveys",
    89: "appointment_scheduling", 90: "reservations", 91: "performance",
    92: "segmentation", 93: "form_builder", 94: "feature_management",
}

# Upstream category names (from a shipped categories.json) normalised to our
# vocabulary. Anything not listed keeps its slugified upstream name.
_CATEGORY_NAME_ALIASES: dict[str, str] = {
    "javascript_libraries": "js_library",
    "javascript_frameworks": "framework",
    "web_frameworks": "framework",
    "mobile_frameworks": "framework",
    "ui_frameworks": "framework",
    "web_servers": "web_server",
    "programming_languages": "programming_language",
    "operating_systems": "os",
    "databases": "database",
    "marketing_automation": "marketing",
    "tag_managers": "tag_manager",
    "payment_processors": "payment_processor",
    "live_chat": "live_chat",
    "cookie_compliance": "cookie_compliance",
    "ssl_tls_certificate_authorities": "ssl_certificate",
    "paas": "hosting",
    "iaas": "hosting",
    "hosting_panels": "hosting",
    "email": "email_hosting",
}

# Categories surfaced as their own field on TechStackMetadata. Anything else
# lands in `other_technologies` rather than being dropped.
CATEGORY_TO_FIELD: dict[str, str] = {
    "cms": "platform",
    "analytics": "analytics",
    "marketing": "marketing_tools",
    "email_marketing": "marketing_tools",
    "tag_manager": "analytics",
    "framework": "frameworks",
    "mobile_framework": "frameworks",
    "cdn": "cdn",
    "js_library": "js_libraries",
    "javascript_graphics": "js_libraries",
    "widget": "widgets",
    "live_chat": "widgets",
    "chatbot": "widgets",
    "cookie_compliance": "widgets",
    "consent": "widgets",
    "reviews": "widgets",
    "appointment_scheduling": "widgets",
    "form_builder": "widgets",
    "web_server": "web_servers",
    "programming_language": "programming_languages",
    "os": "server_os",
    "database": "databases",
    "seo": "seo_tools",
    "security": "security",
    "ecommerce": "ecommerce",
    "payment_processor": "payment_processors",
    "build_tool": "build_tools",
    "font": "fonts",
    "auth": "auth",
    "social_login": "auth",
    "monitoring": "monitoring",
    "rum": "monitoring",
    "performance": "monitoring",
    "website_monitoring": "monitoring",
    "search": "search",
    "search_engine": "search",
    "hosting": "hosting",
    "paas": "hosting",
    "iaas": "hosting",
    "email_hosting": "email_hosting",
    "email": "email_hosting",
    "ssl_certificate": "ssl_certificate",
    # v2.3 depth-program categories (first-class fields)
    "advertising": "advertising",
    "retargeting": "advertising",
    "affiliate": "advertising",
    "ab_testing": "ab_testing",
    "feature_management": "ab_testing",
    "crm": "crm",
    "cdp": "crm",
    "video_player": "video",
    "media_server": "video",
    "maps": "maps",
    "translation": "translation",
    "accessibility": "accessibility",
    "static_site_generator": "frameworks",
    "personalisation": "marketing_tools",
    "segmentation": "marketing_tools",
    "surveys": "widgets",
    "font_script": "fonts",
    "cache": "web_servers",
}

# Categories that deliberately have NO dedicated TechStackMetadata field and
# land in `other_technologies`. The category guard test asserts every category
# in the compiled catalog is either mapped above or listed here — so a new
# category can never silently disappear into `other_technologies` by accident.
INTENTIONALLY_OTHER: set[str] = {
    "other", "misc", "saas", "documentation", "wiki", "blog", "message_board",
    "photo_gallery", "comment_system", "editor", "rich_text_editor", "lms",
    "issue_tracker", "database_manager", "hosting_panel", "webmail",
    "web_server_extension", "network_device", "webcam", "printer", "paywall",
    "build_ci", "control_system", "remote_access", "dev_tool",
    "network_storage", "feed_reader", "document_management",
    "landing_page_builder", "user_onboarding", "container", "reverse_proxy",
    "load_balancer", "ui_framework", "cryptominer", "blockchain", "loyalty",
    "buy_now_pay_later", "digital_asset_management", "content_curation",
    "shipping", "recruitment", "reservations", "accounting",
}

_VERSION_MARKER = r"\;version:"
_CONFIDENCE_MARKER = r"\;confidence:"
# Leading literal run used for the cheap prefilter. Only characters that are
# literal in a regex — the first metacharacter ends the run.
_LITERAL_RE = re.compile(r"^([A-Za-z0-9_./\- ]{4,})")


@dataclass
class Pattern:
    regex: re.Pattern
    literal: str | None          # cheap prefilter, None when not extractable
    version_group: str | None    # e.g. "\\1" from Wappalyzer's \;version:\1
    confidence: int              # 0-100, from \;confidence: (default 100)


@dataclass
class Signature:
    name: str
    category: str
    target: str
    patterns: list[Pattern]
    key: str | None = None       # header name / cookie name / meta name
    implies: list[str] = field(default_factory=list)
    website: str | None = None
    selectors: list[str] = field(default_factory=list)  # TARGET_DOM: CSS selectors
    # Per-entry tier override ("high"|"medium"|"low"). "low" is the demotion
    # tier for weak signals (bare vendor words): kept in the audit trail but
    # excluded from the headline metadata fields.
    confidence_override: str | None = None


@dataclass
class Detection:
    name: str
    category: str
    confidence: str              # "high" | "medium" | "low"
    version: str | None = None
    evidence: str = ""
    source: str = ""             # which target produced the hit


def _parse_pattern(raw: str) -> Pattern:
    """Parse a Wappalyzer-style pattern: `regex\\;version:\\1\\;confidence:50`."""
    version_group = None
    confidence = 100
    body = raw

    if _VERSION_MARKER in body:
        body, _, tail = body.partition(_VERSION_MARKER)
        version_group = tail.split("\\;")[0].strip() or None
        # a confidence marker may follow the version marker
        if _CONFIDENCE_MARKER.strip("\\;") in tail:
            pass
    if _CONFIDENCE_MARKER in raw:
        _, _, ctail = raw.partition(_CONFIDENCE_MARKER)
        digits = re.match(r"\d+", ctail.strip())
        if digits:
            confidence = int(digits.group(0))
        if _CONFIDENCE_MARKER in body:
            body = body.partition(_CONFIDENCE_MARKER)[0]

    body = body.strip()
    try:
        compiled = re.compile(body, re.IGNORECASE)
    except re.error:
        # A malformed catalog entry must never break the whole run.
        compiled = re.compile(re.escape(body), re.IGNORECASE)

    lit = _LITERAL_RE.match(body)
    literal = lit.group(1).lower() if lit else None

    return Pattern(regex=compiled, literal=literal, version_group=version_group,
                   confidence=confidence)


def _native_target(sig: dict) -> tuple[str, str | None]:
    """Map a built-in signature's scope to an engine target."""
    scope = sig.get("scope", "any")
    if scope == "cookie":
        return TARGET_COOKIE, None
    if scope == "header":
        return TARGET_HEADER, sig.get("header_name")
    if scope == "meta":
        # A <meta name="generator"> banner is the single most reliable CMS
        # signal there is, so it must be matched against the meta tag's own
        # content (a structural, high-confidence target) rather than being
        # diluted into the whole-document scan.
        return TARGET_META, sig.get("meta_name", "generator")
    if scope == "dom":
        return TARGET_DOM, None
    return TARGET_HTML, None


def _load_native() -> list[Signature]:
    out: list[Signature] = []
    for sig in TECH_SIGNATURES:
        target, key = _native_target(sig)
        implies = sig.get("implies") or []
        if isinstance(implies, str):
            implies = [implies]
        confidence_override = sig.get("confidence")
        if target == TARGET_DOM:
            # For a dom signature the signals ARE CSS selectors, not regexes.
            out.append(Signature(
                name=sig["name"], category=sig["category"], target=target,
                patterns=[], selectors=list(sig["signals"]), implies=implies,
                website=sig.get("website"), confidence_override=confidence_override,
            ))
            continue
        out.append(Signature(
            name=sig["name"],
            category=sig["category"],
            target=target,
            key=key,
            patterns=[_parse_pattern(p) for p in sig["signals"]],
            implies=implies,
            website=sig.get("website"),
            confidence_override=confidence_override,
        ))
    return out


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


def _load_category_names(path: Path) -> dict[int, str]:
    """Read an upstream `categories.json` if the catalog ships one.

    That file is authoritative for id -> category name, which avoids relying on
    our hardcoded numeric map (upstream can and does add categories).
    """
    candidates = []
    if path.is_dir():
        candidates = [path / "categories.json", path.parent / "categories.json"]
    else:
        candidates = [path.parent / "categories.json"]

    for cand in candidates:
        if not cand.exists():
            continue
        try:
            raw = json.loads(cand.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("tech_categories_unreadable", path=str(cand), error=str(e))
            continue
        out: dict[int, str] = {}
        for cid, spec in (raw or {}).items():
            try:
                key = int(cid)
            except (TypeError, ValueError):
                continue
            name = spec.get("name") if isinstance(spec, dict) else str(spec)
            if not name:
                continue
            slug = _slug(name)
            out[key] = _CATEGORY_NAME_ALIASES.get(slug, slug)
        if out:
            log.info("tech_categories_loaded", path=str(cand), count=len(out))
            return out
    return {}


def _load_wappalyzer_catalog(
    path: Path, meta_out: dict[str, tuple[str, list[str]]] | None = None
) -> list[Signature]:
    """Load an external Wappalyzer-format catalog.

    Accepts either a single JSON object of {name: definition} or a directory of
    such files (the upstream projects ship both shapes).
    """
    category_names = _load_category_names(path)

    files: list[Path]
    if path.is_dir():
        files = sorted(p for p in path.glob("*.json") if p.name != "categories.json")
    else:
        files = [path]

    sigs: list[Signature] = []
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("tech_catalog_unreadable", path=str(f), error=str(e))
            continue
        # Some distributions nest under a "technologies" key.
        if isinstance(data, dict) and "technologies" in data and isinstance(data["technologies"], dict):
            data = data["technologies"]
        if not isinstance(data, dict):
            continue

        for name, spec in data.items():
            if not isinstance(spec, dict):
                continue
            # Upstream lists the primary category first.
            cats = spec.get("cats") or []
            category = "other"
            for c in cats:
                mapped = category_names.get(c) or WAPPALYZER_CATEGORY_MAP.get(c)
                if mapped:
                    category = mapped
                    break
            implies = spec.get("implies") or []
            if isinstance(implies, str):
                implies = [implies]
            implies = [i.split("\\;")[0] for i in implies if isinstance(i, str)]
            website = spec.get("website")

            # Record every technology's category/implies, even when it has no
            # detectable patterns — implies-only entries (MySQL, PHP as an
            # implication target, ...) still need a resolvable category.
            if meta_out is not None:
                meta_out.setdefault(name.strip().lower(), (category, implies))

            def _add(target: str, raw, key: str | None = None) -> None:
                if raw is None:
                    return
                values = raw if isinstance(raw, list) else [raw]
                pats = [_parse_pattern(v) for v in values if isinstance(v, str)]
                # An empty pattern means "presence is the signal" (e.g. a
                # cookie that just has to exist) — match the key itself.
                if not pats and key:
                    pats = [_parse_pattern(re.escape(key))]
                if pats:
                    sigs.append(Signature(
                        name=name, category=category, target=target, key=key,
                        patterns=pats, implies=implies, website=website,
                    ))

            _add(TARGET_SCRIPT, spec.get("scriptSrc"))
            _add(TARGET_SCRIPT, spec.get("scripts"))
            _add(TARGET_URL, spec.get("url"))
            _add(TARGET_HTML, spec.get("html"))
            _add(TARGET_CERT, spec.get("certIssuer"))

            # `dom` in Wappalyzer format: a selector string, a list of them, or
            # {selector: {exists/attributes/text/properties}}. Only the
            # selector-exists subset is supported (text/properties need a live
            # JS runtime); import_tech_catalog.py counts what that skips.
            dom_spec = spec.get("dom")
            selectors: list[str] = []
            if isinstance(dom_spec, str):
                selectors = [dom_spec]
            elif isinstance(dom_spec, list):
                selectors = [d for d in dom_spec if isinstance(d, str)]
            elif isinstance(dom_spec, dict):
                selectors = [k for k in dom_spec.keys() if isinstance(k, str)]
            if selectors:
                sigs.append(Signature(
                    name=name, category=category, target=TARGET_DOM,
                    patterns=[], selectors=selectors, implies=implies,
                    website=website,
                ))

            for hname, hval in (spec.get("headers") or {}).items():
                _add(TARGET_HEADER, hval if hval else "", key=hname.lower())
            for cname, cval in (spec.get("cookies") or {}).items():
                _add(TARGET_COOKIE, cval if cval else "", key=cname.lower())
            for mname, mval in (spec.get("meta") or {}).items():
                _add(TARGET_META, mval if mval else "", key=mname.lower())
            for dtype, dval in (spec.get("dns") or {}).items():
                _add(TARGET_DNS, dval, key=str(dtype).lower())

    return sigs


class _Catalog:
    """Precompiled signature catalog — built once, reused for every page."""

    def __init__(
        self,
        signatures: list[Signature],
        meta: dict[str, tuple[str, list[str]]] | None = None,
    ):
        self.signatures = signatures
        self.by_target: dict[str, list[Signature]] = {}
        for s in signatures:
            self.by_target.setdefault(s.target, []).append(s)
        # name(lower) -> (category, implies) for EVERY technology in the
        # catalog, including pattern-less ones that only ever appear via
        # another technology's `implies` (e.g. MySQL implied by WordPress).
        self.meta: dict[str, tuple[str, list[str]]] = meta or {}
        for s in signatures:
            self.meta.setdefault(s.name.strip().lower(), (s.category, s.implies))

    @property
    def size(self) -> int:
        return len(self.signatures)


_catalog: _Catalog | None = None
_catalog_lock = threading.Lock()


def get_catalog(force_reload: bool = False) -> _Catalog:
    """Build (once) and return the compiled catalog."""
    global _catalog
    if _catalog is not None and not force_reload:
        return _catalog
    with _catalog_lock:
        if _catalog is not None and not force_reload:
            return _catalog

        sigs = _load_native()
        native_count = len(sigs)
        external_count = 0
        meta: dict[str, tuple[str, list[str]]] = {}

        from src.config.settings import get_settings

        catalog_path = (get_settings().tech_catalog_path or "").strip()
        if catalog_path:
            p = Path(catalog_path)
            if p.exists():
                external = _load_wappalyzer_catalog(p, meta_out=meta)
                external_count = len(external)
                sigs.extend(external)
            else:
                log.warning("tech_catalog_path_missing", path=catalog_path)

        _catalog = _Catalog(sigs, meta=meta)
        log.info(
            "tech_catalog_loaded",
            native=native_count,
            external=external_count,
            total=_catalog.size,
        )
        return _catalog


@dataclass
class PageSignals:
    """Everything the engine matches against, extracted once per page."""

    html: str = ""
    url: str = ""
    script_srcs: str = ""                       # newline-joined <script src> values
    headers: dict[str, str] = field(default_factory=dict)
    cookies: dict[str, str] = field(default_factory=dict)
    metas: dict[str, str] = field(default_factory=dict)
    dns: dict[str, str] = field(default_factory=dict)
    cert_issuer: str = ""
    # Whether the HTML is a rendered DOM (browser fetcher) or raw server HTML.
    # DOM-selector matching runs either way (a selector hit on server HTML is
    # still a true positive); the flag exists so corpus scoring can tell when
    # JS-injected evidence was never observable for a fixture.
    rendered: bool = True
    # Lowercased haystacks for the literal prefilter, computed ONCE per page.
    # Lowercasing a 200KB document allocates a full copy, so doing it per
    # pattern (thousands of times) dominates the entire run.
    _small_blob: str = ""
    _html_lower: str = ""
    # Lazily-built selectolax tree for TARGET_DOM, parsed at most once per page
    # (and only when the catalog actually contains dom signatures).
    _dom_tree: object = field(default=None, repr=False, compare=False)
    _dom_parse_failed: bool = False

    def dom_tree(self):
        if self._dom_tree is None and not self._dom_parse_failed and self.html:
            try:
                from selectolax.parser import HTMLParser

                self._dom_tree = HTMLParser(self.html)
            except Exception:
                self._dom_parse_failed = True
        return self._dom_tree


_SCRIPT_SRC_RE = re.compile(r"<script[^>]+src=[\"']([^\"']+)[\"']", re.IGNORECASE)
_META_RE = re.compile(
    r"<meta[^>]+(?:name|property)=[\"']([^\"']+)[\"'][^>]+content=[\"']([^\"']*)[\"']",
    re.IGNORECASE,
)


def extract_page_signals(
    html: str,
    *,
    url: str = "",
    headers: dict[str, str] | None = None,
    dns: dict[str, str] | None = None,
    cert_issuer: str = "",
    rendered: bool = True,
) -> PageSignals:
    """Extract the small targeted fields from a page, once."""
    headers = {k.lower(): v for k, v in (headers or {}).items()}

    script_srcs = "\n".join(_SCRIPT_SRC_RE.findall(html or ""))
    metas = {m[0].lower(): m[1] for m in _META_RE.findall(html or "")}

    cookies: dict[str, str] = {}
    raw_cookie = headers.get("set-cookie", "")
    for part in re.split(r",(?=[^;]+=)", raw_cookie):
        if "=" in part:
            cname, _, cval = part.strip().partition("=")
            cookies[cname.strip().lower()] = cval.split(";")[0].strip()

    signals = PageSignals(
        html=html or "",
        url=url,
        script_srcs=script_srcs,
        headers=headers,
        cookies=cookies,
        metas=metas,
        dns={k.lower(): v for k, v in (dns or {}).items()},
        cert_issuer=cert_issuer,
        rendered=rendered,
    )
    signals._html_lower = (html or "").lower()
    signals._small_blob = "\n".join([
        script_srcs,
        url,
        "\n".join(f"{k}:{v}" for k, v in headers.items()),
        "\n".join(f"{k}={v}" for k, v in cookies.items()),
        "\n".join(f"{k}={v}" for k, v in metas.items()),
        "\n".join(f"{k}={v}" for k, v in signals.dns.items()),
        cert_issuer,
    ]).lower()
    return signals


def _haystacks(sig: Signature, s: PageSignals) -> list[str]:
    """The string(s) this signature should be matched against."""
    if sig.target == TARGET_SCRIPT:
        return [s.script_srcs]
    if sig.target == TARGET_URL:
        return [s.url]
    if sig.target == TARGET_CERT:
        return [s.cert_issuer]
    if sig.target == TARGET_HTML:
        return [s.html]
    if sig.target == TARGET_HEADER:
        if sig.key:
            v = s.headers.get(sig.key)
            # For a header signature the key's presence is itself meaningful.
            return [v] if v is not None else []
        return list(s.headers.keys()) + list(s.headers.values())
    if sig.target == TARGET_COOKIE:
        if sig.key:
            v = s.cookies.get(sig.key)
            return [v] if v is not None else []
        return list(s.cookies.keys())
    if sig.target == TARGET_META:
        if sig.key:
            v = s.metas.get(sig.key)
            return [v] if v is not None else []
        return list(s.metas.values())
    if sig.target == TARGET_DNS:
        if sig.key:
            v = s.dns.get(sig.key)
            return [v] if v is not None else []
        return list(s.dns.values())
    return []


def _extract_version(match: re.Match, version_group: str | None) -> str | None:
    if not version_group or not match.groups():
        return None
    # Wappalyzer uses \1, \2 ... to reference capture groups
    ref = version_group.strip()
    m = re.match(r"\\?(\d+)", ref)
    if not m:
        return None
    idx = int(m.group(1))
    try:
        raw = match.group(idx) or None
    except (IndexError, re.error):
        return None
    if not raw:
        return None
    # Greedy character classes like ([\d.]+) happily swallow a trailing
    # separator ("jquery-3.6.0.min.js" -> "3.6.0."), so trim punctuation.
    cleaned = raw.strip().strip(".-_/")
    return cleaned or None


def _merge(found: dict[str, Detection], det: Detection) -> None:
    """Insert a detection, deduping case-insensitively by name.

    The built-in catalog and an external one both describe e.g. nginx, so keys
    are normalised. On collision the stronger record wins: high beats medium
    beats low, and a version beats no version.
    """
    key = det.name.strip().lower()
    prev = found.get(key)
    if prev is None:
        found[key] = det
        return
    if _TIER_RANK.get(det.confidence, 0) > _TIER_RANK.get(prev.confidence, 0):
        det.version = det.version or prev.version
        found[key] = det
        return
    if not prev.version and det.version:
        prev.version = det.version
    # Prefer the more specific (longer) display name, e.g. "Nginx" over "nginx"
    # only matters cosmetically; keep the first-seen casing otherwise.
    if prev.category == "other" and det.category != "other":
        prev.category = det.category


def detect(signals: PageSignals) -> list[Detection]:
    """Run the full catalog against one page's extracted signals."""
    catalog = get_catalog()
    found: dict[str, Detection] = {}

    for sig in catalog.signatures:
        if sig.target == TARGET_DOM:
            tree = signals.dom_tree()
            if tree is None:
                continue
            for sel in sig.selectors:
                try:
                    node = tree.css_first(sel)
                except Exception:
                    continue  # malformed selector must never break the run
                if node is not None:
                    _merge(found, Detection(
                        name=sig.name, category=sig.category,
                        confidence=sig.confidence_override or "high",
                        evidence=f"dom: {sel}"[:160], source=TARGET_DOM,
                    ))
                    break
            continue

        hays = _haystacks(sig, signals)
        if not hays:
            continue

        # Empty-pattern signatures (presence-only, e.g. a cookie that just has
        # to exist) are satisfied by the key being present at all.
        for pat in sig.patterns:
            # Cheap literal prefilter against the right-sized haystack.
            if pat.literal:
                blob = signals._html_lower if sig.target == TARGET_HTML else signals._small_blob
                if pat.literal not in blob:
                    continue

            hit = None
            for hay in hays:
                if not hay and pat.regex.pattern in ("", "(?:)"):
                    hit = pat.regex.search("")
                    break
                if hay:
                    hit = pat.regex.search(hay)
                    if hit:
                        break
            if hit is None:
                continue

            confidence = sig.confidence_override or (
                "high" if sig.target in _HIGH_CONFIDENCE_TARGETS and pat.confidence >= 75
                else "medium"
            )
            version = _extract_version(hit, pat.version_group)
            evidence = (hit.group(0) or "")[:160]

            _merge(found, Detection(
                name=sig.name, category=sig.category, confidence=confidence,
                version=version, evidence=evidence, source=sig.target,
            ))
            break  # one hit per signature is enough

    # Resolve `implies` (WordPress implies PHP + MySQL). Implied technologies
    # are always medium confidence — they were inferred, not observed.
    queue = list(found.values())
    seen_implies: set[str] = set()
    while queue:
        det = queue.pop()
        entry = catalog.meta.get(det.name.strip().lower())
        if not entry:
            continue
        for implied in entry[1]:
            ikey = implied.strip().lower()
            if ikey in found or ikey in seen_implies:
                continue
            seen_implies.add(ikey)
            icategory = (catalog.meta.get(ikey) or ("other", []))[0]
            idet = Detection(
                name=implied,
                category=icategory,
                confidence="medium",
                evidence=f"implied by {det.name}",
                source="implies",
            )
            _merge(found, idet)
            queue.append(idet)

    return sorted(found.values(), key=lambda d: (d.category, d.name.lower()))


def detections_to_metadata(detections: list[Detection]) -> dict:
    """Fold detections into the TechStackMetadata field shape."""
    out: dict = {
        "platform": None,
        "js_libraries": [],
        "analytics": [],
        "marketing_tools": [],
        "frameworks": [],
        "cdn": [],
        "widgets": [],
        "web_servers": [],
        "programming_languages": [],
        "server_os": None,
        "databases": [],
        "seo_tools": [],
        "security": [],
        "ecommerce": [],
        "payment_processors": [],
        "build_tools": [],
        "fonts": [],
        "auth": [],
        "monitoring": [],
        "search": [],
        "advertising": [],
        "ab_testing": [],
        "crm": [],
        "video": [],
        "maps": [],
        "translation": [],
        "accessibility": [],
        "other_technologies": [],
        "detections": [],
    }

    # `platform` and `server_os` are single-valued, so when a page yields more
    # than one candidate the strongest evidence has to win. Detections arrive
    # sorted by (category, name), so taking the first match would pick
    # alphabetically: a site declaring itself WordPress via <meta generator>
    # (high) while loading images from Contentful's CDN (medium) would be
    # reported as Contentful. Track confidence per scalar field instead.
    scalar_confidence: dict[str, str] = {}

    for det in detections:
        label = f"{det.name} {det.version}".strip() if det.version else det.name
        field_name = CATEGORY_TO_FIELD.get(det.category)

        if det.confidence == "low":
            # The demotion tier: weak evidence (e.g. a bare vendor word in
            # body text) is preserved in the audit trail below but never
            # asserted in the headline fields.
            pass
        elif field_name in ("platform", "server_os"):
            current = scalar_confidence.get(field_name)
            if out[field_name] is None or (
                current == "medium" and det.confidence == "high"
            ):
                out[field_name] = label
                scalar_confidence[field_name] = det.confidence
        elif field_name in ("hosting", "email_hosting", "ssl_certificate"):
            # Domain-level categories live on CompanyProfile, not here.
            out["other_technologies"].append(label)
        elif field_name and isinstance(out.get(field_name), list):
            if label not in out[field_name]:
                out[field_name].append(label)
        else:
            if label not in out["other_technologies"]:
                out["other_technologies"].append(label)

        out["detections"].append({
            "name": det.name,
            "category": det.category,
            "confidence": det.confidence,
            "version": det.version,
            "evidence": det.evidence,
            # `evidence_type` is the DetectedTech field name for the signal
            # that produced the match; `source` is kept as an alias so
            # existing consumers of the raw dict keep working.
            "evidence_type": det.source,
            "source": det.source,
            "recommended": det.confidence in ("high", "medium"),
        })

    return out
