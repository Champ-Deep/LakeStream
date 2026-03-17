import re
from urllib.parse import urlparse

from src.models.scraped_data import DataType

# Blog landing page patterns — these are index/listing pages, not individual articles.
# Matched when the path IS exactly the blog root (e.g. /blog, /news, /insights)
# or a paginated index (e.g. /blog/page/2, /blog?page=2).
_BLOG_LANDING_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"^/blog/?$",
        r"^/blog/page/",
        r"^/insights?/?$",
        r"^/insights?/page/",
        r"^/news/?$",
        r"^/news/page/",
        r"^/articles?/?$",
        r"^/articles?/page/",
        r"^/posts?/?$",
        r"^/posts?/page/",
        r"^/stories/?$",
        r"^/stories/page/",
    ]
]

# Individual article URL patterns — paths that are clearly a single article under a blog
# section (i.e. have a slug segment after the blog root, or follow date-based patterns).
_ARTICLE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"/blog/.+",        # /blog/<slug>
        r"/insights?/.+",   # /insights/<slug>
        r"/news/.+",        # /news/<slug>
        r"/articles?/.+",   # /articles/<slug>
        r"/posts?/.+",      # /posts/<slug>
        r"/stories/.+",     # /stories/<slug>
        r"/\d{4}/\d{2}/",   # Date-based article URLs e.g. /2024/03/my-article
    ]
]

# URL patterns for classification, ordered by specificity
_PATTERNS: list[tuple[DataType, list[re.Pattern[str]]]] = [
    (
        DataType.PRICING,
        [
            re.compile(p, re.IGNORECASE)
            for p in [
                r"/pricing\b",
                r"/plans?\b",
                r"/packages?\b",
            ]
        ],
    ),
    (
        DataType.CONTACT,
        [
            re.compile(p, re.IGNORECASE)
            for p in [
                r"/contact\b",
                r"/get-in-touch\b",
                r"/demo\b",
                r"/request\b",
                r"/careers?\b",
                r"/jobs?\b",
            ]
        ],
    ),
    (
        DataType.RESOURCE,
        [
            re.compile(p, re.IGNORECASE)
            for p in [
                r"/resources?\b",
                r"/whitepapers?\b",
                r"/case-stud",
                r"/webinars?\b",
                r"/ebooks?\b",
                r"/library\b",
                r"/downloads?\b",
                r"/guides?\b",
            ]
        ],
    ),
    (
        DataType.CONTACT,  # Team pages → contact signals
        [
            re.compile(p, re.IGNORECASE)
            for p in [
                r"/team\b",
                r"/about\b",
                r"/leadership\b",
                r"/people\b",
                r"/our-team\b",
                r"/staff\b",
            ]
        ],
    ),
]


def classify_url(url: str) -> dict:
    """Classify a single URL into a data type based on path patterns.

    Classification priority:
    1. Pricing / Contact / Resource (high-specificity non-blog patterns)
    2. Blog landing pages (index/listing pages) → BLOG_URL
    3. Individual article URLs (slug under blog root or date-based) → ARTICLE
    4. Everything else → PAGE (unclassified, won't trigger content workers)
    """
    parsed = urlparse(url)
    path = parsed.path

    # Check non-blog high-specificity patterns first
    for data_type, patterns in _PATTERNS:
        for pattern in patterns:
            if pattern.search(path):
                return {
                    "url": url,
                    "data_type": data_type.value,
                    "confidence": 0.8,
                }

    # Blog landing / index pages → BlogExtractorWorker will paginate through them
    for pattern in _BLOG_LANDING_PATTERNS:
        if pattern.search(path):
            return {
                "url": url,
                "data_type": DataType.BLOG_URL.value,
                "confidence": 0.9,
            }

    # Individual article URLs → go directly to ArticleParserWorker
    for pattern in _ARTICLE_PATTERNS:
        if pattern.search(path):
            return {
                "url": url,
                "data_type": DataType.ARTICLE.value,
                "confidence": 0.85,
            }

    # Default: unclassified (still useful for site mapping)
    return {
        "url": url,
        "data_type": DataType.PAGE.value,  # Uncategorized — won't trigger content workers
        "confidence": 0.2,
    }


# Minimum confidence required for a URL to be sent to a content worker.
# URLs below this threshold are downgraded to PAGE (unclassified) so they
# are not silently misrouted to the wrong worker.
MIN_CLASSIFICATION_CONFIDENCE = 0.5


def classify_urls(urls: list[str]) -> list[dict]:
    """Classify a list of URLs and return classified results.

    URLs whose confidence falls below MIN_CLASSIFICATION_CONFIDENCE are
    downgraded to PAGE so low-confidence guesses never reach content workers.
    """
    results = []
    for url in urls:
        classified = classify_url(url)
        # Enforce confidence gate — uncertain classifications become PAGE
        if classified["confidence"] < MIN_CLASSIFICATION_CONFIDENCE and classified["data_type"] != DataType.PAGE.value:
            classified = {
                "url": url,
                "data_type": DataType.PAGE.value,
                "confidence": classified["confidence"],
                "original_guess": classified["data_type"],  # kept for debugging
            }
        results.append(classified)
    return results
