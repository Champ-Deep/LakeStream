"""Tests for CSS-based schema extractor."""

from src.models.extraction import ExtractionField, ExtractionSchema
from src.scraping.parser.schema_extractor import SchemaExtractor

SAMPLE_HTML = """
<html>
<body>
    <h1>My Company</h1>
    <p class="tagline">We make great stuff</p>
    <a class="website" href="https://example.com">Visit us</a>
    <span class="employee-count">1,500</span>
    <span class="is-public">true</span>

    <div class="pricing">
        <div class="plan">
            <h3>Starter</h3>
            <span class="price">$29/mo</span>
            <ul class="features">Design tools, 5 projects, Email support</ul>
        </div>
        <div class="plan">
            <h3>Pro</h3>
            <span class="price">$99/mo</span>
            <ul class="features">All tools, Unlimited projects, Priority support</ul>
        </div>
        <div class="plan">
            <h3>Enterprise</h3>
            <span class="price">$299/mo</span>
            <ul class="features">Custom, Dedicated manager, SLA</ul>
        </div>
    </div>
</body>
</html>
"""


class TestSingleExtraction:
    """Test extracting fields from the whole document (no list_selector)."""

    def test_extract_text(self):
        schema = ExtractionSchema(
            name="company",
            fields=[
                ExtractionField(name="name", selector="h1"),
                ExtractionField(name="tagline", selector=".tagline"),
            ],
        )
        extractor = SchemaExtractor(SAMPLE_HTML, "https://example.com")
        result = extractor.extract(schema)

        assert result.data["name"] == "My Company"
        assert result.data["tagline"] == "We make great stuff"
        assert result.fields_found == 2

    def test_extract_attribute(self):
        schema = ExtractionSchema(
            name="links",
            fields=[
                ExtractionField(
                    name="url", selector=".website", attribute="href",
                ),
            ],
        )
        extractor = SchemaExtractor(SAMPLE_HTML, "https://example.com")
        result = extractor.extract(schema)

        assert result.data["url"] == "https://example.com"

    def test_extract_number_type(self):
        schema = ExtractionSchema(
            name="stats",
            fields=[
                ExtractionField(
                    name="employees",
                    selector=".employee-count",
                    type="number",
                ),
            ],
        )
        extractor = SchemaExtractor(SAMPLE_HTML, "https://example.com")
        result = extractor.extract(schema)

        assert result.data["employees"] == 1500

    def test_extract_boolean_type(self):
        schema = ExtractionSchema(
            name="flags",
            fields=[
                ExtractionField(
                    name="is_public",
                    selector=".is-public",
                    type="boolean",
                ),
            ],
        )
        extractor = SchemaExtractor(SAMPLE_HTML, "https://example.com")
        result = extractor.extract(schema)

        assert result.data["is_public"] is True

    def test_missing_field_not_in_result(self):
        schema = ExtractionSchema(
            name="test",
            fields=[
                ExtractionField(name="exists", selector="h1"),
                ExtractionField(name="missing", selector=".nonexistent"),
            ],
        )
        extractor = SchemaExtractor(SAMPLE_HTML, "https://example.com")
        result = extractor.extract(schema)

        assert "exists" in result.data
        assert "missing" not in result.data

    def test_required_field_reported_missing(self):
        schema = ExtractionSchema(
            name="test",
            fields=[
                ExtractionField(
                    name="required_field",
                    selector=".nonexistent",
                    required=True,
                ),
            ],
        )
        extractor = SchemaExtractor(SAMPLE_HTML, "https://example.com")
        result = extractor.extract(schema)

        assert "required_field" in result.fields_missing


class TestListExtraction:
    """Test extracting repeating items with list_selector."""

    def test_extract_pricing_plans(self):
        schema = ExtractionSchema(
            name="pricing",
            list_selector=".plan",
            fields=[
                ExtractionField(name="plan_name", selector="h3"),
                ExtractionField(name="price", selector=".price"),
            ],
        )
        extractor = SchemaExtractor(SAMPLE_HTML, "https://example.com")
        result = extractor.extract(schema)

        assert isinstance(result.data, list)
        assert len(result.data) == 3
        assert result.data[0]["plan_name"] == "Starter"
        assert result.data[0]["price"] == "$29/mo"
        assert result.data[2]["plan_name"] == "Enterprise"

    def test_extract_list_type_field(self):
        schema = ExtractionSchema(
            name="features",
            list_selector=".plan",
            fields=[
                ExtractionField(name="plan", selector="h3"),
                ExtractionField(
                    name="features",
                    selector=".features",
                    type="list",
                    transform="split_comma",
                ),
            ],
        )
        extractor = SchemaExtractor(SAMPLE_HTML, "https://example.com")
        result = extractor.extract(schema)

        assert isinstance(result.data[0]["features"], list)
        assert len(result.data[0]["features"]) == 3

    def test_empty_list_when_no_matches(self):
        schema = ExtractionSchema(
            name="none",
            list_selector=".nonexistent-class",
            fields=[
                ExtractionField(name="x", selector="span"),
            ],
        )
        extractor = SchemaExtractor(SAMPLE_HTML, "https://example.com")
        result = extractor.extract(schema)

        assert result.data == []


class TestTransforms:
    def test_lower_transform(self):
        schema = ExtractionSchema(
            name="test",
            fields=[
                ExtractionField(
                    name="name",
                    selector="h1",
                    transform="lower",
                ),
            ],
        )
        extractor = SchemaExtractor(SAMPLE_HTML, "https://example.com")
        result = extractor.extract(schema)

        assert result.data["name"] == "my company"

    def test_upper_transform(self):
        schema = ExtractionSchema(
            name="test",
            fields=[
                ExtractionField(
                    name="name",
                    selector="h1",
                    transform="upper",
                ),
            ],
        )
        extractor = SchemaExtractor(SAMPLE_HTML, "https://example.com")
        result = extractor.extract(schema)

        assert result.data["name"] == "MY COMPANY"


class TestResultMetadata:
    def test_result_has_correct_schema_name(self):
        schema = ExtractionSchema(
            name="my_schema",
            fields=[ExtractionField(name="x", selector="h1")],
        )
        extractor = SchemaExtractor(SAMPLE_HTML, "https://example.com")
        result = extractor.extract(schema)

        assert result.schema_name == "my_schema"
        assert result.url == "https://example.com"
        assert result.mode == "css"
        assert result.extracted_at is not None
