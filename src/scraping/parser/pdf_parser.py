"""PDF document parser using PyMuPDF + pdfplumber.

Extracts text, tables, and metadata from PDF bytes.
PyMuPDF (fitz) handles text/metadata; pdfplumber handles tables.
Both are pure Python wheels — no system dependencies for Railway.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog

log = structlog.get_logger()

# Max PDF size to process (10 MB)
MAX_PDF_BYTES = 10 * 1024 * 1024


@dataclass
class PdfParseResult:
    text: str = ""
    tables: list[list[list[str]]] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    word_count: int = 0
    page_count: int = 0


def parse_pdf(content: bytes) -> PdfParseResult:
    """Extract text, tables, and metadata from PDF bytes.

    Args:
        content: Raw PDF file bytes.

    Returns:
        PdfParseResult with extracted text, tables, metadata, and word count.

    Raises:
        ValueError: If content exceeds MAX_PDF_BYTES.
    """
    if len(content) > MAX_PDF_BYTES:
        raise ValueError(
            f"PDF too large ({len(content)} bytes, max {MAX_PDF_BYTES})"
        )

    result = PdfParseResult()

    # --- Text + metadata via PyMuPDF ---
    try:
        import pymupdf

        doc = pymupdf.open(stream=content, filetype="pdf")
        result.page_count = len(doc)

        # Metadata
        meta = doc.metadata or {}
        result.metadata = {
            "title": meta.get("title", ""),
            "author": meta.get("author", ""),
            "subject": meta.get("subject", ""),
            "creator": meta.get("creator", ""),
            "producer": meta.get("producer", ""),
            "creation_date": meta.get("creationDate", ""),
            "page_count": result.page_count,
        }

        # Text extraction — page by page
        pages_text = []
        for page in doc:
            text = page.get_text("text")
            if text:
                pages_text.append(text.strip())

        result.text = "\n\n".join(pages_text)
        result.word_count = len(result.text.split()) if result.text else 0

        doc.close()
    except ImportError:
        log.warning("pymupdf_not_installed")
        return result
    except Exception as e:
        log.error("pdf_text_extraction_error", error=str(e))
        return result

    # --- Table extraction via pdfplumber ---
    try:
        import pdfplumber

        with pdfplumber.open(stream=content) as pdf:
            for page in pdf.pages:
                page_tables = page.extract_tables()
                if page_tables:
                    for table in page_tables:
                        # Convert None cells to empty strings
                        cleaned = [
                            [str(cell) if cell is not None else "" for cell in row]
                            for row in table
                            if row  # skip empty rows
                        ]
                        if cleaned:
                            result.tables.append(cleaned)
    except ImportError:
        log.debug("pdfplumber_not_installed_skipping_tables")
    except Exception as e:
        log.warning("pdf_table_extraction_error", error=str(e))

    return result


def pdf_to_markdown(result: PdfParseResult) -> str:
    """Convert PdfParseResult to clean Markdown.

    Includes text content and any tables rendered as Markdown tables.
    """
    parts: list[str] = []

    # Metadata header
    title = result.metadata.get("title", "")
    author = result.metadata.get("author", "")
    if title:
        parts.append(f"# {title}")
    if author:
        parts.append(f"*Author: {author}*")
    if title or author:
        parts.append(f"*Pages: {result.page_count} | Words: {result.word_count}*")
        parts.append("---")

    # Main text
    if result.text:
        parts.append(result.text)

    # Tables
    if result.tables:
        parts.append("\n---\n## Extracted Tables\n")
        for i, table in enumerate(result.tables, 1):
            if len(table) < 2:
                continue

            parts.append(f"### Table {i}")

            # First row as header
            header = table[0]
            parts.append("| " + " | ".join(header) + " |")
            parts.append("| " + " | ".join("---" for _ in header) + " |")

            # Data rows
            for row in table[1:]:
                # Pad row to match header length
                padded = row + [""] * (len(header) - len(row))
                parts.append("| " + " | ".join(padded[:len(header)]) + " |")

            parts.append("")

    return "\n\n".join(parts)
