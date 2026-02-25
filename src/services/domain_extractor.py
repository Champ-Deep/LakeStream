"""Domain deduplication logic for discovery pipeline."""

from src.services.lakecurrent import SearchResult


def extract_unique_domains(
    results: list[SearchResult],
    skip_domains: set[str] | None = None,
) -> dict[str, SearchResult]:
    """Return {domain: best_result} with deduplication.

    Picks the highest-scored result per root domain.
    Filters out domains in skip_domains set.
    """
    skip = skip_domains or set()
    domain_map: dict[str, SearchResult] = {}

    for result in results:
        if result.domain in skip:
            continue
        existing = domain_map.get(result.domain)
        if existing is None or (result.score or 0) > (existing.score or 0):
            domain_map[result.domain] = result

    return domain_map
