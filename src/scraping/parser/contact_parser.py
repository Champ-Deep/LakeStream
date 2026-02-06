import json
import re

from selectolax.parser import HTMLParser


class ContactParser:
    """Extracts contact and people information from HTML pages."""

    EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
    PHONE_PATTERN = re.compile(r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}")
    LINKEDIN_PATTERN = re.compile(r"https?://(?:www\.)?linkedin\.com/in/[\w-]+")

    # Generic emails to skip
    GENERIC_EMAILS = {
        "info@",
        "support@",
        "sales@",
        "help@",
        "admin@",
        "contact@",
        "hello@",
        "noreply@",
        "no-reply@",
        "webmaster@",
        "privacy@",
        "legal@",
    }

    def __init__(self, html: str, base_url: str):
        self.tree = HTMLParser(html)
        self.base_url = base_url
        self.text = self.tree.body.text() if self.tree.body else ""

    def extract_people(self) -> list[dict]:
        """Extract people from the page using multiple strategies."""
        people: list[dict] = []

        # Strategy 1: JSON-LD structured data
        people.extend(self._from_json_ld())

        # Strategy 2: Common team page card patterns
        people.extend(self._from_team_cards())

        # Strategy 3: Email + context extraction
        if not people:
            people.extend(self._from_email_patterns())

        return self._deduplicate(people)

    def _from_json_ld(self) -> list[dict]:
        """Extract people from JSON-LD structured data."""
        people: list[dict] = []
        for script in self.tree.css('script[type="application/ld+json"]'):
            try:
                data = json.loads(script.text() or "{}")
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if item.get("@type") == "Person":
                        name_parts = (item.get("name", "")).split(" ", 1)
                        people.append(
                            {
                                "first_name": name_parts[0] if name_parts else None,
                                "last_name": name_parts[1] if len(name_parts) > 1 else None,
                                "job_title": item.get("jobTitle"),
                                "email": item.get("email"),
                                "linkedin_url": item.get("sameAs"),
                                "source": "json_ld",
                            }
                        )
            except (json.JSONDecodeError, AttributeError):
                continue
        return people

    def _from_team_cards(self) -> list[dict]:
        """Extract people from common team page card patterns."""
        people: list[dict] = []
        card_selectors = [
            ".team-member",
            ".staff-card",
            ".person",
            ".bio-card",
            ".team-card",
            ".leadership-card",
            ".member-card",
            ".about-team .card",
        ]

        for selector in card_selectors:
            cards = self.tree.css(selector)
            if not cards:
                continue

            for card in cards:
                name = None
                title = None

                # Try to find name
                for name_sel in ["h3", "h4", ".name", ".member-name", "strong"]:
                    name_node = card.css_first(name_sel)
                    if name_node and name_node.text():
                        name = name_node.text().strip()
                        break

                # Try to find title
                for title_sel in [".title", ".position", ".role", ".job-title", "p"]:
                    title_node = card.css_first(title_sel)
                    if title_node and title_node.text():
                        title = title_node.text().strip()
                        break

                if name:
                    parts = name.split(" ", 1)
                    # Find LinkedIn link in card
                    linkedin = None
                    for a in card.css("a"):
                        href = a.attributes.get("href", "")
                        if "linkedin.com/in/" in href:
                            linkedin = href
                            break

                    people.append(
                        {
                            "first_name": parts[0],
                            "last_name": parts[1] if len(parts) > 1 else None,
                            "job_title": title,
                            "linkedin_url": linkedin,
                            "source": "team_page",
                        }
                    )
            if people:
                break  # Found cards with one selector, stop trying others

        return people

    def _from_email_patterns(self) -> list[dict]:
        """Extract emails from page text, filtering out generic ones."""
        people: list[dict] = []
        emails = self.EMAIL_PATTERN.findall(self.text)

        for email in emails:
            if any(email.lower().startswith(prefix) for prefix in self.GENERIC_EMAILS):
                continue
            people.append(
                {
                    "email": email,
                    "source": "email_pattern",
                }
            )

        # Also extract LinkedIn URLs
        linkedins = self.LINKEDIN_PATTERN.findall(self.text)
        for linkedin in linkedins:
            people.append(
                {
                    "linkedin_url": linkedin,
                    "source": "linkedin_pattern",
                }
            )

        return people

    def _deduplicate(self, people: list[dict]) -> list[dict]:
        """Merge duplicate entries based on email or name."""
        seen_emails: dict[str, int] = {}
        seen_names: dict[str, int] = {}
        result: list[dict] = []

        for person in people:
            email = person.get("email", "").lower()
            name = f"{person.get('first_name', '')} {person.get('last_name', '')}".strip().lower()

            if email and email in seen_emails:
                # Merge into existing
                idx = seen_emails[email]
                for k, v in person.items():
                    if v and not result[idx].get(k):
                        result[idx][k] = v
            elif name and name in seen_names:
                idx = seen_names[name]
                for k, v in person.items():
                    if v and not result[idx].get(k):
                        result[idx][k] = v
            else:
                idx = len(result)
                result.append(person)
                if email:
                    seen_emails[email] = idx
                if name:
                    seen_names[name] = idx

        return result
