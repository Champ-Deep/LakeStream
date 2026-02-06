class ScrapeError(Exception):
    """Base exception for scraping errors."""

    def __init__(self, message: str, domain: str = "", url: str = ""):
        self.domain = domain
        self.url = url
        super().__init__(message)


class BlockedError(ScrapeError):
    """Raised when a request is blocked (403, 429, CAPTCHA)."""

    def __init__(self, message: str, status_code: int = 0, **kwargs: str):
        self.status_code = status_code
        super().__init__(message, **kwargs)


class CaptchaError(BlockedError):
    """Raised when a CAPTCHA challenge is detected."""


class TemplateNotFoundError(ScrapeError):
    """Raised when no matching template is found for a domain."""


class FetchError(ScrapeError):
    """Raised when a page fetch fails after all retries."""

    def __init__(self, message: str, tier: str = "", **kwargs: str):
        self.tier = tier
        super().__init__(message, **kwargs)


class ExportError(ScrapeError):
    """Raised when data export to database fails."""
