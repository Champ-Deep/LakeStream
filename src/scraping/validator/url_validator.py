from src.utils.url import is_valid_scrape_url, normalize_url


def validate_and_deduplicate(urls: list[str]) -> list[str]:
    """Validate, normalize, and deduplicate a list of URLs."""
    seen: set[str] = set()
    result: list[str] = []

    for url in urls:
        if not is_valid_scrape_url(url):
            continue

        normalized = normalize_url(url)
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)

    return result
