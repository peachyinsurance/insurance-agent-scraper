"""Tests for scraper.core.text — clean, format_phone, looks_like_email."""

from scraper.core.text import clean, format_phone, looks_like_email


# --- clean -----------------------------------------------------------------

def test_clean_strips_leading_and_trailing_whitespace():
    assert clean("  hello  ") == "hello"


def test_clean_collapses_internal_runs_of_whitespace():
    assert clean("a    b   c") == "a b c"


def test_clean_collapses_newlines():
    # Real case: Travelers JSON-LD streetAddress like "5 Concourse Pkwy\nSte 2700"
    assert clean("5 Concourse Pkwy\nSte 2700") == "5 Concourse Pkwy Ste 2700"


def test_clean_handles_mixed_whitespace():
    assert clean("foo\t bar\n\nbaz") == "foo bar baz"


def test_clean_returns_empty_for_none():
    assert clean(None) == ""


def test_clean_returns_empty_for_non_string():
    assert clean(123) == ""
    assert clean([]) == ""


def test_clean_returns_empty_for_empty_string():
    assert clean("") == ""
    assert clean("   ") == ""


# --- format_phone ----------------------------------------------------------

def test_format_phone_standard_10_digit():
    assert format_phone("4046336332") == "(404) 633-6332"


def test_format_phone_with_country_code():
    assert format_phone("14046336332") == "(404) 633-6332"
    assert format_phone("+14046336332") == "(404) 633-6332"


def test_format_phone_strips_punctuation():
    assert format_phone("(404) 633-6332") == "(404) 633-6332"
    assert format_phone("404-633-6332") == "(404) 633-6332"
    assert format_phone("404.633.6332") == "(404) 633-6332"


def test_format_phone_returns_empty_for_garbage():
    assert format_phone("+") == ""
    assert format_phone("abc") == ""
    assert format_phone("12345") == ""        # too few digits
    assert format_phone("1234567890123") == ""  # too many digits


def test_format_phone_returns_empty_for_empty_or_none():
    assert format_phone("") == ""
    assert format_phone(None) == ""


def test_format_phone_returns_empty_for_non_string():
    assert format_phone(4046336332) == ""


# --- looks_like_email ------------------------------------------------------

def test_looks_like_email_accepts_standard_address():
    assert looks_like_email("foo@bar.com") is True


def test_looks_like_email_accepts_plus_in_local_part():
    assert looks_like_email("foo+sales@bar.com") is True


def test_looks_like_email_accepts_subdomain():
    assert looks_like_email("foo@mail.bar.com") is True


def test_looks_like_email_rejects_no_at():
    assert looks_like_email("foobar.com") is False


def test_looks_like_email_rejects_no_tld():
    assert looks_like_email("foo@bar") is False


def test_looks_like_email_rejects_empty_local_part():
    assert looks_like_email("@bar.com") is False


def test_looks_like_email_rejects_empty_or_none():
    assert looks_like_email("") is False
    assert looks_like_email(None) is False
