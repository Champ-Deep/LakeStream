"""Universal document → markdown converter ("Parse Bytes").

Dispatches raw bytes by detected file type to the right parser:
  - PDF   → pdf_parser (PyMuPDF + pdfplumber)
  - DOCX  → mammoth (docx→HTML) → html_to_markdown
  - HTML  → html_to_markdown
  - text / markdown / csv → decoded passthrough

Detection is intentionally lightweight (extension + content-type header +
magic bytes) — no python-magic system dependency.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

import structlog

from src.scraping.parser.markdown import content_hash, html_to_markdown
from src.scraping.parser.pdf_parser import MAX_PDF_BYTES, parse_pdf, pdf_to_markdown

log = structlog.get_logger()

# Max document size for any type (matches the existing PDF cap)
MAX_DOC_BYTES = MAX_PDF_BYTES

_TEXT_TYPES = {"text", "markdown", "csv"}
SUPPORTED_TYPES = {"pdf", "docx", "html"} | _TEXT_TYPES


@dataclass
class DocumentParseResult:
    source_type: str = "unknown"
    markdown: str = ""
    text: str = ""
    tables: list[list[list[str]]] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    word_count: int = 0
    page_count: int | None = None
    content_hash: str = ""


def detect_document_type(
    filename: str | None,
    content_type: str | None,
    content: bytes,
) -> str:
    """Best-effort file type: magic bytes first, then content-type, then extension."""
    head = content[:512].lstrip()

    # Magic bytes are the strongest signal
    if head.startswith(b"%PDF"):
        return "pdf"
    if content[:4] == b"PK\x03\x04":
        # Zip container — docx if the package mentions word/, else unsupported zip
        if b"word/" in content[:4096] or (filename or "").lower().endswith(".docx"):
            return "docx"
        return "zip"
    if head[:1] == b"<" and (b"<html" in head.lower() or b"<!doctype" in head.lower() or b"<body" in head.lower()):
        return "html"

    ct = (content_type or "").lower().split(";")[0].strip()
    if "pdf" in ct:
        return "pdf"
    if "wordprocessingml" in ct or ct == "application/msword":
        return "docx"
    if "html" in ct:
        return "html"
    if ct.startswith("text/csv"):
        return "csv"
    if ct.startswith("text/markdown"):
        return "markdown"

    name = (filename or "").lower()
    for ext, kind in (
        (".pdf", "pdf"), (".docx", "docx"), (".html", "html"), (".htm", "html"),
        (".md", "markdown"), (".markdown", "markdown"), (".csv", "csv"), (".txt", "text"),
    ):
        if name.endswith(ext):
            return kind

    # Fall back to text when it decodes cleanly, else unknown binary
    try:
        head.decode("utf-8")
        return "text"
    except UnicodeDecodeError:
        return "unknown"


def _decode(content: bytes) -> str:
    for enc in ("utf-8", "utf-16", "latin-1"):
        try:
            return content.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return content.decode("utf-8", errors="replace")


def _parse_docx(content: bytes) -> DocumentParseResult:
    """DOCX → HTML via mammoth → markdown. Falls back with a clear error if absent."""
    try:
        import mammoth
    except ImportError as e:
        raise ValueError("DOCX parsing unavailable — the 'mammoth' package is not installed") from e

    conv = mammoth.convert_to_html(io.BytesIO(content))
    html = conv.value or ""
    markdown = html_to_markdown(html, find_main=False)
    text = html_to_markdown(html, find_main=False, strip_images=True)
    return DocumentParseResult(
        source_type="docx",
        markdown=markdown,
        text=text,
        metadata={"messages": [str(m) for m in conv.messages[:10]]},
        word_count=len(text.split()),
    )


def parse_document(
    content: bytes,
    *,
    filename: str | None = None,
    content_type: str | None = None,
) -> DocumentParseResult:
    """Parse arbitrary document bytes into markdown + text.

    Raises:
        ValueError: On oversize input or unsupported/undetectable file types.
    """
    if not content:
        raise ValueError("Empty document")
    if len(content) > MAX_DOC_BYTES:
        raise ValueError(f"Document too large ({len(content)} bytes, max {MAX_DOC_BYTES})")

    kind = detect_document_type(filename, content_type, content)

    if kind == "pdf":
        pdf = parse_pdf(content)
        result = DocumentParseResult(
            source_type="pdf",
            markdown=pdf_to_markdown(pdf),
            text=pdf.text,
            tables=pdf.tables,
            metadata=pdf.metadata,
            word_count=pdf.word_count,
            page_count=pdf.page_count,
        )
    elif kind == "docx":
        result = _parse_docx(content)
    elif kind == "html":
        html = _decode(content)
        markdown = html_to_markdown(html)
        result = DocumentParseResult(
            source_type="html",
            markdown=markdown,
            text=markdown,
            word_count=len(markdown.split()),
        )
    elif kind in _TEXT_TYPES:
        text = _decode(content)
        result = DocumentParseResult(
            source_type=kind,
            markdown=text,
            text=text,
            word_count=len(text.split()),
        )
    else:
        raise ValueError(
            f"Unsupported document type '{kind}' "
            f"(supported: {', '.join(sorted(SUPPORTED_TYPES))})"
        )

    result.content_hash = content_hash(result.markdown) if result.markdown else ""
    return result
