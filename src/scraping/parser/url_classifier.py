import re
from urllib.parse import urlparse

from src.models.scraped_data import DataType

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
        DataType.BLOG_URL,
        [
            re.compile(p, re.IGNORECASE)
            for p in [
                r"/blog\b",
                r"/insights?\b",
                r"/news\b",
                r"/articles?\b",
                r"/posts?\b",
                r"/stories\b",
                r"/\d{4}/\d{2}/",  # Date-based article URLs
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
    """Classify a single URL into a data type based on path patterns."""
    parsed = urlparse(url)
    path = parsed.path

    for data_type, patterns in _PATTERNS:
        for pattern in patterns:
            if pattern.search(path):
                return {
                    "url": url,
                    "data_type": data_type.value,
                    "confidence": 0.8,
                }

    # Default: unclassified (still useful for site mapping)
    return {
        "url": url,
        "data_type": DataType.BLOG_URL.value,  # Default to blog for discovery
        "confidence": 0.2,
    }


def classify_urls(urls: list[str]) -> list[dict]:
    """Classify a list of URLs and return classified results."""
    return [classify_url(url) for url in urls]
