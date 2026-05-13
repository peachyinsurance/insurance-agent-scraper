"""Small text helpers shared across site adapters and the enrichment layer.

Names are unprefixed (no leading underscore) because they're public API of the
core package — callers in other modules import them directly.
"""

import re


def clean(value: str) -> str:
    """Collapse internal whitespace (including newlines) and strip ends.

    Returns '' for None or non-string input. Useful for normalizing values
    pulled out of JSON-LD, where street addresses might contain '\\n' or
    runs of spaces (e.g. '5 Concourse Pkwy\\nSte 2700' -> '5 Concourse Pkwy Ste 2700').
    """
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


def format_phone(raw: str) -> str:
    """Normalize a US phone number to '(NXX) NXX-XXXX'.

    Handles inputs like '+14046336332', '404-633-6332', '4046336332', and
    '(404) 633-6332'. Returns '' for clearly broken input (fewer or more
    digits than 10, or non-string input) — better than echoing garbage
    like '+' through to the CSV.
    """
    if not raw or not isinstance(raw, str):
        return ""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return ""
    return f"({digits[0:3]}) {digits[3:6]}-{digits[6:10]}"


def looks_like_email(value: str) -> bool:
    """Cheap sanity check: a non-empty string with @ and a dot in the domain.

    Not a full RFC 5322 validator — just enough to filter obvious garbage
    like 'foo@bar' (no TLD) or strings without an @.
    """
    if not value or "@" not in value:
        return False
    local, _, domain = value.partition("@")
    return bool(local) and "." in domain
