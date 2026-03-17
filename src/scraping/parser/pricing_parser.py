"""Parser for extracting pricing plans from B2B pricing pages.

Handles multiple real-world pricing page layouts:
1. Card-based  — .pricing-card / .plan / .tier containers
2. Table-based — <table> rows where each column is a plan
3. Section-based — <section> or <div> blocks anchored by heading + price
4. Text/prose fallback — scan full page text for price+name patterns
"""

import re

from selectolax.parser import HTMLParser


class PricingParser:
    """Extracts pricing plans and details from pricing pages."""

    CURRENCY_PATTERN = re.compile(r"[$€£¥]\s*[\d,]+(?:\.\d{2})?")
    # Matches "free", "contact us", "custom" as plan prices
    FREE_OR_CUSTOM_PATTERN = re.compile(r"\b(free|contact\s+us|custom|get\s+a\s+quote|talk\s+to\s+us)\b", re.IGNORECASE)
    BILLING_PATTERNS = {
        "monthly": [r"month", r"/mo\b", r"per month"],
        "annual": [r"year", r"annual", r"/yr\b", r"per year"],
        "quarterly": [r"quarter", r"/qtr\b"],
    }

    # Card-based selectors — tried in order, first with ≥2 matches wins
    CARD_SELECTORS = [
        ".pricing-card",
        ".pricing-plan",
        ".pricing-tier",
        ".price-card",
        ".plan-card",
        ".plan",
        ".tier",
        ".price-box",
        ".pricing-box",
        ".pricing-column",
        ".pricing-table > div",
        ".pricing-table > li",
        "[class*='pricing'] > div",
        "[class*='plan'] > div",
    ]

    def __init__(self, html: str, base_url: str):
        self.tree = HTMLParser(html)
        self.raw_html = html
        self.base_url = base_url

    def extract_pricing_plans(self) -> list[dict]:
        """Extract pricing plan information from the page using multiple strategies."""

        # Strategy 1: card-based layout
        plans = self._extract_card_based()
        if plans:
            return plans

        # Strategy 2: HTML table layout (each column = one plan)
        plans = self._extract_table_based()
        if plans:
            return plans

        # Strategy 3: section/block layout (heading + price anchored blocks)
        plans = self._extract_section_based()
        if plans:
            return plans

        # Strategy 4: prose fallback — find price mentions and build minimal records
        plans = self._extract_prose_fallback()
        return plans

    # ------------------------------------------------------------------
    # Strategy 1: card-based
    # ------------------------------------------------------------------

    def _extract_card_based(self) -> list[dict]:
        for selector in self.CARD_SELECTORS:
            items = self.tree.css(selector)
            if items and len(items) >= 2:
                plans = [p for p in (self._parse_pricing_card(item) for item in items) if p]
                if plans:
                    return plans
        return []

    def _parse_pricing_card(self, node: object) -> dict | None:
        plan_name = None
        for sel in ["h2", "h3", "h4", ".plan-name", ".tier-name", ".tier-title", ".name", "[class*='name']"]:
            name_node = node.css_first(sel)
            if name_node and name_node.text() and len(name_node.text().strip()) >= 2:
                plan_name = name_node.text().strip()
                break

        if not plan_name:
            return None

        text = node.text() or ""
        price = self._extract_price(text)
        if price is None:
            return None  # Card has no price — skip (likely decorative)

        billing_cycle = self._detect_billing_cycle(text)
        features = self._extract_feature_list(node)
        has_free_trial = bool(re.search(r"free trial|try free", text, re.IGNORECASE))
        cta_text = self._extract_cta(node)

        return {
            "plan_name": plan_name,
            "price": price,
            "billing_cycle": billing_cycle,
            "features": features,
            "has_free_trial": has_free_trial,
            "cta_text": cta_text,
        }

    # ------------------------------------------------------------------
    # Strategy 2: table-based
    # ------------------------------------------------------------------

    def _extract_table_based(self) -> list[dict]:
        """Extract from <table> where the first row contains plan names and
        subsequent rows contain features or prices."""
        plans: list[dict] = []
        for table in self.tree.css("table"):
            rows = table.css("tr")
            if not rows or len(rows) < 2:
                continue

            # First row = header with plan names
            header_cells = rows[0].css("th, td")
            if len(header_cells) < 2:
                continue

            plan_names = [c.text().strip() for c in header_cells if c.text() and c.text().strip()]
            if not plan_names:
                continue

            # Build plan skeletons
            col_plans: list[dict] = [
                {"plan_name": name, "price": None, "billing_cycle": "unknown",
                 "features": [], "has_free_trial": False, "cta_text": None}
                for name in plan_names
            ]

            # Scan remaining rows for prices and features
            for row in rows[1:]:
                cells = row.css("td")
                for i, cell in enumerate(cells):
                    if i >= len(col_plans):
                        break
                    cell_text = cell.text() or ""
                    # Try to extract price if not yet found
                    if col_plans[i]["price"] is None:
                        col_plans[i]["price"] = self._extract_price(cell_text)
                    # Collect as feature
                    if cell_text.strip() and len(cell_text.strip()) > 2:
                        col_plans[i]["features"].append(cell_text.strip())

            # Only return plans that have at least a price
            for plan in col_plans:
                if plan["price"] is not None:
                    plan["features"] = plan["features"][:10]
                    plans.append(plan)

            if plans:
                return plans

        return []

    # ------------------------------------------------------------------
    # Strategy 3: section-based
    # ------------------------------------------------------------------

    def _extract_section_based(self) -> list[dict]:
        """Find sections/divs that each contain a heading + price, treat as plans."""
        plans: list[dict] = []
        for selector in ["section", "article", "[class*='plan']", "[class*='tier']", "[class*='price']"]:
            for block in self.tree.css(selector):
                text = block.text() or ""
                # Must have both a heading-level element and a price
                has_heading = bool(block.css_first("h1, h2, h3, h4"))
                price = self._extract_price(text)
                if not has_heading or price is None:
                    continue

                # Get plan name from first heading
                heading = block.css_first("h1, h2, h3, h4")
                plan_name = heading.text().strip() if heading and heading.text() else None
                if not plan_name or len(plan_name) < 2:
                    continue

                plans.append({
                    "plan_name": plan_name,
                    "price": price,
                    "billing_cycle": self._detect_billing_cycle(text),
                    "features": self._extract_feature_list(block),
                    "has_free_trial": bool(re.search(r"free trial|try free", text, re.IGNORECASE)),
                    "cta_text": self._extract_cta(block),
                })

            if len(plans) >= 2:
                return plans
            plans.clear()

        return []

    # ------------------------------------------------------------------
    # Strategy 4: prose fallback
    # ------------------------------------------------------------------

    def _extract_prose_fallback(self) -> list[dict]:
        """Last resort: scan full page text for currency amounts near plan-like headings."""
        plans: list[dict] = []
        full_text = self.tree.body.text() if self.tree.body else ""
        if not full_text:
            return []

        # Find all price occurrences in the page text
        for match in self.CURRENCY_PATTERN.finditer(full_text):
            price_str = match.group(0)
            # Take up to 200 chars before the price as context
            start = max(0, match.start() - 200)
            context = full_text[start:match.end() + 100]
            billing = self._detect_billing_cycle(context)
            has_free_trial = bool(re.search(r"free trial|try free", context, re.IGNORECASE))
            plans.append({
                "plan_name": f"Plan at {price_str}",
                "price": price_str,
                "billing_cycle": billing,
                "features": [],
                "has_free_trial": has_free_trial,
                "cta_text": None,
            })

        return plans[:6]  # Cap at 6 for prose fallback

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _extract_price(self, text: str) -> str | None:
        """Return the first currency amount found, or free/custom indicator."""
        price_match = self.CURRENCY_PATTERN.search(text)
        if price_match:
            return price_match.group(0)
        free_match = self.FREE_OR_CUSTOM_PATTERN.search(text)
        if free_match:
            return free_match.group(0).strip().lower()
        return None

    def _extract_feature_list(self, node: object) -> list[str]:
        features: list[str] = []
        for ul in node.css("ul"):
            for li in ul.css("li"):
                text = li.text()
                if text and len(text.strip()) > 3:
                    features.append(text.strip())
        return features[:10]

    def _extract_cta(self, node: object) -> str | None:
        cta = node.css_first("button, a.btn, a[class*='cta'], a[class*='button'], .cta")
        if cta and cta.text():
            return cta.text().strip() or None
        return None

    def _detect_billing_cycle(self, text: str) -> str:
        text_lower = text.lower()
        for cycle, patterns in self.BILLING_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, text_lower):
                    return cycle
        return "unknown"
