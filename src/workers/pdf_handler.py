"""PDF document extraction, extracted 1:1 from ``ContentWorker._process_pdf``.

Pure: parses PDF bytes into a document record. Persistence (``export_results``)
and DTO assembly remain orchestration concerns and stay in ``ContentWorker``.
"""

from typing import Any
from uuid import UUID

import structlog

from src.models.scraped_data import DataType, DocumentMetadata
from src.scraping.parser.pdf_parser import parse_pdf, pdf_to_markdown

log = structlog.get_logger()


def build_document_record(
    job_id: str,
    domain: str,
    url: str,
    content_bytes: bytes | None,
    logger: Any | None = None,
) -> dict | None:
    """Parse PDF bytes into a DOCUMENT record, or None if there's nothing to save."""
    logger = logger or log

    if not content_bytes:
        logger.warning("pdf_no_content", url=url)
        return None

    try:
        result = parse_pdf(content_bytes)
    except ValueError as e:
        logger.warning("pdf_parse_error", url=url, error=str(e))
        return None

    if not result.text and not result.tables:
        return None

    markdown = pdf_to_markdown(result)

    metadata = DocumentMetadata(
        source_type="pdf",
        page_count=result.page_count,
        author=result.metadata.get("author"),
        tables=result.tables,
        word_count=result.word_count,
        text_content=markdown,
    )

    return {
        "job_id": UUID(job_id),
        "domain": domain,
        "data_type": DataType.DOCUMENT,
        "url": url,
        "title": result.metadata.get("title") or f"PDF: {url.split('/')[-1]}",
        "metadata": metadata.model_dump(),
    }
