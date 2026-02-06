from src.templates.base import BaseTemplate
from src.templates.registry import detect_template


async def detect_template_for_domain(html: str, url: str) -> BaseTemplate:
    """Detect the best template for a domain based on its HTML."""
    return detect_template(html, url)
