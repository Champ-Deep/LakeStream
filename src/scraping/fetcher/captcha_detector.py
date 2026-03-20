"""Shared CAPTCHA/bot-check detection for all fetcher tiers.

Conservative patterns — only triggers on specific, unambiguous markers
to avoid false positives on pages that merely mention CAPTCHAs.
"""

import re

_CAPTCHA_PATTERNS = [
    # reCAPTCHA
    re.compile(r'<script[^>]+src=["\'][^"\']*recaptcha', re.I),
    re.compile(r'class=["\'][^"\']*g-recaptcha', re.I),
    re.compile(r"data-sitekey=", re.I),
    # hCaptcha
    re.compile(r'<script[^>]+src=["\'][^"\']*hcaptcha', re.I),
    re.compile(r'class=["\'][^"\']*h-captcha', re.I),
    # Cloudflare challenge
    re.compile(r"<title[^>]*>\s*Just a moment", re.I),
    re.compile(r"cf-challenge-running|challenge-form", re.I),
    # Cloudflare Turnstile
    re.compile(r'class=["\'][^"\']*cf-turnstile', re.I),
    # PerimeterX
    re.compile(r"px-captcha|_pxCaptcha", re.I),
    # DataDome
    re.compile(r'<script[^>]+src=["\'][^"\']*datadome', re.I),
]


def detect_captcha(html: str) -> bool:
    """Detect CAPTCHA/bot-check pages by scanning for known markers.

    Returns True only when specific CAPTCHA DOM elements or challenge scripts
    are found — NOT for pages that merely mention the word 'captcha'.
    """
    return any(p.search(html) for p in _CAPTCHA_PATTERNS)
