"""Canonical technology categories and normalization of the mixed namespace.

`TechParser` emits lowercase snake_case category ids (`cms`, `js_library`).
The Wappalyzer library pass emits its own human-readable names (`Page builders`,
`WordPress plugins`), and `_merge_wappalyzer_detections` writes those verbatim
into the same `DetectedTech.category` field, plus a literal `"other"` sentinel
when a technology has no categories at all.

Everything user-facing keys off `normalize_category`, so the UI filter, the
grouped output and the CSV export all speak one vocabulary.
"""

from __future__ import annotations

CANONICAL_CATEGORIES: list[tuple[str, str]] = [
    ("cms", "CMS / Platform"),
    ("ecommerce", "Ecommerce"),
    ("framework", "Frameworks"),
    ("js_library", "JS Libraries"),
    ("backend", "Backend"),
    ("os", "Operating Systems"),
    ("database", "Databases"),
    ("hosting", "Hosting"),
    ("cdn", "CDN"),
    ("analytics", "Analytics"),
    ("tag_manager", "Tag Managers"),
    ("marketing", "Marketing"),
    ("crm", "CRM"),
    ("seo", "SEO"),
    ("ab_testing", "A/B Testing"),
    ("payment", "Payment"),
    ("auth", "Authentication"),
    ("security", "Security"),
    ("monitoring", "Monitoring"),
    ("search", "Search"),
    ("video", "Video"),
    ("font", "Fonts"),
    ("build_tool", "Build Tools"),
    ("a11y", "Accessibility"),
    ("widgets", "Widgets"),
    ("other", "Other"),
]

CANONICAL_IDS: frozenset[str] = frozenset(c for c, _ in CANONICAL_CATEGORIES)

OTHER = "other"

WAPP_CATEGORY_TO_ID: dict[str, str] = {
    "A/B Testing": "ab_testing",
    "Accessibility": "a11y",
    "Accounting": OTHER,
    "Advertising": "marketing",
    "Affiliate programs": "marketing",
    "Analytics": "analytics",
    "Appointment scheduling": "widgets",
    "Augmented reality": "widgets",
    "Authentication": "auth",
    "Blogs": "cms",
    "Browser fingerprinting": "analytics",
    "Buy now pay later": "payment",
    "CDN": "cdn",
    "CI": "build_tool",
    "CMS": "cms",
    "CRM": "crm",
    "Caching": "backend",
    "Cart abandonment": "marketing",
    "Comment systems": "widgets",
    "Containers": "backend",
    "Content curation": "marketing",
    "Control systems": OTHER,
    "Cookie compliance": "widgets",
    "Cross border ecommerce": "ecommerce",
    "Cryptominers": OTHER,
    "Customer data platform": "marketing",
    "DMS": OTHER,
    "Database managers": "database",
    "Databases": "database",
    "Development": "build_tool",
    "Digital asset management": OTHER,
    "Documentation": OTHER,
    "Domain parking": "hosting",
    "Drupal themes": "cms",
    "Ecommerce": "ecommerce",
    "Ecommerce frontends": "ecommerce",
    "Editors": "build_tool",
    "Email": "marketing",
    "Feature management": "ab_testing",
    "Feed readers": OTHER,
    "Font scripts": "font",
    "Form builders": "widgets",
    "Fulfilment": "ecommerce",
    "Fundraising & donations": "payment",
    "Geolocation": "widgets",
    "Hosting": "hosting",
    "Hosting panels": "hosting",
    "IaaS": "hosting",
    "Issue trackers": OTHER,
    "JavaScript frameworks": "framework",
    "JavaScript graphics": "js_library",
    "JavaScript libraries": "js_library",
    "LMS": "cms",
    "Live chat": "widgets",
    "Livestreaming": "video",
    "Load balancers": "backend",
    "Loyalty & rewards": "marketing",
    "Maps": "widgets",
    "Marketing automation": "marketing",
    "Media servers": "video",
    "Message boards": "cms",
    "Miscellaneous": OTHER,
    "Mobile frameworks": "framework",
    "Network devices": OTHER,
    "Network storage": "hosting",
    "Operating systems": "os",
    "PaaS": "hosting",
    "Page builders": "cms",
    "Payment processors": "payment",
    "Performance": "monitoring",
    "Personalisation": "marketing",
    "Photo galleries": "widgets",
    "Programming languages": "backend",
    "RUM": "monitoring",
    "Recruitment & staffing": OTHER,
    "Referral marketing": "marketing",
    "Remote access": OTHER,
    "Reservations & delivery": "widgets",
    "Retargeting": "marketing",
    "Returns": "ecommerce",
    "Reverse proxies": "backend",
    "Reviews": "widgets",
    "Rich text editors": "widgets",
    "SEO": "seo",
    "SSL/TLS certificate authorities": "security",
    "Search engines": "search",
    "Security": "security",
    "Segmentation": "marketing",
    "Shipping carriers": "ecommerce",
    "Shopify apps": "ecommerce",
    "Shopify themes": "ecommerce",
    "Static site generator": "build_tool",
    "Surveys": "widgets",
    "Tag managers": "tag_manager",
    "Ticket booking": "widgets",
    "Translation": "widgets",
    "UI frameworks": "framework",
    "User onboarding": "widgets",
    "Video players": "video",
    "Web frameworks": "framework",
    "Web server extensions": "backend",
    "Web servers": "backend",
    "Webcams": OTHER,
    "Webmail": OTHER,
    "Widgets": "widgets",
    "Wikis": "cms",
    "WordPress plugins": "cms",
    "WordPress themes": "cms",
}

_FIELD_TO_ID: dict[str, str] = {
    "platform": "cms",
    "frameworks": "framework",
    "js_libraries": "js_library",
    "analytics": "analytics",
    "marketing_tools": "marketing",
    "cdn": "cdn",
    "hosting": "hosting",
    "backend": "backend",
    "build_tools": "build_tool",
    "fonts": "font",
    "payment": "payment",
    "auth": "auth",
    "monitoring": "monitoring",
    "search": "search",
    "ab_testing": "ab_testing",
    "tag_managers": "tag_manager",
    "video": "video",
    "ecommerce": "ecommerce",
    "accessibility": "a11y",
    "databases": "database",
    "crm": "crm",
    "seo": "seo",
    "os": "os",
    "security": "security",
    "widgets": "widgets",
}

_LOWER_LOOKUP: dict[str, str] = {k.lower(): v for k, v in WAPP_CATEGORY_TO_ID.items()}


def normalize_category(raw: str | None) -> str:
    """Map any category string from any detector onto a canonical id.

    Resolution order: canonical id -> exact Wappalyzer name -> case-insensitive
    Wappalyzer name -> flat `detect()` field name -> "other". Never raises and
    never returns an id outside CANONICAL_IDS, so nothing is silently dropped.
    """
    if not raw:
        return OTHER
    if raw in CANONICAL_IDS:
        return raw
    mapped = WAPP_CATEGORY_TO_ID.get(raw) or _LOWER_LOOKUP.get(raw.lower())
    if mapped:
        return mapped
    return _FIELD_TO_ID.get(raw, OTHER)


def field_to_id(field: str) -> str:
    """Canonical id for a flat `TechParser.detect()` output field name."""
    return _FIELD_TO_ID.get(field, OTHER)


def group_detections(detections: list[dict]) -> dict[str, list[str]]:
    """Bucket detections by canonical category id, preserving order.

    Reads from `detections` rather than the flat fields because `detections` is
    a strict superset: `platform` keeps only the first CMS hit, and most
    Wappalyzer findings never reach a flat column at all.
    """
    grouped: dict[str, list[str]] = {}
    for det in detections:
        name = det.get("name")
        if not name:
            continue
        cid = normalize_category(det.get("category"))
        bucket = grouped.setdefault(cid, [])
        if name not in bucket:
            bucket.append(name)
    return grouped
