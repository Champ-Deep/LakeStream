"""Tests for LinkedIn Sales Navigator server-side scraper."""

from src.services.linkedin_scraper import SELECTORS, _split_name


class TestSplitName:
    def test_empty_name(self):
        assert _split_name("") == {"first_name": "", "last_name": ""}

    def test_single_name(self):
        assert _split_name("John") == {"first_name": "John", "last_name": ""}

    def test_two_names(self):
        assert _split_name("John Doe") == {"first_name": "John", "last_name": "Doe"}

    def test_three_names(self):
        assert _split_name("John Van Doe") == {"first_name": "John", "last_name": "Van Doe"}

    def test_extra_whitespace(self):
        assert _split_name("  John   Doe  ") == {"first_name": "John", "last_name": "Doe"}


class TestSelectors:
    """Verify selector lists are present and non-empty."""

    def test_search_selectors_exist(self):
        assert len(SELECTORS["search_result_cards"]) > 0
        assert len(SELECTORS["name_link"]) > 0
        assert len(SELECTORS["title"]) > 0
        assert len(SELECTORS["company"]) > 0

    def test_pagination_selectors_exist(self):
        assert len(SELECTORS["next_button"]) > 0

    def test_profile_selectors_exist(self):
        assert len(SELECTORS["profile_name"]) > 0
        assert len(SELECTORS["profile_title"]) > 0
        assert len(SELECTORS["profile_company"]) > 0

    def test_headline_selectors_exist(self):
        """Headline selectors are new — verify they were added."""
        assert "headline" in SELECTORS
        assert len(SELECTORS["headline"]) > 0
