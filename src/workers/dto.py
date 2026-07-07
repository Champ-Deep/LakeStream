"""Shared dict-record -> ScrapedData DTO assembly.

``ContentWorker`` previously re-wrote this same wrapping logic in three
separate places (raw-only return, normal return, PDF return). This module
gives it one home.
"""

from datetime import UTC, datetime
from uuid import UUID

from src.models.scraped_data import ScrapedData


def build_scraped_data(record: dict, job_id: str, domain: str, url: str) -> ScrapedData:
    """Wrap a plain extraction-result dict into a ScrapedData DTO.

    ``url`` is the fallback used when the record itself has no "url" key
    (matches original inline behavior: ``rec.get("url", url)``).
    """
    return ScrapedData(
        id=UUID(int=0),
        job_id=UUID(job_id),
        domain=domain,
        data_type=record["data_type"],
        url=record.get("url", url),
        title=record.get("title"),
        metadata=record.get("metadata", {}),
        scraped_at=datetime.now(UTC),
    )


def build_scraped_data_list(
    records: list[dict], job_id: str, domain: str, url: str,
) -> list[ScrapedData]:
    """Wrap a list of extraction-result dicts into ScrapedData DTOs."""
    return [build_scraped_data(rec, job_id, domain, url) for rec in records]
