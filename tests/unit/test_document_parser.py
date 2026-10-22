"""Offline unit tests for the Parse Bytes document parser + NAICS crosswalk."""

import pytest

from src.scraping.parser.document_parser import (
    DocumentParseResult,
    detect_document_type,
    parse_document,
)
from src.services.naics_crosswalk import industry_to_codes


class TestDetectDocumentType:
    def test_pdf_magic_bytes(self):
        assert detect_document_type(None, None, b"%PDF-1.7\n...") == "pdf"

    def test_docx_zip_magic(self):
        content = b"PK\x03\x04" + b"..." + b"word/document.xml" + b"..."
        assert detect_document_type("resume.docx", None, content) == "docx"

    def test_html_magic(self):
        assert detect_document_type(None, None, b"<!doctype html><html>") == "html"

    def test_content_type_fallback(self):
        assert detect_document_type(None, "application/pdf", b"garbage") == "pdf"

    def test_extension_fallback(self):
        assert detect_document_type("notes.md", None, b"hello world") == "markdown"

    def test_plain_text_default(self):
        assert detect_document_type(None, None, b"just some text") == "text"


class TestParseDocument:
    def test_html_to_markdown(self):
        html = b"<html><body><main><h1>Title</h1><p>Body text</p></main></body></html>"
        result = parse_document(html, content_type="text/html")
        assert result.source_type == "html"
        assert "# Title" in result.markdown
        assert result.content_hash

    def test_text_passthrough(self):
        result = parse_document(b"plain content", content_type="text/plain")
        assert result.source_type == "text"
        assert result.markdown == "plain content"

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="Empty document"):
            parse_document(b"")

    def test_oversize_raises(self):
        from src.scraping.parser.document_parser import MAX_DOC_BYTES

        with pytest.raises(ValueError, match="too large"):
            parse_document(b"x" * (MAX_DOC_BYTES + 1), content_type="text/plain")

    def test_result_is_dataclass(self):
        assert isinstance(parse_document(b"hi", content_type="text/plain"), DocumentParseResult)


class TestNaicsCrosswalk:
    def test_known_industry(self):
        naics, sic = industry_to_codes("Software")
        assert naics == "5112"
        assert sic == "7372"

    def test_case_insensitive(self):
        assert industry_to_codes("software") == industry_to_codes("Software")

    def test_unknown_returns_none(self):
        assert industry_to_codes("Nonexistent Industry") == (None, None)

    def test_none_returns_none(self):
        assert industry_to_codes(None) == (None, None)

    def test_all_taxonomy_industries_mapped(self):
        from src.models.lake_b2b import LAKE_B2B_INDUSTRIES

        for industry in LAKE_B2B_INDUSTRIES:
            naics, sic = industry_to_codes(industry)
            assert naics and sic, f"{industry} has no NAICS/SIC mapping"
