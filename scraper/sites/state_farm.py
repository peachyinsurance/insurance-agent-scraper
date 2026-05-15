"""Site adapter for State Farm's public agent directory at statefarm.com.

Unlike Travelers/Progressive, State Farm publishes a sitemap that enumerates
every agent URL — so this adapter is sitemap-driven rather than crawl-driven.
The pluggable scraper detects this by the presence of `iter_agency_urls`
on the module and skips the state→city→agency walk used by other adapters.

Stage 1 output uses founder.name from JSON-LD (the agent's clean person name)
for agency_name. The top-level JSON-LD `name` includes marketing copy
("Harold Mitchell Jr - State Farm Agent - Forest Park, GA") and is only
used as a fallback after suffix stripping. State Farm doesn't expose
agent emails on the page — the email column is always empty and Clay
handles email enrichment downstream.
"""

import re
from collections.abc import Iterator
from urllib.parse import urlparse
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup

from scraper.core.http import PerDomainLimiter, fetch_url
from scraper.core.jsonld import iter_jsonld_blocks
from scraper.core.text import clean, format_phone
from scraper.records import AgencyRecord


SITEMAP_URL = "https://www.statefarm.com/sitemap-agents.xml"

# Agent detail URLs are 5-segment: /agent/us/<state>/<city>/<name-id-slug>.
# The sitemap also contains 3-segment state pages and 4-segment city pages
# which we skip — they're not useful for Stage 1 enrichment.
_AGENT_PATH_RE = re.compile(r"^/agent/us/([a-z]{2})/([^/]+)/([^/]+)$")

# State Farm slug IDs are exactly 11 chars, alphanumeric (e.g. "vp4jn1ys000").
# Used to separate the trailing ID from name tokens in the URL slug.
_AGENT_ID_RE = re.compile(r"^[a-z0-9]{11}$")

# Strip the trailing " - State Farm Agent - <City>, <ST>" from the top-level
# JSON-LD `name`. Anchored to end so we only touch the trailing pattern,
# never a hyphen that's part of the actual agent name.
_NAME_SUFFIX_RE = re.compile(
    r"\s*-\s*State Farm Agent\s*-\s*[^,]+,\s*[A-Z]{2}\s*$"
)

# Sitemap XML namespace — needed because ElementTree includes it in tag names.
_SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


# --- URL enumeration --------------------------------------------------------

def iter_agency_urls(
    session: requests.Session,
    limiter: PerDomainLimiter,
    state_slug: str | None = None,
) -> Iterator[str]:
    """Yield agent detail URLs from State Farm's published sitemap.

    Fetches SITEMAP_URL through the shared rate-limited fetcher, parses each
    <loc> entry, and yields only 5-segment agent detail URLs (skipping the
    3-segment state landing pages and 4-segment city pages also in the file).

    If state_slug is given (e.g. "ga"), filter to that state only — case-
    insensitive on input, the sitemap itself uses lowercase 2-letter codes.

    Yields URLs in sitemap order (which is unsorted — Texas, Pennsylvania,
    Connecticut, etc. interleaved). Caller sorts if it needs deterministic
    ordering for resumability with --start-from.

    Raises RuntimeError if the sitemap fetch fails. Malformed or non-agent
    <loc> entries are silently skipped so a single bad row doesn't kill
    enumeration of the other ~25K.
    """
    sitemap_xml = fetch_url(SITEMAP_URL, session, limiter)
    if sitemap_xml is None:
        raise RuntimeError(f"Failed to fetch State Farm sitemap at {SITEMAP_URL}")

    state_filter = state_slug.lower() if state_slug else None

    try:
        root = ElementTree.fromstring(sitemap_xml)
    except ElementTree.ParseError as exc:
        raise RuntimeError(f"Sitemap XML did not parse: {exc}") from exc

    for url_elem in root.findall(f"{_SITEMAP_NS}url"):
        loc_elem = url_elem.find(f"{_SITEMAP_NS}loc")
        if loc_elem is None or not loc_elem.text:
            continue
        url = loc_elem.text.strip()
        path = urlparse(url).path
        match = _AGENT_PATH_RE.match(path)
        if not match:
            continue
        state_in_url = match.group(1)
        if state_filter and state_in_url != state_filter:
            continue
        yield url


# --- URL slug parsing -------------------------------------------------------

def parse_url_slug(url: str) -> dict[str, str]:
    """Mine state/city/name/agent-id from a State Farm agent URL.

    Given https://www.statefarm.com/agent/us/ga/forest-park/harold-mitchell-jr-vp4jn1ys000
    returns:
        {
            "state": "ga",
            "city": "forest-park",
            "first_name_guess": "Harold",
            "last_name_guess": "Mitchell Jr",
            "agent_id": "vp4jn1ys000",
        }

    The slug ID is the trailing 11-char alphanumeric token. If the trailing
    token doesn't match that shape, it's treated as part of the name and
    agent_id is empty.

    Heuristic for splitting: first hyphen-separated token is first name,
    everything after is last name. This preserves "Jr"/"II" suffixes and
    multi-word last names ("Van Der Berg") without special-casing.

    Returns all-empty fields if the URL doesn't match the agent-page shape
    (e.g. a 3-segment state URL or 4-segment city URL gets ignored cleanly).
    """
    empty = {
        "state": "",
        "city": "",
        "first_name_guess": "",
        "last_name_guess": "",
        "agent_id": "",
    }
    path = urlparse(url).path
    match = _AGENT_PATH_RE.match(path)
    if not match:
        return empty

    state, city, name_id_slug = match.groups()
    tokens = name_id_slug.split("-")

    if tokens and _AGENT_ID_RE.match(tokens[-1]):
        agent_id = tokens[-1]
        name_tokens = tokens[:-1]
    else:
        agent_id = ""
        name_tokens = tokens

    if not name_tokens:
        first_name = ""
        last_name = ""
    elif len(name_tokens) == 1:
        first_name = name_tokens[0].title()
        last_name = ""
    else:
        first_name = name_tokens[0].title()
        last_name = " ".join(t.title() for t in name_tokens[1:])

    return {
        "state": state,
        "city": city,
        "first_name_guess": first_name,
        "last_name_guess": last_name,
        "agent_id": agent_id,
    }


# --- agency detail parsing --------------------------------------------------

def _website_or_empty(url: str) -> str:
    """Return url if it points to a real personal website, '' if it's State Farm.

    State Farm's `sameAs[0]` is sometimes the agent's own statefarm.com page
    (when they don't have a personal site set up) — that's not what belongs
    in the website_url column. Strip any statefarm.com or st8fm.com host.

    Also prepends https:// if the URL is bare (`www.haroldmitchell.net` →
    `https://www.haroldmitchell.net`), which is the shape State Farm
    publishes in the JSON-LD.
    """
    if not isinstance(url, str):
        return ""
    candidate = url.strip()
    if not candidate:
        return ""
    if not candidate.startswith(("http://", "https://")):
        candidate = "https://" + candidate
    host = urlparse(candidate).netloc.lower()
    if "statefarm.com" in host or "st8fm.com" in host:
        return ""
    return candidate


def _strip_name_suffix(name: str) -> str:
    """Strip ' - State Farm Agent - <City>, <ST>' tail from JSON-LD `name`.

    Example: 'Harold Mitchell Jr - State Farm Agent - Forest Park, GA'
    becomes 'Harold Mitchell Jr'. Returns '' for non-string input.
    """
    if not isinstance(name, str):
        return ""
    return _NAME_SUFFIX_RE.sub("", name).strip()


def _first_string(value) -> str:
    """Return first item if value is a list, value if it's a string, else ''.

    State Farm's JSON-LD has fields that are sometimes a single string and
    sometimes a single-element list (telephone, sameAs). Normalize both
    shapes here so callers don't repeat the isinstance check.
    """
    if isinstance(value, list):
        return value[0] if value and isinstance(value[0], str) else ""
    if isinstance(value, str):
        return value
    return ""


def _extract_agency_jsonld(soup) -> dict:
    """Pick the InsuranceAgency JSON-LD block on a State Farm agent page.

    State Farm pages have ~9 JSON-LD blocks: 8 are product Offer schemas
    (auto/home/life insurance product cards) and one is the InsuranceAgency
    block we want. No Yext verifiable credential like Travelers — there's
    only the one block to find.

    Handles @type as either a string ("InsuranceAgency") or a list
    (["InsuranceAgency"]) — State Farm uses the list form. Returns {} if
    no matching block is present.
    """
    for item in iter_jsonld_blocks(soup):
        type_value = item.get("@type")
        types = type_value if isinstance(type_value, list) else [type_value]
        if "InsuranceAgency" in types:
            return item
    return {}


def parse_agency_page(html: str, source_url: str) -> AgencyRecord:
    """Extract fields from one State Farm agent detail page.

    Reads the InsuranceAgency JSON-LD block, with founder.name preferred
    over the marketing-suffixed top-level name. Email is always empty —
    State Farm doesn't expose agent emails on the page; Clay handles
    email enrichment downstream.

    If no InsuranceAgency JSON-LD block is found, returns a record with
    only source_url populated (matches Travelers' convention — a row
    written to disk so the run is resumable, just with empty fields).
    """
    soup = BeautifulSoup(html, "html.parser")
    data = _extract_agency_jsonld(soup)
    if not data:
        return AgencyRecord(source_url=source_url)

    address = data.get("address") if isinstance(data.get("address"), dict) else {}
    founder = data.get("founder") if isinstance(data.get("founder"), dict) else {}

    agent_name = clean(founder.get("name")) or _strip_name_suffix(data.get("name"))

    return AgencyRecord(
        source_url=source_url,
        agency_name=agent_name,
        address_line=clean(address.get("streetAddress")),
        city=clean(address.get("addressLocality")),
        state=clean(address.get("addressRegion")),
        zip=clean(address.get("postalCode")),
        phone=format_phone(_first_string(data.get("telephone"))),
        website_url=_website_or_empty(_first_string(data.get("sameAs"))),
        email="",
    )
