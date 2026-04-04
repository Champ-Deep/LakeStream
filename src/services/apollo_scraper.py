"""Server-side Apollo.io scraper.

Extracts contacts from Apollo people search results using Playwright with
authenticated sessions. Mirrors the Chrome extension selectors but adds
pagination and server-side automation.

Apollo is less aggressive with anti-bot than LinkedIn, so higher page limits
and shorter delays are safe.
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

DOMAIN = "apollo.io"

# CSS selectors — kept in sync with extension/content/apollo.js
SELECTORS = {
    "table_rows": [
        "tr.zp_cWbgJ",
        "table tbody tr",
        '[data-cy="contacts-table"] tbody tr',
    ],
    "name_cell": [
        "td:first-child a",
        ".zp_xVJ20 a",
        'a[href*="/contacts/"]',
    ],
    "title_cell": [
        "td:nth-child(3)",
        ".zp_Y6y8d",
    ],
    "company_cell": [
        "td:nth-child(4) a",
        'a[href*="/accounts/"]',
    ],
    "email_cell": [
        'td a[href^="mailto:"]',
        '.zp_RFed0 a[href^="mailto:"]',
    ],
    "phone_cell": [
        'td a[href^="tel:"]',
    ],
    "location_cell": [
        "td:nth-child(6)",
        ".zp_Y6y8d:last-child",
    ],
    "linkedin_link": [
        'a[href*="linkedin.com/in/"]',
    ],
    # Additional fields not in original extension
    "company_size_cell": [
        "td:nth-child(5)",
    ],
    "industry_cell": [
        "td:nth-child(7)",
    ],
    # Pagination
    "next_button": [
        'button[aria-label="Next"]',
        'button[aria-label="next"]',
        ".zp_bWS5y:last-child button",
        "button.pagination-next",
    ],
    "page_indicator": [
        ".zp_bWS5y .zp_FGGFx",
        "span.pagination-current",
    ],
}


def _split_name(full_name: str) -> dict[str, str]:
    parts = full_name.split()
    if not parts:
        return {"first_name": "", "last_name": ""}
    if len(parts) == 1:
        return {"first_name": parts[0], "last_name": ""}
    return {"first_name": parts[0], "last_name": " ".join(parts[1:])}


class ApolloScraper:
    """Server-side Apollo.io people search scraper."""

    def __init__(self) -> None:
        self._session_mgr = AuthenticatedSessionManager()

    async def scrape_people_search(
        self,
        search_url: str,
        *,
        max_pages: int = 10,
        cookies: list[dict] | None = None,
    ) -> list[dict]:
        """Scrape contacts from an Apollo people search URL.

        Args:
            search_url: Full Apollo search URL.
            max_pages: Maximum number of result pages to scrape.
            cookies: Optional cookies for authentication.

        Returns:
            List of contact dicts.
        """
        settings = get_settings()

        if cookies:
            await self._session_mgr.create_session(DOMAIN, cookies)

        all_contacts: list[dict] = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=settings.playwright_headless)

            try:
                context, _ = await self._session_mgr.create_browser_context(
                    browser, DOMAIN,
                )
                page = await context.new_page()

                status = await self._session_mgr.navigate_with_stealth(page, search_url)
                if status in (403, 429):
                    log.warning("apollo_blocked", status=status, url=search_url)
                    await browser.close()
                    return []

                if not await self._session_mgr.is_authenticated(page, DOMAIN):
                    log.warning("apollo_not_authenticated", url=search_url)
                    await browser.close()
                    return []

                for page_num in range(1, max_pages + 1):
                    log.info("apollo_scraping_page", page=page_num, max_pages=max_pages)

                    # Wait for table to load
                    await self._wait_for_table(page)

                    contacts = await self._extract_search_page(page)
                    all_contacts.extend(contacts)

                    log.info(
                        "apollo_page_extracted",
                        page=page_num,
                        contacts=len(contacts),
                        total=len(all_contacts),
                    )

                    # Update session
                    storage_state = await context.storage_state()
                    await self._session_mgr.update_session(DOMAIN, storage_state)

                    # Paginate
                    if page_num < max_pages:
                        has_next = await self._click_next_page(page)
                        if not has_next:
                            log.info("apollo_no_more_pages", last_page=page_num)
                            break

                        # Apollo is less aggressive — 1-2s delay is enough
                        await random_delay(1000, 2500)

            finally:
                await browser.close()

        log.info("apollo_scrape_complete", total_contacts=len(all_contacts))
        return all_contacts

    async def scrape_contact_detail(
        self,
        contact_url: str,
        *,
        cookies: list[dict] | None = None,
    ) -> dict | None:
        """Scrape detailed info from an Apollo contact page.

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

                status = await self._session_mgr.navigate_with_stealth(page, contact_url)
                if status in (403, 429):
                    return None

                contact = await self._extract_contact_detail(page, contact_url)

                storage_state = await context.storage_state()
                await self._session_mgr.update_session(DOMAIN, storage_state)

                return contact

            finally:
                await browser.close()

    # ------------------------------------------------------------------
    # Extraction helpers
    # ------------------------------------------------------------------

    async def _extract_search_page(self, page: Page) -> list[dict]:
        """Extract contacts from current search results table."""
        rows = []
        for sel in SELECTORS["table_rows"]:
            rows = await page.query_selector_all(sel)
            if rows:
                break

        contacts = []
        for row in rows:
            try:
                # Name + profile URL
                name_el = None
                for sel in SELECTORS["name_cell"]:
                    name_el = await row.query_selector(sel)
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
                            profile_url = f"https://app.apollo.io{href}"
                        else:
                            profile_url = href

                name_parts = _split_name(full_name)

                # Title
                title = ""
                for sel in SELECTORS["title_cell"]:
                    el = await row.query_selector(sel)
                    if el:
                        title = (await el.inner_text()).strip()
                        break

                # Company
                company = ""
                for sel in SELECTORS["company_cell"]:
                    el = await row.query_selector(sel)
                    if el:
                        company = (await el.inner_text()).strip()
                        break

                # Email
                email = ""
                for sel in SELECTORS["email_cell"]:
                    el = await row.query_selector(sel)
                    if el:
                        href = await el.get_attribute("href")
                        if href and href.startswith("mailto:"):
                            email = href.replace("mailto:", "")
                        else:
                            text = (await el.inner_text()).strip()
                            if "@" in text:
                                email = text
                        break

                # Phone
                phone = ""
                for sel in SELECTORS["phone_cell"]:
                    el = await row.query_selector(sel)
                    if el:
                        href = await el.get_attribute("href")
                        if href and href.startswith("tel:"):
                            phone = href.replace("tel:", "")
                        else:
                            phone = (await el.inner_text()).strip()
                        break

                # Location
                location = ""
                for sel in SELECTORS["location_cell"]:
                    el = await row.query_selector(sel)
                    if el:
                        location = (await el.inner_text()).strip()
                        break

                # LinkedIn URL
                linkedin_url = ""
                for sel in SELECTORS["linkedin_link"]:
                    el = await row.query_selector(sel)
                    if el:
                        linkedin_url = (await el.get_attribute("href")) or ""
                        break

                # Company size (additional field)
                company_size = ""
                for sel in SELECTORS["company_size_cell"]:
                    el = await row.query_selector(sel)
                    if el:
                        company_size = (await el.inner_text()).strip()
                        break

                # Industry (additional field)
                industry = ""
                for sel in SELECTORS["industry_cell"]:
                    el = await row.query_selector(sel)
                    if el:
                        industry = (await el.inner_text()).strip()
                        break

                contacts.append({
                    "first_name": name_parts["first_name"],
                    "last_name": name_parts["last_name"],
                    "name": full_name,
                    "job_title": title,
                    "company": company,
                    "email": email,
                    "phone": phone,
                    "location": location,
                    "linkedin_url": linkedin_url,
                    "profile_url": profile_url,
                    "company_size": company_size,
                    "industry": industry,
                    "source": "apollo_server",
                })
            except Exception as e:
                log.debug("apollo_row_extraction_error", error=str(e))
                continue

        return contacts

    async def _extract_contact_detail(self, page: Page, url: str) -> dict | None:
        """Extract detailed contact info from a contact detail page."""
        # Apollo contact pages have a different layout — get key fields
        name = ""
        try:
            el = await page.query_selector("h1")
            if el:
                name = (await el.inner_text()).strip()
        except Exception:
            pass

        if not name:
            return None

        name_parts = _split_name(name)

        # Extract from structured sections
        title = ""
        company = ""
        email = ""
        phone = ""

        try:
            title_el = await page.query_selector('[data-cy="contact-title"]')
            if title_el:
                title = (await title_el.inner_text()).strip()
        except Exception:
            pass

        try:
            company_el = await page.query_selector('[data-cy="contact-company"] a')
            if company_el:
                company = (await company_el.inner_text()).strip()
        except Exception:
            pass

        try:
            email_el = await page.query_selector('a[href^="mailto:"]')
            if email_el:
                href = await email_el.get_attribute("href")
                if href:
                    email = href.replace("mailto:", "")
        except Exception:
            pass

        try:
            phone_el = await page.query_selector('a[href^="tel:"]')
            if phone_el:
                href = await phone_el.get_attribute("href")
                if href:
                    phone = href.replace("tel:", "")
        except Exception:
            pass

        return {
            "first_name": name_parts["first_name"],
            "last_name": name_parts["last_name"],
            "name": name,
            "job_title": title,
            "company": company,
            "email": email,
            "phone": phone,
            "linkedin_url": url,
            "source": "apollo_server",
        }

    async def _wait_for_table(self, page: Page, timeout: int = 10000) -> None:
        """Wait for the contacts table to appear."""
        for sel in SELECTORS["table_rows"]:
            try:
                await page.wait_for_selector(sel, timeout=timeout)
                return
            except Exception:
                continue

    async def _click_next_page(self, page: Page) -> bool:
        """Click the Next button. Returns False if no next page."""
        for sel in SELECTORS["next_button"]:
            try:
                btn = await page.query_selector(sel)
                if btn:
                    is_disabled = await btn.get_attribute("disabled")
                    if is_disabled:
                        return False

                    await btn.scroll_into_view_if_needed()
                    await random_delay(300, 700)
                    await btn.click()

                    # Wait for table to reload
                    await page.wait_for_load_state("networkidle", timeout=10000)
                    await random_delay(500, 1000)
                    return True
            except Exception:
                continue

        return False
