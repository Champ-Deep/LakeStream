from src.models.template import TemplateConfig
from src.templates.base import BaseTemplate
from src.templates.directory import DirectoryTemplate
from src.templates.generic import GenericTemplate
from src.templates.hubspot import HubSpotTemplate
from src.templates.webflow import WebflowTemplate
from src.templates.wordpress import WordPressTemplate

# Registry of all templates, ordered by detection priority
_TEMPLATES: list[BaseTemplate] = [
    WordPressTemplate(),
    HubSpotTemplate(),
    WebflowTemplate(),
    DirectoryTemplate(),
    GenericTemplate(),  # Always last — fallback
]

_TEMPLATE_MAP: dict[str, BaseTemplate] = {t.config.id: t for t in _TEMPLATES}


def detect_template(html: str, url: str) -> BaseTemplate:
    """Auto-detect which template matches the given HTML."""
    for template in _TEMPLATES:
        if template.config.id == "generic":
            continue  # Skip generic during detection
        if template.detect_platform(html, url):
            return template
    return _TEMPLATE_MAP["generic"]


def get_template(template_id: str) -> TemplateConfig | None:
    """Get a template config by ID."""
    template = _TEMPLATE_MAP.get(template_id)
    return template.config if template else None


def get_template_instance(template_id: str) -> BaseTemplate | None:
    """Get a template instance by ID."""
    return _TEMPLATE_MAP.get(template_id)


def list_templates() -> list[TemplateConfig]:
    """List all available template configs."""
    return [t.config for t in _TEMPLATES]
