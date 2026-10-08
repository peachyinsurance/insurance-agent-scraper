"""Backfill empty Carrier and State/Region columns on a HubSpot contacts export.

Runs the reverse of the directory scrapers: instead of walking a carrier's
directory and collecting agents, it starts from a known agent (name, email,
phone) and works out which carrier they're with and which state they're in.

Signals, cheapest first. A value is only filled when the column is empty —
existing CRM values are never overwritten:

  1. Email domain        @allstate.com -> Allstate, @farmersagent.com -> Farmers
  2. Associated Company  some rows carry the carrier there ("Allstate")
  3. Agency website      custom-domain emails -> homepage address -> state
  4. Locator search hit  Google result URL on a carrier locator, e.g.
                         agents.allstate.com/jessica-guay-rochester-ny.html,
                         gives carrier + state in one go. Only accepted when
                         the URL slug contains the agent's name.
  5. Phone area code     state only, low confidence (mobiles move with people)

We can't hit the locators directly — agents.allstate.com sits behind
Cloudflare and agents.farmers.com behind Incapsula (both 403 a plain GET) —
so step 4 goes through Google via Apify, and only the result URLs are parsed.

Everything here is pure; backfill_crm.py owns the network calls.
"""

import re
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

import phonenumbers
from bs4 import BeautifulSoup
from phonenumbers import geocoder

from scraper.core.jsonld import iter_jsonld_blocks
from scraper.core.text import clean


# HubSpot export column names.
FIRST_NAME_COL = "First Name"
LAST_NAME_COL = "Last Name"
EMAIL_COL = "Email"
PHONE_COL = "Phone Number"
STATE_COL = "State/Region"
CARRIER_COL = "Carrier"
COMPANY_COL = "Associated Company"

# Provenance columns appended to the output. Leave them unmapped on HubSpot
# import — they're for reviewing what the backfill did.
STATE_SOURCE_COL = "State Source"
CARRIER_SOURCE_COL = "Carrier Source"

APIFY_GOOGLE_ACTOR_ID = "apify/google-search-scraper"

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine",
    "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska",
    "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
_STATE_NAME_TO_CODE = {name.lower(): code for code, name in US_STATES.items()}

# Captive-agent email domains. Matched against the domain and its parents,
# so "aaa.com" also covers regional clubs like acg.aaa.com.
CARRIER_EMAIL_DOMAINS = {
    "allstate.com": "Allstate",
    "alltstate.com": "Allstate",  # typo that exists in the CRM
    "farmersagent.com": "Farmers",
    "farmersagency.com": "Farmers",
    "statefarm.com": "State Farm",
    "aaa.com": "AAA",
}

# Personal mailboxes — no agency website behind them.
FREE_EMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "icloud.com", "hotmail.com", "outlook.com",
    "aol.com", "live.com", "msn.com", "me.com", "comcast.net",
}

# Our own and test contacts. Left untouched in the output.
INTERNAL_EMAIL_DOMAINS = {"nextcallclub.com", "peachyinsurance.com"}

# Agent-page URL shapes. Each regex runs against the lowercased, URL-decoded
# path and must capture `state` (2-letter code or hyphenated full name) and
# `slug` (the part holding the agent's name). Hosts are compared with any
# leading "www." removed. Carrier None means "read it from the slug" — used
# for business directories, which only count when the slug names a carrier.
_LOCATOR_PATTERNS = [
    # agents.allstate.com/jessica-guay-rochester-ny.html
    ("Allstate", "agents.allstate.com",
     re.compile(r"^/(?P<slug>[a-z0-9-]+)-(?P<state>[a-z]{2})\.html$")),
    # agents.farmers.com/mn/lino-lakes/jesse-bentrup-sr/
    ("Farmers", "agents.farmers.com",
     re.compile(r"^/(?P<state>[a-z]{2})/[^/]+/(?P<slug>[^/]+)/?$")),
    # statefarm.com/agent/us/mn/mounds-view/jessica-thompson-vlz1b5gf7ak
    ("State Farm", "statefarm.com",
     re.compile(r"^/agent/us/(?P<state>[a-z]{2})/[^/]+/(?P<slug>[^/]+)/?$")),
    # agency.nationwide.com/paul-mears-in-murphy-nc
    ("Nationwide", "agency.nationwide.com",
     re.compile(r"^/(?P<slug>[a-z0-9-]+)-in-[a-z0-9-]+-(?P<state>[a-z]{2})/?$")),
    # mapquest.com/us/alabama/clay-colvard-allstate-insurance-412710515
    (None, "mapquest.com",
     re.compile(r"^/us/(?P<state>[a-z-]+)/(?P<slug>[a-z0-9-]+)-\d+/?$")),
    # yellowpages.com/loveland-co/allstate-insurance-bj-hill
    (None, "yellowpages.com",
     re.compile(r"^/[a-z0-9-]+-(?P<state>[a-z]{2})/(?P<slug>[a-z0-9-]+)/?$")),
]

# Carrier names as they appear in directory slugs. Order matters:
# "state-farm" before anything that might match a substring of it.
_SLUG_CARRIERS = [
    ("state-farm", "State Farm"),
    ("allstate", "Allstate"),
    ("farmers-insurance", "Farmers"),
    ("nationwide", "Nationwide"),
]

# Where to point a site-restricted query when the carrier is already known.
# A known carrier's locator page is far more likely to surface this way than
# with a broad query (which returned pure junk for some agents).
LOCATOR_SEARCH_SITES = {
    "Allstate": "agents.allstate.com",
    "Farmers": "agents.farmers.com",
    "State Farm": "statefarm.com/agent",
    "Nationwide": "agency.nationwide.com",
}

_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}

# "Marietta, GA 30062" in page text. Requiring the ZIP keeps random
# two-capital-letter tokens ("IT", "OR") from matching.
_ADDRESS_STATE_RE = re.compile(r",\s*([A-Z]{2})\s+\d{5}(?:-\d{4})?\b")


# --- state normalization -----------------------------------------------------

def state_code(value: str) -> str:
    """'PA', 'pa', 'Pennsylvania', ' wv ' -> 2-letter code. '' if unrecognized."""
    v = clean(value)
    if v.upper() in US_STATES:
        return v.upper()
    return _STATE_NAME_TO_CODE.get(v.lower(), "")


def state_name(code: str) -> str:
    """'PA' -> 'Pennsylvania'. The CRM mostly stores full names, so we write those."""
    return US_STATES.get(code, "")


# --- row classification ------------------------------------------------------

def email_domain(email: str) -> str:
    email = clean(email).lower()
    return email.rsplit("@", 1)[1] if "@" in email else ""


def _domain_lookup(domain: str, table: dict[str, str]) -> str:
    """Match domain or any parent domain against table keys."""
    parts = domain.split(".")
    for i in range(len(parts) - 1):
        hit = table.get(".".join(parts[i:]))
        if hit:
            return hit
    return ""


def carrier_from_email(email: str) -> str:
    return _domain_lookup(email_domain(email), CARRIER_EMAIL_DOMAINS)


def carrier_from_company(company: str) -> str:
    """'Allstate' in Associated Company -> 'Allstate'. Exact names only."""
    known = set(CARRIER_EMAIL_DOMAINS.values())
    company = clean(company)
    return company if company in known else ""


def is_internal(row: dict) -> bool:
    """Our own accounts and obvious test records — never enriched."""
    if email_domain(row.get(EMAIL_COL, "")) in INTERNAL_EMAIL_DOMAINS:
        return True
    name_blob = " ".join(
        row.get(col, "") for col in (FIRST_NAME_COL, LAST_NAME_COL, COMPANY_COL)
    ).lower()
    return "test" in name_blob.split()


def agency_website(email: str) -> str:
    """Homepage URL for a custom-domain email, '' for carrier or free mailboxes."""
    domain = email_domain(email)
    if not domain or domain in FREE_EMAIL_DOMAINS:
        return ""
    if _domain_lookup(domain, CARRIER_EMAIL_DOMAINS):
        return ""
    if domain in INTERNAL_EMAIL_DOMAINS:
        return ""
    return f"https://{domain}/"


# --- names --------------------------------------------------------------------

def _letters(token: str) -> str:
    return re.sub(r"[^a-z]", "", token.lower())


def split_name(first: str, last: str) -> tuple[str, list[str]]:
    """Return (first-name token, last-name tokens) for slug matching.

    Copes with the CRM's messier rows:
      'Sean Dammann' / 'Dammann'   -> ('sean', ['dammann'])
      'Jake Greco' / 'Greco II'    -> ('jake', ['greco'])
      'Robert A' / 'Irwin'         -> ('robert', ['irwin'])
      'DANIELLE' / 'LEE-BREEDY'    -> ('danielle', ['lee', 'breedy'])
      'Kevin' / "O'Neil"           -> ('kevin', ['oneil'])
    """
    last_tokens = [
        t for t in (_letters(p) for p in re.split(r"[\s-]+", clean(last)))
        if t and t not in _NAME_SUFFIXES
    ]
    first_tokens = [t for t in (_letters(p) for p in clean(first).split()) if t]
    # Drop a surname someone typed into First Name as well.
    while len(first_tokens) > 1 and first_tokens[-1] in last_tokens:
        first_tokens.pop()
    return (first_tokens[0] if first_tokens else ""), last_tokens


def display_name(first: str, last: str) -> str:
    """Human-readable 'First Last' for the search query.

    'Sean Dammann' / 'Dammann' -> 'Sean Dammann'; 'Catherine Van' / 'Tiem'
    -> 'Catherine Van Tiem'; 'Jake Greco' / 'Greco II' -> 'Jake Greco'.
    """
    first_token, last_tokens = split_name(first, last)
    if not first_token or not last_tokens:
        return ""
    first_words = clean(first).strip(" ,").split()
    while len(first_words) > 1 and _letters(first_words[-1]) in last_tokens:
        first_words.pop()
    last_words = [p for p in clean(last).split() if _letters(p) not in _NAME_SUFFIXES]
    return " ".join(first_words + last_words).strip(" ,")


def slug_matches_name(slug: str, first_token: str, last_tokens: list[str]) -> bool:
    """True if every last-name token and the first name appear in the slug.

    First names match on prefix in either direction (min 3 chars) so
    'tim' matches 'timothy' and 'jessica' matches 'jess'. Nicknames that
    aren't prefixes ('mike'/'michael') don't — a missed fill is cheaper
    than a wrong one.
    """
    if not first_token or not last_tokens:
        return False
    tokens = {_letters(t) for t in re.split(r"[^a-z0-9']+", slug.lower())}
    tokens.discard("")
    if not all(t in tokens for t in last_tokens):
        return False
    if first_token in tokens:
        return True
    if len(first_token) < 3:
        return False
    return any(
        len(t) >= 3 and (t.startswith(first_token) or first_token.startswith(t))
        for t in tokens
    )


def slug_matches_email_domain(slug: str, email: str) -> bool:
    """True if the slug names the agency behind the contact's email domain.

    Some carrier pages are listed under the agency, not the agent:
    kevin@purdygoodcoverage.com -> agents.allstate.com/purdy-good-coverage-corp-commack-ny.html.
    Only for custom domains, and only labels of 6+ letters so short generic
    names ('ins', 'agency') can't match by accident.
    """
    if not agency_website(email):
        return False
    label = _letters(email_domain(email).split(".")[0])
    return len(label) >= 6 and label in _letters(slug)


# --- locator URLs -------------------------------------------------------------

@dataclass(frozen=True)
class LocatorHit:
    carrier: str
    state: str  # 2-letter code
    slug: str
    url: str


def _carrier_in_slug(slug: str) -> str:
    for keyword, carrier in _SLUG_CARRIERS:
        if keyword in slug:
            return carrier
    return ""


def parse_locator_url(url: str) -> LocatorHit | None:
    """Return carrier/state/slug if url is a US agent page we can read, else None.

    Covers the carrier locators plus MapQuest / Yellow Pages listings whose
    slug names the carrier ('clay-colvard-allstate-insurance').
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    host = parsed.netloc.lower().removeprefix("www.")
    path = unquote(parsed.path).lower()
    for carrier, locator_host, pattern in _LOCATOR_PATTERNS:
        if host != locator_host:
            continue
        match = pattern.match(path)
        if not match:
            return None
        code = state_code(match.group("state").replace("-", " "))
        if not code:
            return None
        slug = match.group("slug")
        carrier = carrier or _carrier_in_slug(slug)
        if not carrier:
            return None
        return LocatorHit(carrier=carrier, state=code, slug=slug, url=url)
    return None


def search_query(first: str, last: str, carrier: str) -> str:
    """Google query for one agent. '' when the name is too thin to search.

    Known carrier with a locator -> restrict to that locator. Otherwise a
    broad query, which also surfaces MapQuest / Yellow Pages listings.
    """
    name = display_name(first, last)
    if not name:
        return ""
    site = LOCATOR_SEARCH_SITES.get(carrier)
    if site:
        return f'"{name}" site:{site}'
    return f'"{name}" insurance agent'


def urls_by_query(items: list[dict]) -> dict[str, list[str]]:
    """Group organic result URLs from apify/google-search-scraper by query term."""
    grouped: dict[str, list[str]] = {}
    for item in items:
        term = (item.get("searchQuery") or {}).get("term", "")
        if not term:
            continue
        urls = grouped.setdefault(term, [])
        for result in item.get("organicResults") or []:
            url = result.get("url")
            if url:
                urls.append(url)
    return grouped


def google_search_payload(queries: list[str]) -> dict:
    return {
        "queries": "\n".join(queries),
        "maxPagesPerQuery": 1,
        "resultsPerPage": 10,
        "countryCode": "us",
        "languageCode": "en",
        "mobileResults": False,
        "saveHtml": False,
        "saveHtmlToKeyValueStore": False,
    }


# --- website / phone ----------------------------------------------------------

def state_from_website_html(html: str) -> str:
    """State code from an agency homepage, '' if absent or ambiguous.

    JSON-LD addressRegion first; otherwise 'City, ST 12345' patterns in the
    visible text, accepted only when they all agree (multi-office agencies
    list several states and we can't tell which one the contact sits in).
    """
    soup = BeautifulSoup(html, "html.parser")
    for block in iter_jsonld_blocks(soup):
        address = block.get("address")
        if isinstance(address, list):
            address = address[0] if address else None
        if isinstance(address, dict):
            code = state_code(address.get("addressRegion", ""))
            if code:
                return code
    text = soup.get_text(" ")
    codes = {m.group(1) for m in _ADDRESS_STATE_RE.finditer(text)} & US_STATES.keys()
    return codes.pop() if len(codes) == 1 else ""


def state_from_phone(raw: str) -> str:
    """State code from a US number's area code, '' for non-US or junk numbers."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 10:
        digits = "1" + digits
    if len(digits) != 11 or not digits.startswith("1"):
        return ""
    try:
        number = phonenumbers.parse("+" + digits, None)
    except phonenumbers.NumberParseException:
        return ""
    if not phonenumbers.is_valid_number(number):
        return ""
    if phonenumbers.region_code_for_number(number) != "US":
        return ""
    # Comes back as 'Iowa' or 'Cedar Rapids, IA' depending on prefix depth.
    description = geocoder.description_for_number(number, "en")
    return state_code(description.rsplit(",", 1)[-1])


# --- per-row fill -------------------------------------------------------------

@dataclass
class Fill:
    """Working state for one contact. `state` is a 2-letter code internally."""
    carrier: str = ""
    carrier_source: str = ""
    state: str = ""
    state_source: str = ""
    skipped: bool = False
    # Set when the CRM already has a State value we can't parse ("Ontario",
    # a typo). We keep it as-is rather than overwrite with a lookup.
    keep_existing_state: bool = False

    @property
    def needs_state(self) -> bool:
        return not self.state and not self.keep_existing_state

    @property
    def complete(self) -> bool:
        return bool(self.carrier) and not self.needs_state


def initial_fill(row: dict) -> Fill:
    """Existing values plus the free signals (email domain, company column)."""
    if is_internal(row):
        return Fill(skipped=True)

    fill = Fill()
    existing_state = row.get(STATE_COL, "")
    if clean(existing_state):
        fill.state = state_code(existing_state)
        if fill.state:
            fill.state_source = "existing"
        else:
            fill.keep_existing_state = True
            fill.state_source = "existing (unrecognized, kept)"

    existing_carrier = clean(row.get(CARRIER_COL, ""))
    if existing_carrier:
        fill.carrier, fill.carrier_source = existing_carrier, "existing"
    elif carrier := carrier_from_email(row.get(EMAIL_COL, "")):
        fill.carrier, fill.carrier_source = carrier, "email domain"
    elif carrier := carrier_from_company(row.get(COMPANY_COL, "")):
        fill.carrier, fill.carrier_source = carrier, "associated company"
    return fill


def apply_website(fill: Fill, html: str, url: str, phone_state: str = "") -> None:
    """Fill state from the agency homepage.

    The homepage gives the agency's main office, which isn't always where the
    contact sits (Bramble lists CA; Hunter Schneider's number is 770/GA). We
    still take it, but flag disagreement with the phone so it gets reviewed.
    """
    if not fill.needs_state or not html:
        return
    code = state_from_website_html(html)
    if not code:
        return
    fill.state, fill.state_source = code, f"website: {url}"
    if phone_state and phone_state != code:
        fill.state_source += f" (CHECK: phone area code says {phone_state})"


def choose_hit(
    hits: list[LocatorHit], fill: Fill, phone_state: str = ""
) -> LocatorHit | None:
    """Pick the one locator hit consistent with what we already know.

    Hits that contradict a known carrier or state are dropped (same-name
    agents at other carriers/states are common). If the rest still disagree,
    the phone's area code breaks the tie; otherwise we give up rather than
    guess.
    """
    candidates = [
        h for h in hits
        if (not fill.carrier or h.carrier == fill.carrier)
        and (not fill.state or h.state == fill.state)
    ]
    if len({(h.carrier, h.state) for h in candidates}) == 1:
        return candidates[0]
    if phone_state:
        narrowed = [h for h in candidates if h.state == phone_state]
        if len({(h.carrier, h.state) for h in narrowed}) == 1:
            return narrowed[0]
    return None


def apply_search_results(fill: Fill, row: dict, urls: list[str]) -> None:
    first_token, last_tokens = split_name(
        row.get(FIRST_NAME_COL, ""), row.get(LAST_NAME_COL, "")
    )
    email = row.get(EMAIL_COL, "")
    hits = [
        hit for hit in (parse_locator_url(u) for u in urls)
        if hit and (
            slug_matches_name(hit.slug, first_token, last_tokens)
            or slug_matches_email_domain(hit.slug, email)
        )
    ]
    phone_state = state_from_phone(row.get(PHONE_COL, ""))
    hit = choose_hit(hits, fill, phone_state)
    if hit is None:
        return
    # A lone same-name agent in another state is the main false-positive
    # (Ben Bucher: 402/NE phone, matched an Allstate page in Fort Wayne, IN).
    # Keep the fill but flag it, same as apply_website.
    source = f"locator: {hit.url}"
    if phone_state and phone_state != hit.state:
        source += f" (CHECK: phone area code says {phone_state})"
    if not fill.carrier:
        fill.carrier, fill.carrier_source = hit.carrier, source
    if fill.needs_state:
        fill.state, fill.state_source = hit.state, source


def apply_phone(fill: Fill, row: dict) -> None:
    if not fill.needs_state:
        return
    code = state_from_phone(row.get(PHONE_COL, ""))
    if code:
        fill.state, fill.state_source = code, "phone area code (low confidence)"


def output_row(row: dict, fill: Fill) -> dict:
    """Original row with State/Carrier filled and provenance columns added."""
    out = dict(row)
    if fill.skipped:
        out[STATE_SOURCE_COL] = out[CARRIER_SOURCE_COL] = "skipped (internal/test)"
        return out
    if fill.state:
        out[STATE_COL] = state_name(fill.state)
    if fill.carrier:
        out[CARRIER_COL] = fill.carrier
    out[STATE_SOURCE_COL] = fill.state_source or "not found"
    out[CARRIER_SOURCE_COL] = fill.carrier_source or "not found"
    return out
