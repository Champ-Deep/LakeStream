"""CSS-based schema extractor.

Given an ExtractionSchema and HTML, extracts structured data using
CSS selectors. Wraps selectolax (same as HtmlParser).

For repeating items (tables, card grids), set list_selector on the schema
and each field's selector is relative to each matched item.
"""

from __future__ import annotations

from datetime import UTC, datetime

from selectolax.parser import HTMLParser

from src.models.extraction import ExtractionField, ExtractionResult, ExtractionSchema


class SchemaExtractor:
    """Extract structured data from HTML using CSS selectors."""

    def __init__(self, html: str, url: str) -> None:
        self._parser = HTMLParser(html)
        self._url = url

    def extract(self, schema: ExtractionSchema) -> ExtractionResult:
        """Run extraction according to schema definition.

        If schema.list_selector is set, extracts per repeating item.
        Otherwise, extracts once from the whole document.
        """
        if schema.list_selector:
            data = self._extract_list(schema)
        else:
            data = self._extract_single(schema)

        # Count found vs missing
        if isinstance(data, list):
            all_found = set()
            for item in data:
                all_found.update(item.keys())
            fields_found = len(all_found)
        else:
            fields_found = len([v for v in data.values() if v is not None])

        found_names = set(data.keys()) if isinstance(data, dict) else (
            set().union(*(item.keys() for item in data)) if data else set()
        )
        fields_missing = [
            f.name for f in schema.fields
            if f.required and f.name not in found_names
        ]

        return ExtractionResult(
            schema_name=schema.name,
            data=data,
            url=self._url,
            extracted_at=datetime.now(UTC),
            fields_found=fields_found,
            fields_missing=fields_missing,
            mode="css",
        )

    def _extract_single(self, schema: ExtractionSchema) -> dict:
        """Extract fields once from the whole document."""
        result = {}
        root = self._parser.root
        if not root:
            return result

        for field in schema.fields:
            value = self._extract_field(root, field)
            if value is not None:
                result[field.name] = value

        return result

    def _extract_list(self, schema: ExtractionSchema) -> list[dict]:
        """Extract fields per repeating item."""
        items = self._parser.css(schema.list_selector)  # type: ignore[arg-type]
        results = []

        for item in items:
            row = {}
            for field in schema.fields:
                value = self._extract_field(item, field)
                if value is not None:
                    row[field.name] = value

            # Only include rows that have at least one value
            if row:
                results.append(row)

        return results

    def _extract_field(self, node: object, field: ExtractionField) -> object:
        """Extract a single field from a node."""
        try:
            el = node.css_first(field.selector)  # type: ignore[union-attr]
            if el is None:
                return None

            # Get raw value based on attribute type
            if field.attribute == "text":
                raw = el.text(strip=True)
            else:
                raw = el.attributes.get(field.attribute)

            if raw is None:
                return None

            # Apply transform
            raw = self._apply_transform(raw, field.transform)

            # Coerce to target type
            return self._coerce_type(raw, field.type)

        except Exception:
            return None

    def _apply_transform(self, value: str, transform: str | None) -> str:
        """Apply optional post-processing transform."""
        if not transform:
            return value

        match transform:
            case "strip":
                return value.strip()
            case "lower":
                return value.lower()
            case "upper":
                return value.upper()
            case "split_comma":
                return value  # Handled in coerce_type for list type
            case _:
                return value

    def _coerce_type(self, value: str, target_type: str) -> object:
        """Coerce extracted string to target type."""
        match target_type:
            case "string":
                return value
            case "number":
                # Try int first, then float
                cleaned = value.replace(",", "").replace("$", "").strip()
                try:
                    return int(cleaned)
                except ValueError:
                    try:
                        return float(cleaned)
                    except ValueError:
                        return value
            case "boolean":
                return value.lower() in ("true", "yes", "1", "on")
            case "list":
                return [item.strip() for item in value.split(",") if item.strip()]
            case _:
                return value
