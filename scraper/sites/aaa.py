"""Site adapter for AAA via Apify.

Unlike Travelers and State Farm, AAA is NOT scraped directly. The 2026-05-14
scout of aaa.com showed it's a federation portal with no agent/branch sitemap
(robots.txt blocks /aaa/*; the sitemap index has only travel/membership/
marketing properties). The actual data is scattered across ~30 regional clubs
with heterogeneous sites — a multi-week build for branch-level (not agent-
level) data.

Andrew's 2026-05-14 decision (relayed via Victor): query Apify's
compass/crawler-google-places actor with "AAA agents in <STATE>" and let
Google Maps surface whatever's relevant. Clay handles email + person-name
enrichment downstream, so the Stage 1 CSV ships directly without a Stage 2.

This module only contains pure helpers — query template, payload builder,
result-row mapper. The actual Apify API call (start run, poll, fetch dataset)
lives in scrape.py:run_aaa_stage1, since it's a one-off integration and
doesn't fit the site-adapter contract used by Travelers/State Farm.
"""

from typing import Any
from urllib.parse import urlparse


APIFY_ACTOR_ID = "compass/crawler-google-places"

# Default cap per state. Andrew called 1000 a good ceiling for one state's
# "AAA agents in <STATE>" query; Apify subscription handles the cost so we
# don't need to fight for fewer results. CLI --limit overrides this.
DEFAULT_PLACES_PER_STATE = 1000

# 2-letter postal code → full state name. The actor's `state` input field
# expects the full name ("Texas", not "TX") per its input-schema docs.
# Without this geographic bias the actor's results are unstable — a TX
# query came back with California results on 2026-05-14 because Google
# Maps geo-localized to the actor's proxy region rather than honoring
# the "in TX" hint in the search string.
_STATE_CODE_TO_NAME = {
    "al": "Alabama", "ak": "Alaska", "az": "Arizona", "ar": "Arkansas",
    "ca": "California", "co": "Colorado", "ct": "Connecticut", "de": "Delaware",
    "dc": "District of Columbia", "fl": "Florida", "ga": "Georgia", "hi": "Hawaii",
    "id": "Idaho", "il": "Illinois", "in": "Indiana", "ia": "Iowa",
    "ks": "Kansas", "ky": "Kentucky", "la": "Louisiana", "me": "Maine",
    "md": "Maryland", "ma": "Massachusetts", "mi": "Michigan", "mn": "Minnesota",
    "ms": "Mississippi", "mo": "Missouri", "mt": "Montana", "ne": "Nebraska",
    "nv": "Nevada", "nh": "New Hampshire", "nj": "New Jersey", "nm": "New Mexico",
    "ny": "New York", "nc": "North Carolina", "nd": "North Dakota", "oh": "Ohio",
    "ok": "Oklahoma", "or": "Oregon", "pa": "Pennsylvania", "ri": "Rhode Island",
    "sc": "South Carolina", "sd": "South Dakota", "tn": "Tennessee", "tx": "Texas",
    "ut": "Utah", "vt": "Vermont", "va": "Virginia", "wa": "Washington",
    "wv": "West Virginia", "wi": "Wisconsin", "wy": "Wyoming",
}


def state_full_name(state_slug: str) -> str:
    """Return the full state name for a 2-letter postal code.

    Raises ValueError if the slug doesn't match a known US state/DC.
    Case-insensitive on input.
    """
    name = _STATE_CODE_TO_NAME.get(state_slug.lower())
    if name is None:
        raise ValueError(
            f"Unknown state slug: {state_slug!r}. "
            f"Expected one of: {sorted(_STATE_CODE_TO_NAME)}."
        )
    return name


def query_for_state(state_slug: str) -> str:
    """Build the Google search string for one state.

    State slug is the 2-letter postal code (uppercase or lowercase). We
    pass it to Google verbatim — Google's understanding of "AAA agents in TX"
    is what the actor relies on for SEARCH context. The actor's structured
    `state` field (set separately in build_apify_input) does the geo
    BIAS independent of the search text.
    """
    return f"AAA agents in {state_slug.upper()}"


def build_apify_input(state_slug: str, limit: int | None = None) -> dict[str, Any]:
    """Build the JSON payload for one Apify actor run.

    One state per invocation so progress is per-state and a failure on TX
    doesn't take out GA's results. The actor supports batched search terms
    but we trade a small amount of overhead for cleaner failure isolation.

    Geographic constraint is via the structured `state` + `countryCode`
    fields (which the actor's docs explicitly recommend over location text
    inside the search query). Reviews and images are disabled — we don't
    need them and they slow runs significantly.
    """
    return {
        "searchStringsArray": [query_for_state(state_slug)],
        "state": state_full_name(state_slug),
        "countryCode": "us",  # lowercase — actor rejects "US" with 400 invalid-input
        "maxCrawledPlacesPerSearch": limit or DEFAULT_PLACES_PER_STATE,
        "language": "en",
        "exportPlaceUrls": False,
        "includeReviews": False,
        "includeImages": False,
    }


def is_aaa_result(item: dict[str, Any]) -> bool:
    """True if the Apify result is a legitimate AAA business (not an impostor).

    Two-pronged check, tightened on 2026-05-20 after the 118-item TX corpus
    revealed ~50% impostor rate with just the name prefix:

    1. Title starts with "AAA " (4-char prefix, uppercase, trailing space).
       Drops the AAAA-prefixed impostors like "AAAA Auto Insurance".
    2. Website host is `aaa.com` or any `*.aaa.com` subdomain. Drops the
       AAA-prefixed-but-unrelated businesses like "AAA Loans" (aaaloanco.com),
       "AAA Security Guard Service" (aaaguards.com), "AAA Bail Bonds"
       (aaabaillubbock.com), "AAA Infinity Insurance" (gotinfinity.com) —
       all of which passed the name-only filter.

    Trade-off: rejects legit AAA listings on Google Maps that have no
    website at all (rare — ~2 per 100 results in the TX corpus). We can't
    disambiguate those from impostors without the domain signal, so we
    drop them. Acceptable for ~90%+ precision on what Clay receives.

    Case-sensitive on the title — AAA's brand is always uppercase in
    Google Maps data, so lowercase variants are suspect.
    """
    title = item.get("title")
    if not isinstance(title, str) or not title.startswith("AAA "):
        return False
    return _is_aaa_website(item.get("website"))


def _is_aaa_website(url: Any) -> bool:
    """True if url's host is exactly aaa.com or any subdomain like ace.aaa.com.

    Robust against substring impostors — `aaainsurancecenter.com` would
    match if we did a naive `"aaa.com" in url` check; using urlparse's
    .hostname and checking equality / .endswith('.aaa.com') excludes it
    correctly. Also handles bare hostnames (no scheme) by prepending
    http:// before parsing.

    Returns False for None, non-strings, and empty strings — which is
    why missing websites in Apify results get dropped.
    """
    if not isinstance(url, str) or not url:
        return False
    parsed = urlparse(url if "://" in url else f"http://{url}")
    host = (parsed.hostname or "").lower()
    return host == "aaa.com" or host.endswith(".aaa.com")


def apify_item_to_row(item: dict[str, Any], source_query: str) -> dict[str, str]:
    """Map one Apify result dict into a Clay-ready Stage 1 row.

    Column names match what Clay expects on import (company_name, website,
    phone, street_address, city, state, postal_code). Provenance fields
    (google_place_id, google_maps_url, category, rating, review_count,
    source_query) are extras for dedup + downstream quality filtering.

    Missing or None fields default to empty string; numeric fields (rating,
    review_count) are stringified so the CSV writer gets uniform types.
    """
    return {
        "company_name": _str(item.get("title")),
        "website": _str(item.get("website")),
        "phone": _str(item.get("phone")),
        "street_address": _str(item.get("street")),
        "city": _str(item.get("city")),
        "state": _str(item.get("state")),
        "postal_code": _str(item.get("postalCode")),
        "google_place_id": _str(item.get("placeId")),
        "google_maps_url": _str(item.get("url")),
        "category": _str(item.get("categoryName")),
        "rating": _str(item.get("totalScore")),
        "review_count": _str(item.get("reviewsCount")),
        "source_query": source_query,
    }


def _str(value: Any) -> str:
    """Coerce any value to string, with None and missing-key defaulting to ''."""
    if value is None:
        return ""
    return str(value)
