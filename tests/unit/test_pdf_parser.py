"""Tests for PDF parser."""

import pymupdf

from src.scraping.parser.pdf_parser import (
    MAX_PDF_BYTES,
    PdfParseResult,
    parse_pdf,
    pdf_to_markdown,
)


def _make_simple_pdf(text: str = "Hello World", pages: int = 1) -> bytes:
    """Create a minimal PDF in memory for testing."""
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"{text} - Page {i + 1}")
    content = doc.tobytes()
    doc.close()
    return content


def _make_pdf_with_metadata(title: str, author: str) -> bytes:
    """Create a PDF with metadata."""
    doc = pymupdf.open()
    doc.set_metadata({"title": title, "author": author})
    page = doc.new_page()
    page.insert_text((72, 72), "Content here")
    content = doc.tobytes()
    doc.close()
    return content


class TestParsePdf:
    def test_extracts_text(self):
        pdf_bytes = _make_simple_pdf("Test content")
        result = parse_pdf(pdf_bytes)

        assert "Test content" in result.text
        assert result.word_count > 0
        assert result.page_count == 1

    def test_multi_page(self):
        pdf_bytes = _make_simple_pdf("Multi page doc", pages=3)
        result = parse_pdf(pdf_bytes)

        assert result.page_count == 3
        assert "Page 1" in result.text
        assert "Page 3" in result.text

    def test_extracts_metadata(self):
        pdf_bytes = _make_pdf_with_metadata("My Title", "John Doe")
        result = parse_pdf(pdf_bytes)

        assert result.metadata["title"] == "My Title"
        assert result.metadata["author"] == "John Doe"
        assert result.metadata["page_count"] == 1

    def test_rejects_oversized_pdf(self):
        try:
            # Create bytes larger than MAX_PDF_BYTES
            parse_pdf(b"x" * (MAX_PDF_BYTES + 1))
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "too large" in str(e)

    def test_empty_pdf(self):
        doc = pymupdf.open()
        doc.new_page()  # blank page
        content = doc.tobytes()
        doc.close()

        result = parse_pdf(content)
        assert result.page_count == 1
        assert result.word_count == 0

    def test_returns_empty_result_for_invalid_bytes(self):
        result = parse_pdf(b"not a real pdf")
        # Should not crash — returns empty result
        assert result.text == ""
        assert result.page_count == 0


class TestPdfToMarkdown:
    def test_basic_markdown(self):
        result = PdfParseResult(
            text="Hello world content here.",
            metadata={"title": "Test Doc", "author": "Author"},
            page_count=1,
            word_count=4,
        )
        md = pdf_to_markdown(result)

        assert "# Test Doc" in md
        assert "Author" in md
        assert "Hello world content here." in md

    def test_markdown_with_tables(self):
        result = PdfParseResult(
            text="Some text.",
            tables=[[["Name", "Value"], ["A", "1"], ["B", "2"]]],
            metadata={},
            page_count=1,
            word_count=2,
        )
        md = pdf_to_markdown(result)

        assert "| Name | Value |" in md
        assert "| A | 1 |" in md
        assert "Table 1" in md

    def test_no_metadata_no_header(self):
        result = PdfParseResult(
            text="Just text.",
            metadata={"title": "", "author": ""},
            page_count=1,
            word_count=2,
        )
        md = pdf_to_markdown(result)
        assert md.startswith("Just text.")

    def test_empty_result(self):
        result = PdfParseResult()
        md = pdf_to_markdown(result)
        assert md == ""
