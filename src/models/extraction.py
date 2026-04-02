"""Models for schema-based structured extraction.

Users define an ExtractionSchema with fields (CSS selectors + types),
and the schema extractor returns structured JSON matching that schema.
Used by both CSS-based and AI-based extraction (Phase 5).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ExtractionField(BaseModel):
    """A single field to extract from the page."""

    name: str = Field(description="Field name in the output JSON")
    selector: str = Field(description="CSS selector to locate the element")
    attribute: str = Field(
        default="text",
        description="What to extract: 'text' (inner text), 'href', 'src', or any HTML attribute",
    )
    type: str = Field(
        default="string",
        description="Output type: string, number, boolean, list",
    )
    required: bool = False
    transform: str | None = Field(
        default=None,
        description="Post-processing: strip, lower, upper, split_comma",
    )


class ExtractionSchema(BaseModel):
    """Defines what to extract from a page."""

    name: str = Field(default="custom", description="Schema name for reference")
    fields: list[ExtractionField]
    list_selector: str | None = Field(
        default=None,
        description="CSS selector for repeating items (e.g. product cards, table rows). "
        "If set, fields are extracted per item. If None, fields are extracted once.",
    )
    description: str = ""


class ExtractionResult(BaseModel):
    """Result of schema-based extraction."""

    schema_name: str
    data: list[dict] | dict
    url: str
    extracted_at: datetime
    fields_found: int
    fields_missing: list[str] = []
    mode: str = "css"  # css, ai, auto
