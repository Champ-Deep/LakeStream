from src.scraping.validator.email_validator import is_business_email, is_valid_email


def test_valid_email():
    assert is_valid_email("user@example.com")


def test_invalid_email_no_at():
    assert not is_valid_email("userexample.com")


def test_invalid_email_no_domain():
    assert not is_valid_email("user@")


def test_invalid_email_disposable():
    assert not is_valid_email("user@mailinator.com")


def test_empty_email():
    assert not is_valid_email("")


def test_business_email():
    assert is_business_email("john@acmecorp.com")


def test_free_email_not_business():
    assert not is_business_email("john@gmail.com")
    assert not is_business_email("john@yahoo.com")
    assert not is_business_email("john@hotmail.com")
