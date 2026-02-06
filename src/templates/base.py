from abc import ABC, abstractmethod
from urllib.parse import urljoin

from src.models.template import TemplateConfig


class BaseTemplate(ABC):
    @property
    @abstractmethod
    def config(self) -> TemplateConfig: ...

    @abstractmethod
    def detect_platform(self, html: str, url: str) -> bool: ...

    @abstractmethod
    def extract_blog_urls(self, html: str, base_url: str) -> list[str]: ...

    @abstractmethod
    def extract_article(self, html: str, url: str) -> dict: ...

    @abstractmethod
    def extract_contacts(self, html: str, url: str) -> list[dict]: ...

    def resolve_url(self, relative: str, base: str) -> str:
        return urljoin(base, relative)

    def clean_text(self, text: str) -> str:
        return " ".join(text.split()).strip()
