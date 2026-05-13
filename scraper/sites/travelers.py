"""Site adapter for Travelers' public agent directory at agent.travelers.com."""

from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from scraper.core.jsonld import iter_jsonld_blocks
from scraper.core.text import clean, format_phone
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

def _agency_website_or_empty(url: str) -> str:
    """Return url if it points to a real agency website, '' if it's a Travelers self-link.

    Travelers-specific filter: the InsuranceAgency JSON-LD block's `url` field
    points at Travelers' own directory page, not the agency's website. We don't
    want that bleeding into the `website` column on Stage 2 output.
    """
    if not isinstance(url, str) or not url:
        return ""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if "travelers.com" in host:
        return ""
    return url


def _extract_agency_jsonld(soup) -> dict:
    """Pick the best structured-data block for an agency on a Travelers page.

    Travelers embeds two relevant JSON-LD blocks. We prefer the Yext verifiable
    credential because its credentialSubject has the agency's real website URL
    AND an email — neither of which the plain InsuranceAgency block carries
    (its `url` points to Travelers itself, and there's no `email` field at all).
    Falls back to the InsuranceAgency block when no Yext VC is present.
    Returns {} if neither shape is found.
    """
    yext_subject = None
    insurance_agency = None

    for item in iter_jsonld_blocks(soup):
        # Yext verifiable credential — credentialSubject is one level deep
        subject = item.get("credentialSubject")
        if isinstance(subject, dict) and subject.get("name") and isinstance(subject.get("address"), dict):
            yext_subject = subject
            continue  # keep looking just in case, but VC wins if found

        # Schema.org InsuranceAgency — top-level type
        if (
            item.get("@type") == "InsuranceAgency"
            and item.get("name")
            and isinstance(item.get("address"), dict)
        ):
            insurance_agency = item

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
        agency_name=clean(data.get("name")),
        address_line=clean(address.get("streetAddress")),
        city=clean(address.get("addressLocality")),
        state=clean(address.get("addressRegion")),
        zip=clean(address.get("postalCode")),
        phone=format_phone(data.get("telephone")),
        website_url=_agency_website_or_empty(data.get("url")),
        email=clean(data.get("email")).lower(),
    )
