import csv
import io
from datetime import UTC, datetime
from uuid import uuid4

from src.models.scraped_data import DataType, ScrapedData
from src.services.csv_export import CSV_FIELDNAMES, _join_list, scraped_data_to_csv


def _make_item(**overrides) -> ScrapedData:
    defaults = dict(
        id=uuid4(),
        job_id=uuid4(),
        domain="example.com",
        data_type=DataType.ARTICLE,
        url="https://example.com/post",
        title="Hello world",
        scraped_at=datetime(2024, 1, 1, tzinfo=UTC),
        metadata={},
    )
    defaults.update(overrides)
    return ScrapedData(**defaults)


class TestJoinList:
    def test_joins_list_values_with_semicolon(self):
        assert _join_list({"frameworks": ["React", "Vue"]}, "frameworks") == "React; Vue"

    def test_missing_key_returns_empty_string(self):
        assert _join_list({}, "frameworks") == ""

    def test_non_list_value_returns_empty_string(self):
        assert _join_list({"frameworks": "React"}, "frameworks") == ""


class TestScrapedDataToCsv:
    def test_header_matches_fieldnames(self):
        csv_text = scraped_data_to_csv([_make_item()])
        header = next(csv.reader(io.StringIO(csv_text)))
        assert header == CSV_FIELDNAMES

    def test_empty_list_produces_header_only(self):
        csv_text = scraped_data_to_csv([])
        rows = list(csv.reader(io.StringIO(csv_text)))
        assert len(rows) == 1  # header row only

    def test_flattens_basic_fields(self):
        item = _make_item(domain="acme.com", title="My Post")
        csv_text = scraped_data_to_csv([item])
        rows = list(csv.DictReader(io.StringIO(csv_text)))
        assert len(rows) == 1
        assert rows[0]["domain"] == "acme.com"
        assert rows[0]["title"] == "My Post"
        assert rows[0]["data_type"] == "article"

    def test_flattens_metadata_fields_including_lists(self):
        item = _make_item(
            data_type=DataType.TECH_STACK,
            metadata={
                "platform": "Shopify",
                "frameworks": ["React", "Next.js"],
                "js_libraries": ["jQuery"],
            },
        )
        csv_text = scraped_data_to_csv([item])
        rows = list(csv.DictReader(io.StringIO(csv_text)))
        assert rows[0]["platform"] == "Shopify"
        assert rows[0]["frameworks"] == "React; Next.js"
        assert rows[0]["js_libraries"] == "jQuery"

    def test_handles_multiple_rows_independently(self):
        items = [
            _make_item(domain="a.com", title="A"),
            _make_item(domain="b.com", title="B"),
        ]
        csv_text = scraped_data_to_csv(items)
        rows = list(csv.DictReader(io.StringIO(csv_text)))
        assert [r["domain"] for r in rows] == ["a.com", "b.com"]
        assert [r["title"] for r in rows] == ["A", "B"]

    def test_missing_optional_fields_render_as_empty_strings(self):
        item = _make_item(url=None, title=None, published_date=None)
        csv_text = scraped_data_to_csv([item])
        rows = list(csv.DictReader(io.StringIO(csv_text)))
        assert rows[0]["url"] == ""
        assert rows[0]["title"] == ""
        assert rows[0]["published_date"] == ""
        assert rows[0]["author"] == ""
