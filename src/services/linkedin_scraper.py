"""Server-side LinkedIn Sales Navigator scraper.

Extracts contacts from Sales Nav search results and profile pages using
Playwright with authenticated sessions. Mirrors the Chrome extension selectors
but adds pagination, anti-detection, and server-side automation.

Usage:
    scraper = LinkedInScraper()
    contacts = await scraper.scrape_search_results(
        "https://www.linkedin.com/sales/search/people?query=...",
        max_pages=5,
        cookies=[...],  # from Chrome extension or settings
    )
"""

from __future__ import annotations

import structlog
from playwright.async_api import Page, async_playwright

from src.config.settings import get_settings
from src.services.session_manager import (
    AuthenticatedSessionManager,
    random_delay,
)

log = structlog.get_logger()

DOMAIN = "linkedin.com"

# Session rotation threshold — after this many requests, rotate session
SESSION_ROTATION_THRESHOLD = 50

# CSS selectors — kept in sync with extension/content/linkedin-sales-nav.js
SELECTORS = {
    "search_result_cards": [
        "li.artdeco-list__item",
        '[data-view-name="search-results-lead-card"]',
        ".search-results__result-item",
    ],
    "name_link": [
        'a[data-control-name="view_lead_panel_via_search_lead_name"]',
        ".result-lockup__name a",
        "span.entity-result__title-text a",
        ".artdeco-entity-lockup__title a",
    ],
    "title": [
        ".result-lockup__highlight-keyword",
        ".artdeco-entity-lockup__subtitle",
        "span.entity-result__primary-subtitle",
    ],
    "company": [
        ".result-lockup__position-company a",
        ".artdeco-entity-lockup__caption a",
        'a[data-control-name="view_lead_panel_via_search_lead_company_name"]',
    ],
    "location": [
        ".result-lockup__misc-item",
        ".artdeco-entity-lockup__metadata",
        "span.entity-result__secondary-subtitle",
    ],
    "headline": [
        ".result-lockup__summary",
        ".artdeco-entity-lockup__content p",
    ],
    # Pagination
    "next_button": [
        'button[aria-label="Next"]',
        "button.artdeco-pagination__button--next",
        'li.artdeco-pagination__indicator--number:last-child button',
    ],
    "page_indicator": [
        "li.artdeco-pagination__indicator--number.selected",
        ".search-results__pagination-text",
    ],
    # Profile page
    "profile_name": [
        ".profile-topcard-person-entity__name",
        "h1.inline.t-24",
        ".top-card-layout__title",
    ],
    "profile_title": [
        ".profile-topcard__summary-position",
        ".profile-topcard-person-entity__title",
    ],
    "profile_company": [
        ".profile-topcard__summary-position-company",
        ".profile-topcard-person-entity__company a",
    ],
    "profile_location": [
        ".profile-topcard__location-data",
        ".profile-topcard-person-entity__location",
    ],
    "profile_about": [
        ".profile-topcard__summary-content",
        ".pv-about__summary-text",
    ],
    "profile_connections": [
        ".profile-topcard__connections-data",
        "span.t-bold:has(+ span:text-is('connections'))",
    ],
}


async def _query(page: Page, selector_list: list[str]) -> str:
    """Try multiple selectors, return text of first match."""
    for sel in selector_list:
        try:
            el = await page.query_selector(sel)
            if el:
                text = await el.inner_text()
                return text.strip()
        except Exception:
            continue
    return ""


async def _query_attr(page: Page, selector_list: list[str], attr: str) -> str:
    """Try multiple selectors, return attribute of first match."""
    for sel in selector_list:
        try:
            el = await page.query_selector(sel)
            if el:
                val = await el.get_attribute(attr)
                return (val or "").strip()
        except Exception:
            continue
    return ""


async def _query_all_within(
    page: Page, parent_selector_list: list[str]
) -> list:
    """Find all elements matching any of the selectors."""
    for sel in parent_selector_list:
        try:
            els = await page.query_selector_all(sel)
            if els:
                return els
        except Exception:
            continue
    return []


def _split_name(full_name: str) -> dict[str, str]:
    parts = full_name.split()
    if not parts:
        return {"first_name": "", "last_name": ""}
    if len(parts) == 1:
        return {"first_name": parts[0], "last_name": ""}
    return {"first_name": parts[0], "last_name": " ".join(parts[1:])}


class LinkedInScraper:
    """Server-side LinkedIn Sales Navigator scraper."""

    def __init__(self) -> None:
        self._session_mgr = AuthenticatedSessionManager()

    async def scrape_search_results(
        self,
        search_url: str,
        *,
        max_pages: int = 5,
        cookies: list[dict] | None = None,
    ) -> list[dict]:
        """Scrape contacts from a Sales Navigator search URL.

        Args:
            search_url: Full Sales Navigator search URL.
            max_pages: Maximum number of result pages to scrape.
            cookies: Optional cookies for authentication. If not provided,
                     uses existing session from Redis or settings.

        Returns:
            List of contact dicts with fields matching the Chrome extension format.
        """
        settings = get_settings()

        # Set up session if cookies provided
        if cookies:
            await self._session_mgr.create_session(DOMAIN, cookies)
        elif settings.linkedin_session_cookies:
            import json
            try:
                parsed_cookies = json.loads(settings.linkedin_session_cookies)
                await self._session_mgr.create_session(DOMAIN, parsed_cookies)
            except (json.JSONDecodeError, TypeError):
                log.warning("invalid_linkedin_session_cookies")

        all_contacts: list[dict] = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=settings.playwright_headless)

            try:
                context, session_data = await self._session_mgr.create_browser_context(
                    browser, DOMAIN,
                )
                page = await context.new_page()

                # Navigate to search page
                status = await self._session_mgr.navigate_with_stealth(page, search_url)
                if status in (403, 429, 999):
                    log.warning("linkedin_blocked", status=status, url=search_url)
                    await browser.close()
                    return []

                # Check auth
                if not await self._session_mgr.is_authenticated(page, DOMAIN):
                    log.warning("linkedin_not_authenticated", url=search_url)
                    await browser.close()
                    return []

                # Extract from each page
                for page_num in range(1, max_pages + 1):
                    log.info(
                        "linkedin_scraping_page",
                        page=page_num,
                        max_pages=max_pages,
                    )

                    contacts = await self._extract_search_page(page)
                    all_contacts.extend(contacts)

                    log.info(
                        "linkedin_page_extracted",
                        page=page_num,
                        contacts=len(contacts),
                        total=len(all_contacts),
                    )

                    # Update session
                    storage_state = await context.storage_state()
                    await self._session_mgr.update_session(DOMAIN, storage_state)

                    # Check session rotation
                    session = await self._session_mgr.get_session(DOMAIN)
                    if session and session.get("request_count", 0) >= SESSION_ROTATION_THRESHOLD:
                        log.info("linkedin_session_rotation", requests=session["request_count"])
                        break

                    # Paginate
                    if page_num < max_pages:
                        has_next = await self._click_next_page(page)
                        if not has_next:
                            log.info("linkedin_no_more_pages", last_page=page_num)
                            break

                        # Human-like delay between pages (3-5s for LinkedIn)
                        await random_delay(3000, 5000)

            finally:
                await browser.close()

        log.info("linkedin_scrape_complete", total_contacts=len(all_contacts))
        return all_contacts

    async def scrape_profile(
        self,
        profile_url: str,
        *,
        cookies: list[dict] | None = None,
    ) -> dict | None:
        """Scrape a single Sales Navigator profile page.

        Returns:
            Contact dict or None if extraction failed.
        """
        settings = get_settings()

        if cookies:
            await self._session_mgr.create_session(DOMAIN, cookies)

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=settings.playwright_headless)

            try:
                context, _ = await self._session_mgr.create_browser_context(
                    browser, DOMAIN,
                )
                page = await context.new_page()

                status = await self._session_mgr.navigate_with_stealth(page, profile_url)
                if status in (403, 429, 999):
                    return None

                contact = await self._extract_profile(page, profile_url)

                # Update session
                storage_state = await context.storage_state()
                await self._session_mgr.update_session(DOMAIN, storage_state)

                return contact

            finally:
                await browser.close()

    # ------------------------------------------------------------------
    # Extraction helpers
    # ------------------------------------------------------------------

    async def _extract_search_page(self, page: Page) -> list[dict]:
        """Extract contacts from current search results page."""
        # Scroll to load all results
        await self._scroll_to_load_all(page)

        cards = await _query_all_within(page, SELECTORS["search_result_cards"])
        contacts = []

        for card in cards:
            try:
                # Name + profile URL
                name_el = None
                for sel in SELECTORS["name_link"]:
                    name_el = await card.query_selector(sel)
                    if name_el:
                        break

                full_name = (await name_el.inner_text()).strip() if name_el else ""
                if not full_name:
                    continue

                profile_url = ""
                if name_el:
                    href = await name_el.get_attribute("href")
                    if href:
                        if href.startswith("/"):
                            profile_url = f"https://www.linkedin.com{href}"
                        else:
                            profile_url = href

                name_parts = _split_name(full_name)

                # Title
                title = ""
                for sel in SELECTORS["title"]:
                    el = await card.query_selector(sel)
                    if el:
                        title = (await el.inner_text()).strip()
                        break

                # Company
                company = ""
                for sel in SELECTORS["company"]:
                    el = await card.query_selector(sel)
                    if el:
                        company = (await el.inner_text()).strip()
                        break

                # Location
                location = ""
                for sel in SELECTORS["location"]:
                    el = await card.query_selector(sel)
                    if el:
                        location = (await el.inner_text()).strip()
                        break

                # Headline
                headline = ""
                for sel in SELECTORS["headline"]:
                    el = await card.query_selector(sel)
                    if el:
                        headline = (await el.inner_text()).strip()
                        break

                contacts.append({
                    "first_name": name_parts["first_name"],
                    "last_name": name_parts["last_name"],
                    "name": full_name,
                    "job_title": title,
                    "company": company,
                    "location": location,
                    "headline": headline,
                    "linkedin_url": profile_url,
                    "source": "linkedin_sales_nav_server",
                })
            except Exception as e:
                log.debug("linkedin_card_extraction_error", error=str(e))
                continue

        return contacts

    async def _extract_profile(self, page: Page, url: str) -> dict | None:
        """Extract contact from a profile page."""
        full_name = await _query(page, SELECTORS["profile_name"])
        if not full_name:
            return None

        name_parts = _split_name(full_name)

        return {
            "first_name": name_parts["first_name"],
            "last_name": name_parts["last_name"],
            "name": full_name,
            "job_title": await _query(page, SELECTORS["profile_title"]),
            "company": await _query(page, SELECTORS["profile_company"]),
            "location": await _query(page, SELECTORS["profile_location"]),
            "about": await _query(page, SELECTORS["profile_about"]),
            "linkedin_url": url,
            "source": "linkedin_sales_nav_server",
        }

    async def _scroll_to_load_all(self, page: Page) -> None:
        """Scroll down incrementally to trigger lazy loading of all results."""
        for _ in range(5):
            await page.evaluate("window.scrollBy(0, 600)")
            await random_delay(300, 700)

        # Scroll back to top
        await page.evaluate("window.scrollTo(0, 0)")
        await random_delay(200, 500)

    async def _click_next_page(self, page: Page) -> bool:
        """Click the Next button. Returns False if no next page."""
        for sel in SELECTORS["next_button"]:
            try:
                btn = await page.query_selector(sel)
                if btn:
                    is_disabled = await btn.get_attribute("disabled")
                    if is_disabled:
                        return False

                    # Human-like: move to element area then click
                    await btn.scroll_into_view_if_needed()
                    await random_delay(500, 1000)
                    await btn.click()

                    # Wait for new results to load
                    await page.wait_for_load_state("networkidle", timeout=15000)
                    await random_delay(1000, 2000)
                    return True
            except Exception:
                continue

        return False
