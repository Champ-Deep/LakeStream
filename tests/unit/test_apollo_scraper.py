"""Tests for Apollo.io server-side scraper."""

from src.services.apollo_scraper import SELECTORS, _split_name


class TestSplitName:
    def test_empty_name(self):
        assert _split_name("") == {"first_name": "", "last_name": ""}

    def test_single_name(self):
        assert _split_name("Jane") == {"first_name": "Jane", "last_name": ""}

    def test_two_names(self):
        assert _split_name("Jane Smith") == {"first_name": "Jane", "last_name": "Smith"}

    def test_compound_last_name(self):
        assert _split_name("Jane Van Der Smith") == {
            "first_name": "Jane",
            "last_name": "Van Der Smith",
        }


class TestSelectors:
    """Verify selector lists are present and non-empty."""

    def test_table_selectors_exist(self):
        assert len(SELECTORS["table_rows"]) > 0
        assert len(SELECTORS["name_cell"]) > 0
        assert len(SELECTORS["title_cell"]) > 0
        assert len(SELECTORS["company_cell"]) > 0

    def test_contact_detail_selectors_exist(self):
        assert len(SELECTORS["email_cell"]) > 0
        assert len(SELECTORS["phone_cell"]) > 0

    def test_pagination_selectors_exist(self):
        assert len(SELECTORS["next_button"]) > 0

    def test_additional_field_selectors_exist(self):
        """New fields added beyond original extension."""
        assert "company_size_cell" in SELECTORS
        assert "industry_cell" in SELECTORS
        assert len(SELECTORS["company_size_cell"]) > 0
        assert len(SELECTORS["industry_cell"]) > 0

    def test_linkedin_link_selector_exists(self):
        assert len(SELECTORS["linkedin_link"]) > 0
