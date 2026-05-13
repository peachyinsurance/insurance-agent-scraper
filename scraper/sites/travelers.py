"""Site adapter for Travelers' public agent directory at agent.travelers.com."""

import json
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from scraper.records import AgencyRecord


# --- URL building / list parsing --------------------------------------------

def state_url(state_slug: str) -> str:
    """Build the Travelers state-landing URL. Slug is the 2-letter postal code."""
    return f"https://agent.travelers.com/{state_slug.lower()}"


def parse_state_page(html: str, state_slug: str) -> list[str]:
    """Return sorted, deduplicated list of city-page URLs from a state page."""
    soup = BeautifulSoup(html, "html.parser")
    base = state_url(state_slug)
    expected_prefix = f"/{state_slug.lower()}/"
    city_urls: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href or href.startswith("#"):
            continue
        absolute = urljoin(base, href)
        parsed = urlparse(absolute)
        if parsed.netloc != "agent.travelers.com":
            continue
        if not parsed.path.startswith(expected_prefix):
            continue
        remainder = parsed.path[len(expected_prefix):].rstrip("/")
        if not remainder or "/" in remainder:
            continue
        city_urls.add(f"https://agent.travelers.com{parsed.path.rstrip('/')}")

    return sorted(city_urls)


def parse_city_page(html: str, city_url: str) -> list[str]:
    """Return sorted, deduplicated list of agency detail URLs from a city page."""
    soup = BeautifulSoup(html, "html.parser")
    city_path = urlparse(city_url).path.rstrip("/")
    expected_prefix = f"{city_path}/"
    agency_urls: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href or href.startswith("#"):
            continue
        absolute = urljoin(city_url, href)
        parsed = urlparse(absolute)
        if parsed.netloc != "agent.travelers.com":
            continue
        if not parsed.path.startswith(expected_prefix):
            continue
        remainder = parsed.path[len(expected_prefix):].rstrip("/")
        if not remainder or "/" in remainder:
            continue
        agency_urls.add(f"https://agent.travelers.com{parsed.path.rstrip('/')}")

    return sorted(agency_urls)


# --- agency detail parsing --------------------------------------------------

def _clean(value):
    """Collapse internal whitespace (including newlines) and strip ends."""
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


def _format_phone(raw):
    """Normalize a phone number to '(NXX) NXX-XXXX'. Returns '' for clearly broken input."""
    if not raw or not isinstance(raw, str):
        return ""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return ""
    return f"({digits[0:3]}) {digits[3:6]}-{digits[6:10]}"


def _agency_website_or_empty(url):
    """Return url if it points to a real agency website, '' if it's a Travelers self-link."""
    if not isinstance(url, str) or not url:
        return ""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if "travelers.com" in host:
        return ""
    return url


def _extract_agency_jsonld(soup):
    """Find the best structured-data block for an agency on a Travelers page.

    Prefers the Yext verifiable credential (credentialSubject) because it has
    email + the agency's real website URL. Falls back to the schema.org
    InsuranceAgency block (no email; its url points to Travelers itself).
    Returns the dict, or {} if neither is present.
    """
    yext_subject = None
    insurance_agency = None

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue

        # Yext verifiable credential
        if isinstance(data, dict) and isinstance(data.get("credentialSubject"), dict):
            subject = data["credentialSubject"]
            if subject.get("name") and isinstance(subject.get("address"), dict):
                yext_subject = subject
                continue  # keep looking in case there's anything later, but VC wins

        # Schema.org InsuranceAgency (may be wrapped in @graph)
        if isinstance(data, dict):
            graph = data.get("@graph")
            items = graph if isinstance(graph, list) else [data]
        elif isinstance(data, list):
            items = data
        else:
            continue

        for item in items:
            if (
                isinstance(item, dict)
                and item.get("@type") == "InsuranceAgency"
                and item.get("name")
                and isinstance(item.get("address"), dict)
            ):
                insurance_agency = item
                break

    return yext_subject or insurance_agency or {}


def parse_agency_page(html: str, source_url: str) -> AgencyRecord:
    """Extract fields from one Travelers agency detail page.

    Reads the Yext verifiable credential when present (gives email + real
    agency website URL); falls back to the InsuranceAgency JSON-LD which
    lacks those fields. If neither is present, returns a record with only
    source_url populated.
    """
    soup = BeautifulSoup(html, "html.parser")
    data = _extract_agency_jsonld(soup)
    address = data.get("address") or {}

    return AgencyRecord(
        source_url=source_url,
        agency_name=_clean(data.get("name")),
        address_line=_clean(address.get("streetAddress")),
        city=_clean(address.get("addressLocality")),
        state=_clean(address.get("addressRegion")),
        zip=_clean(address.get("postalCode")),
        phone=_format_phone(data.get("telephone")),
        website_url=_agency_website_or_empty(data.get("url")),
        email=_clean(data.get("email")).lower(),
    )