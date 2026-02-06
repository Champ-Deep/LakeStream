import re

EMAIL_PATTERN = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")

# Common disposable/temp email domains
DISPOSABLE_DOMAINS = {
    "mailinator.com",
    "guerrillamail.com",
    "tempmail.com",
    "throwaway.email",
    "temp-mail.org",
    "10minutemail.com",
    "yopmail.com",
    "sharklasers.com",
    "guerrillamailblock.com",
}


def is_valid_email(email: str) -> bool:
    """Basic email format validation."""
    if not email or not EMAIL_PATTERN.match(email):
        return False

    domain = email.split("@")[1].lower()
    if domain in DISPOSABLE_DOMAINS:
        return False

    # Must have at least one dot in domain part
    if "." not in domain:
        return False

    return True


def is_business_email(email: str) -> bool:
    """Check if email is likely a business email (not free provider)."""
    free_providers = {
        "gmail.com",
        "yahoo.com",
        "hotmail.com",
        "outlook.com",
        "aol.com",
        "icloud.com",
        "mail.com",
        "protonmail.com",
        "zoho.com",
        "yandex.com",
    }

    if not is_valid_email(email):
        return False

    domain = email.split("@")[1].lower()
    return domain not in free_providers
