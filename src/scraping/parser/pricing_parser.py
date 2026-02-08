"""Parser for extracting pricing plans from B2B pricing pages."""

import re

from selectolax.parser import HTMLParser


class PricingParser:
    """Extracts pricing plans and details from pricing pages."""

    CURRENCY_PATTERN = re.compile(r'[$€£¥]\s*[\d,]+(?:\.\d{2})?')
    BILLING_PATTERNS = {
        "monthly": [r"month", r"/mo", r"per month"],
        "annual": [r"year", r"annual", r"/yr", r"per year"],
        "quarterly": [r"quarter", r"/qtr"],
    }

    def __init__(self, html: str, base_url: str):
        self.tree = HTMLParser(html)
        self.base_url = base_url

    def extract_pricing_plans(self) -> list[dict]:
        """Extract pricing plan information from the page."""
        plans: list[dict] = []

        # Try to find pricing cards/tiers
        for selector in [
            ".pricing-card",
            ".plan",
            ".tier",
            ".price-box",
            ".pricing-table > div",
            ".pricing-column",
        ]:
            items = self.tree.css(selector)
            if items and len(items) >= 2:  # At least 2 plans
                for item in items:
                    plan = self._parse_pricing_card(item)
                    if plan:
                        plans.append(plan)
                if plans:
                    break

        return plans

    def _parse_pricing_card(self, node: object) -> dict | None:
        """Parse a single pricing plan card."""
        # Extract plan name
        plan_name = None
        for sel in ["h2", "h3", "h4", ".plan-name", ".tier-title", ".name"]:
            name_node = node.css_first(sel)
            if name_node and name_node.text():
                plan_name = name_node.text().strip()
                break

        if not plan_name or len(plan_name) < 2:
            return None

        # Extract price
        text = node.text() or ""
        price_match = self.CURRENCY_PATTERN.search(text)
        price = price_match.group(0) if price_match else None

        # Detect billing cycle
        billing_cycle = self._detect_billing_cycle(text)

        # Extract features
        features = []
        feature_list = node.css_first("ul")
        if feature_list:
            for li in feature_list.css("li"):
                feature_text = li.text()
                if feature_text and len(feature_text) > 3:
                    features.append(feature_text.strip())

        # Check for CTA
        has_free_trial = bool(re.search(r"free trial|try free", text, re.IGNORECASE))
        cta_text = None
        cta_button = node.css_first("button, .cta, a.btn")
        if cta_button:
            cta_text = cta_button.text().strip() if cta_button.text() else None

        return {
            "plan_name": plan_name,
            "price": price,
            "billing_cycle": billing_cycle,
            "features": features[:10],  # Limit to top 10
            "has_free_trial": has_free_trial,
            "cta_text": cta_text,
        }

    def _detect_billing_cycle(self, text: str) -> str:
        """Detect billing cycle from text."""
        text_lower = text.lower()
        for cycle, patterns in self.BILLING_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, text_lower):
                    return cycle
        return "unknown"
