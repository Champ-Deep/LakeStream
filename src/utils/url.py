from urllib.parse import urljoin, urlparse, urlunparse


def normalize_url(url: str, base_url: str | None = None) -> str:
    """Normalize a URL: resolve relative, strip fragments, lowercase scheme/host."""
    if base_url and not url.startswith(("http://", "https://")):
        url = urljoin(base_url, url)

    parsed = urlparse(url)
    normalized = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        fragment="",
    )
    # Strip trailing slash from path (unless it's just "/")
    path = normalized.path.rstrip("/") or "/"
    normalized = normalized._replace(path=path)

    return urlunparse(normalized)


def extract_domain(url: str) -> str:
    """Extract the domain (netloc) from a URL."""
    parsed = urlparse(url)
    domain = parsed.netloc or parsed.path.split("/")[0]
    # Remove www. prefix
    if domain.startswith("www."):
        domain = domain[4:]
    return domain.lower()


def ensure_scheme(url: str, default_scheme: str = "https") -> str:
    """Ensure a URL has a scheme prefix."""
    if not url.startswith(("http://", "https://")):
        return f"{default_scheme}://{url}"
    return url


def is_valid_scrape_url(url: str) -> bool:
    """Check if a URL is worth scraping (not a file, mailto, anchor, etc.)."""
    if not url or url.startswith(("#", "mailto:", "tel:", "javascript:")):
        return False

    parsed = urlparse(url)
    # Skip common non-HTML extensions
    skip_extensions = {
        ".pdf",
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".svg",
        ".ico",
        ".css",
        ".js",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".mp3",
        ".mp4",
        ".avi",
        ".mov",
        ".zip",
        ".gz",
        ".tar",
        ".xml",
        ".rss",
        ".atom",
    }
    path_lower = parsed.path.lower()
    return not any(path_lower.endswith(ext) for ext in skip_extensions)
