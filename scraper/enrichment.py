"""Stage 2 enrichment: scrape agency websites for (name, email) pairs.

Module is built up across step 7 sub-steps. Currently has:
- extract_emails_from_html (7b)
- extract_name_from_email_local_part (7c)
- find_name_candidates (7d)
- Constants: COMMON_FIRST_NAMES, GENERIC_LOCAL_PARTS, TITLE_WORDS,
  NON_PERSON_WORDS, SUFFIXES

Coming next:
- candidate scoring + name-email association + dedup (7e)
- enrich_agency orchestrator (7f)
"""

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from scraper.core.http import PerDomainLimiter, fetch_url
from scraper.records import AgencyRecord, EnrichedLead


EMAIL_REGEX = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


# Email local-parts that are role addresses, not people. Used to skip
# generic emails when extracting names and to filter parts of compound
# local-parts (info.team@, sales.support@).
GENERIC_LOCAL_PARTS = frozenset([
    "info", "mail", "sales", "contact", "admin", "office", "hello",
    "support", "service", "services", "team", "agency", "agent", "agents",
    "help", "quote", "quotes", "billing", "accounts", "accounting",
    "customer", "customerservice", "noreply", "no-reply", "donotreply",
    "marketing", "hr", "general", "reception", "main", "web", "webmaster",
    "postmaster", "abuse", "root", "ins", "insure", "insurance", "policy",
    "claims", "operations", "docs", "staff",
])


# Common US first names. Conservative match list for the single-word
# email-local-part path. Lowercased.
COMMON_FIRST_NAMES = frozenset([
    "aaron", "abby", "abigail", "adam", "adrian", "aidan", "alan", "alex",
    "alexander", "alexandra", "alexis", "alfred", "alice", "allison", "amanda",
    "amber", "amy", "ana", "andrea", "andrew", "andy", "angela", "angelica",
    "angie", "anna", "anne", "annette", "anthony", "antoine", "antonio",
    "april", "arnold", "arthur", "ashley", "audrey", "austin",
    "barbara", "barry", "beatriz", "becky", "belinda", "ben", "benjamin",
    "bernard", "beth", "betty", "beverly", "bill", "billy", "blake", "bobby",
    "brad", "bradley", "brandon", "brandy", "brenda", "brent", "brett",
    "brian", "bruce", "bryan",
    "caitlin", "calvin", "cameron", "candice", "carl", "carla", "carlos",
    "carmen", "carol", "caroline", "carolyn", "carrie", "casey", "catherine",
    "cathy", "cecilia", "chad", "charles", "charlie", "cheryl", "chris",
    "christian", "christina", "christine", "christopher", "christy", "cindy",
    "claire", "clarence", "clark", "claudia", "clay", "clifford", "clint",
    "cody", "colleen", "collin", "connie", "connor", "cory", "craig",
    "crystal", "curtis", "cynthia",
    "daniel", "danielle", "danny", "darin", "darrell", "darryl", "dave",
    "david", "dawn", "dean", "deanna", "deborah", "debra", "denise", "dennis",
    "derek", "derrick", "diana", "diane", "dianne", "don", "donald", "donna",
    "doris", "dorothy", "doug", "douglas", "duane", "dustin", "dwight",
    "earl", "edgar", "edith", "edna", "eduardo", "edward", "edwin", "eileen",
    "elaine", "elena", "elizabeth", "ellen", "elliot", "elsa", "emily",
    "emma", "eric", "erica", "erik", "erika", "erin", "ernest", "esteban",
    "esther", "eugene", "evan", "evelyn",
    "faith", "felicia", "felix", "fernando", "fred", "frederick", "francis",
    "frank", "frankie",
    "gabriel", "gabrielle", "gary", "gene", "george", "gerald", "gilbert",
    "gina", "glen", "glenn", "gloria", "grace", "greg", "gregory", "gus",
    "guy",
    "hannah", "harold", "harry", "heather", "hector", "helen", "henry",
    "holly", "howard", "hugh", "hugo",
    "ian", "ida", "ignacio", "irene", "isabel", "isaac", "ivan",
    "jack", "jackie", "jacob", "jacqueline", "jaime", "james", "jamie",
    "jan", "jane", "janet", "janice", "jared", "jasmine", "jason", "javier",
    "jay", "jean",
    "jeanne", "jeff", "jeffrey", "jen", "jennifer", "jeremy", "jerome",
    "jerry", "jesse", "jessica", "jessie", "jesus", "jill", "jim", "jimmy",
    "joan", "joanne", "joaquin", "jodi", "joe", "joel", "john", "johnny",
    "jon", "jonathan", "jordan", "jorge", "jose", "joseph", "josh", "joshua",
    "joyce", "juan", "juanita", "judith", "judy", "julia", "julian", "julie",
    "julio", "june", "justin",
    "karen", "karl", "karla", "kate", "katherine", "kathleen", "kathryn",
    "kathy", "katie", "keith", "kelly", "ken", "kendra", "kenneth", "kent",
    "kevin", "kim", "kimberly", "kris", "kristen", "kristin", "kristina",
    "kristine", "kurt", "kyle",
    "lance", "larry", "laura", "lauren", "laurie", "lawrence", "lee", "leon",
    "leonard", "leroy", "leslie", "lewis", "linda", "lisa", "lloyd", "lois",
    "lonnie", "loretta", "lori", "lorraine", "louis", "louise", "lucas",
    "lucia", "lucy", "luis", "luke", "lydia", "lyle", "lynn",
    "maggie", "mara", "marc", "marcia", "marco", "marcus", "margaret",
    "maria", "marie", "marilyn", "mario", "marion", "marissa", "mark",
    "marlon", "martha", "martin", "marty", "marvin", "mary", "mason",
    "mateo", "matt", "matthew", "maureen", "max", "megan", "melissa", "melvin",
    "meredith", "mia", "michael", "michele", "michelle", "miguel", "mike",
    "mildred", "miranda", "mitch", "mitchell", "molly", "monica", "morgan",
    "nancy", "nathan", "neil", "nelson", "nicholas", "nick", "nicole",
    "noah", "nora", "norma", "norman",
    "olga", "olive", "oliver", "olivia", "omar", "oscar",
    "pablo", "pamela", "patricia", "patrick", "patty", "paul", "paula",
    "pedro", "peggy", "penny", "perry", "pete", "peter", "phil", "philip",
    "phillip", "phyllis",
    "rachel", "rafael", "ralph", "ramon", "randall", "randy", "raul", "ray",
    "raymond", "rebecca", "regina", "renee", "rex", "ricardo", "richard",
    "rick", "ricky", "riley", "rita", "rob", "robbie", "robert", "roberto",
    "robin", "rod", "roderick", "rodney", "roger", "roland", "ron", "ronald",
    "ronnie", "rory", "rosa", "rose", "rosemary", "roy", "ruben", "ruby",
    "rudy", "russell", "ruth", "ryan",
    "sally", "sam", "samantha", "samuel", "sandra", "sandy", "santiago",
    "sarah", "scott", "sean", "sergio", "seth", "shane", "shannon", "sharon",
    "shawn", "sheila", "shelly", "sherry", "sheryl", "shirley", "sidney",
    "silvia", "simon", "sofia", "sonia", "stacey", "stacy", "stanley",
    "stephanie", "stephen", "steve", "steven", "stewart", "stuart", "susan",
    "sylvia",
    "tammy", "tanya", "tara", "ted", "teresa", "terrance", "terri", "terry",
    "theresa", "thomas", "tim", "timothy", "tina", "todd", "tom", "tommy",
    "tony", "tonya", "tracy", "travis", "trevor", "tyler",
    "valerie", "vanessa", "vera", "veronica", "vicki", "victor", "victoria",
    "vincent", "virginia",
    "walter", "wanda", "warren", "wayne", "wendy", "wesley", "whitney",
    "william", "willie", "wilson",
    "xavier",
    "yolanda", "yvonne",
    "zachary",
])


# Words that signal "this is a person's role" — used to boost name candidates
# when one of these appears in the same parent block as a name.
TITLE_WORDS = frozenset([
    "agent", "agents", "owner", "owners", "principal", "principals",
    "president", "vice president", "vp", "founder", "co-founder",
    "manager", "director", "ceo", "cfo", "coo", "broker", "brokers",
    "producer", "producers", "csr", "account manager", "account executive",
])


# Words that disqualify a phrase from being a person's name. Catches
# "Smith Insurance Agency", "Smith LLC", "General Inquiries", etc.
NON_PERSON_WORDS = frozenset([
    "inc", "llc", "co", "corp", "ltd", "insurance", "agency", "group",
    "company", "brokers", "broker", "associates", "services", "service",
    "solutions", "partners", "consultants", "agents", "and", "the",
    "general", "inquiries", "department",
])


# CTA labels that often prefix a real name on agency websites:
# "Email Dale Hodges", "Call Jane Doe", etc. Stripped before name validation.
LEADING_LABELS = frozenset(["email", "call", "contact", "meet", "ask", "message"])


# Name suffixes — when present, the part before the suffix is the lastname.
SUFFIXES = frozenset(["jr", "sr", "ii", "iii", "iv", "v"])


@dataclass(frozen=True)
class NameCandidate:
    """One person-name spotted on a page, with provenance."""
    full_name: str
    first_name: str
    last_name: str
    source: str               # "h1" | "h2" | "h3" | "h4" | "near_mailto"
    has_title_nearby: bool


def _looks_like_email(value: str) -> bool:
    """Cheap sanity check: has @ and a dot in the domain."""
    if not value or "@" not in value:
        return False
    local, _, domain = value.partition("@")
    return bool(local) and "." in domain


def extract_emails_from_html(html: str) -> list[str]:
    """Return all unique, lowercased email addresses found on the page.

    Two sources checked:
    1. mailto: anchor href values (strips ?subject= and handles
       comma-separated multi-address mailtos).
    2. Plain-text emails matched by EMAIL_REGEX.

    <script>, <style>, and <noscript> tags are stripped first.
    Generic locals (info@, sales@) are NOT filtered here.
    Known limitation: no de-obfuscation of '[at]' / '[dot]' patterns.
    """
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    found: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href.lower().startswith("mailto:"):
            continue
        body = href[len("mailto:"):].split("?", 1)[0]
        for email in body.split(","):
            email = email.strip()
            if _looks_like_email(email):
                found.add(email.lower())

    text = soup.get_text(" ", strip=True)
    for match in EMAIL_REGEX.finditer(text):
        email = match.group(0)
        if _looks_like_email(email):
            found.add(email.lower())

    return sorted(found)


def extract_name_from_email_local_part(email: str) -> tuple[str, str]:
    """Best-effort (first_name, last_name) from an email's local-part.

    Patterns recognized:
      - firstname.lastname@...   -> (First, Last). Relaxed: doesn't require
        firstname to be in COMMON_FIRST_NAMES; the dot is signal enough.
      - <known-first-name>@...   -> (First, ""). Conservative single-word path.

    Returns ('', '') for everything else.
    """
    if not email or "@" not in email:
        return "", ""
    local = email.split("@", 1)[0].lower()
    if local in GENERIC_LOCAL_PARTS:
        return "", ""

    if "." in local:
        first, _, rest = local.partition(".")
        last = rest.split(".")[0]
        if (
            first.isalpha() and 2 <= len(first) <= 20
            and last.isalpha() and 2 <= len(last) <= 20
            and first not in GENERIC_LOCAL_PARTS
            and last not in GENERIC_LOCAL_PARTS
        ):
            return first.capitalize(), last.capitalize()
        return "", ""

    if local.isalpha() and local in COMMON_FIRST_NAMES:
        return local.capitalize(), ""

    return "", ""


def _looks_like_person_name(text: str) -> tuple[str, str, str] | None:
    """Return (full, first, last) if text looks like a Title Case person name.

    Accepts:
      - John Smith
      - John M. Smith (middle initial)
      - Mary-Ann O'Brien (hyphens, apostrophes)
      - John Smith Jr (suffix; last = Smith)

    Rejects:
      - all-lowercase / ALL CAPS
      - single-word or 5+ words
      - phrases containing company-suffix words (Inc, LLC, Insurance, etc.)
    """
    text = " ".join(text.split())
    if not text or len(text) > 60 or len(text) < 4:
        return None
    parts = text.split()
    # Strip a leading action-verb label like "Email" or "Call" — the real
    # name (if any) follows it. "Email Dale Hodges" -> "Dale Hodges".
    if parts and parts[0].lower() in LEADING_LABELS:
        parts = parts[1:]
    if len(parts) < 2 or len(parts) > 4:
        return None

    for p in parts:
        if not re.match(r"^[A-Z][A-Za-z'\.\-]*$", p):
            return None
        # Reject ALL CAPS words (>1 letter, no lowercase). Allows initials like "M."
        letters_only = re.sub(r"[^A-Za-z]", "", p)
        if len(letters_only) > 1 and letters_only.isupper():
            return None
        if p.lower().rstrip(".") in NON_PERSON_WORDS:
            return None

    first = parts[0].rstrip(".")
    non_suffix = [p for p in parts[1:] if p.lower().rstrip(".") not in SUFFIXES]
    if not non_suffix:
        return None
    last = non_suffix[-1].rstrip(".")
    # full_name should reflect post-strip text, not the original (which may
    # include "Email"/"Call" prefix that we just removed).
    full_name = " ".join(parts)
    return full_name, first, last


def _has_title_word_near(element) -> bool:
    """True if any TITLE_WORDS appears in element.parent text (word-boundary)."""
    parent = element.parent
    if parent is None:
        return False
    text = parent.get_text(" ", strip=True).lower()
    for title in TITLE_WORDS:
        if re.search(r"\b" + re.escape(title) + r"\b", text):
            return True
    return False


def find_name_candidates(html: str) -> list[NameCandidate]:
    """Find person-name candidates on an HTML page.

    Searches headings (h1-h4) and mailto anchor display text. Each candidate
    has has_title_nearby=True if a TITLE_WORDS phrase appears in the same
    parent block — that's the team-card detection signal for 7e's scoring.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    candidates: list[NameCandidate] = []
    seen: set[str] = set()

    for tag_name in ("h1", "h2", "h3", "h4"):
        for heading in soup.find_all(tag_name):
            text = heading.get_text(" ", strip=True)
            result = _looks_like_person_name(text)
            if not result:
                continue
            full, first, last = result
            if full in seen:
                continue
            seen.add(full)
            candidates.append(NameCandidate(
                full_name=full, first_name=first, last_name=last,
                source=tag_name, has_title_nearby=_has_title_word_near(heading),
            ))

    for anchor in soup.find_all("a", href=lambda h: h and h.lower().startswith("mailto:")):
        text = anchor.get_text(" ", strip=True)
        result = _looks_like_person_name(text)
        if not result:
            continue
        full, first, last = result
        if full in seen:
            continue
        seen.add(full)
        candidates.append(NameCandidate(
            full_name=full, first_name=first, last_name=last,
            source="near_mailto", has_title_nearby=_has_title_word_near(anchor),
        ))

    return candidates


# --- 7e: scoring, name-email association, dedup -----------------------------

@dataclass(frozen=True)
class PairedLead:
    """One (email, name) pairing for a single page, with provenance + confidence."""
    email: str
    first_name: str
    last_name: str
    name_source: str    # team_page | contact_page | email_local_part | no_name_found
    confidence: int     # used to pick a winner across pages in dedupe_leads_by_email


# Base confidence by page type. Team pages are the most reliable source of
# person-level data; home and "other" are the noisiest.
_PAGE_TYPE_SCORE = {"team": 100, "contact": 80, "about": 60, "home": 40}


def _fragment_match_score(local: str, name: NameCandidate) -> int:
    """How well does a name appear in an email local-part? 0 if neither name overlaps."""
    first = name.first_name.lower()
    last = name.last_name.lower()
    first_in = bool(first) and first in local
    last_in = bool(last) and last in local
    if first_in and last_in:
        return 100
    if first_in or last_in:
        return 50
    return 0


def pair_names_to_emails(
    emails: list[str],
    names: list[NameCandidate],
    page_type: str = "other",
) -> list[PairedLead]:
    """Associate each email with a name via fragment match against the local-part.

    Falls back to parsing the local-part itself when no name matches.
    Generic locals (info@, sales@) skip the matching phase and emit as
    name_source='no_name_found'.

    page_type drives the base confidence (used by cross-page dedup) and
    chooses the source label: 'team_page' for team pages, 'contact_page'
    for everything else where an HTML name was found.
    """
    base_score = _PAGE_TYPE_SCORE.get(page_type, 20)
    html_source_label = "team_page" if page_type == "team" else "contact_page"

    paired: list[PairedLead] = []
    for email in emails:
        local = email.split("@", 1)[0].lower() if "@" in email else ""

        if not local or local in GENERIC_LOCAL_PARTS:
            paired.append(PairedLead(
                email=email, first_name="", last_name="",
                name_source="no_name_found", confidence=base_score,
            ))
            continue

        best_total: int = 0
        best_name: NameCandidate | None = None
        for name in names:
            match = _fragment_match_score(local, name)
            if match == 0:
                continue
            total = match + (20 if name.has_title_nearby else 0)
            if total > best_total:
                best_total = total
                best_name = name

        if best_name is not None:
            paired.append(PairedLead(
                email=email,
                first_name=best_name.first_name,
                last_name=best_name.last_name,
                name_source=html_source_label,
                confidence=base_score + best_total,
            ))
            continue

        first, last = extract_name_from_email_local_part(email)
        if first or last:
            paired.append(PairedLead(
                email=email, first_name=first, last_name=last,
                name_source="email_local_part",
                confidence=base_score // 2,
            ))
        else:
            paired.append(PairedLead(
                email=email, first_name="", last_name="",
                name_source="no_name_found",
                confidence=base_score // 4,
            ))

    return paired


def _lead_priority(lead: PairedLead) -> tuple[int, int, int]:
    """Sort key for choosing the best PairedLead among same-email candidates."""
    has_full_name = 1 if (lead.first_name and lead.last_name) else 0
    name_chars = len(lead.first_name) + len(lead.last_name)
    return (lead.confidence, has_full_name, name_chars)


def dedupe_leads_by_email(leads: list[PairedLead]) -> list[PairedLead]:
    """Keep one PairedLead per unique email, picking the highest-priority one.

    Ties broken by: more complete name (both first+last) > one name > none,
    then longer total name. Result sorted by email for determinism.
    """
    by_email: dict[str, PairedLead] = {}
    for lead in leads:
        existing = by_email.get(lead.email)
        if existing is None or _lead_priority(lead) > _lead_priority(existing):
            by_email[lead.email] = lead
    return sorted(by_email.values(), key=lambda L: L.email)


# --- 7f: enrich_agency orchestrator ----------------------------------------

# Subpaths walked on every agency website, in order. Homepage first (empty
# string), then most-likely-to-have-people pages.
SUBPATHS = [
    "",                  # homepage
    "/contact", "/contact-us",
    "/about", "/about-us",
    "/team", "/our-team", "/meet-the-team",
    "/staff",
    "/agents", "/our-agents",
]


def _classify_page_type(subpath: str) -> str:
    """Map a subpath to one of: home | team | contact | about | other."""
    if not subpath:
        return "home"
    s = subpath.lower()
    if "team" in s or "staff" in s or "agent" in s:
        return "team"
    if "contact" in s:
        return "contact"
    if "about" in s:
        return "about"
    return "other"


def _build_url(website_url: str, subpath: str) -> str:
    """Resolve subpath against the agency's homepage URL."""
    if not subpath:
        return website_url
    base = website_url if website_url.endswith("/") else website_url + "/"
    return urljoin(base, subpath.lstrip("/"))


def _status_lead(agency: AgencyRecord, status: str) -> EnrichedLead:
    """Build a placeholder EnrichedLead carrying agency fields + a diagnostic status."""
    return EnrichedLead(
        source_url=agency.source_url,
        company_name=agency.agency_name,
        phone=agency.phone,
        address=agency.address_line,
        city=agency.city,
        state=agency.state,
        zip=agency.zip,
        website=agency.website_url,
        enrichment_status=status,
        name_source="no_name_found",
    )


def _lead_from_pair(agency: AgencyRecord, paired: PairedLead) -> EnrichedLead:
    """Convert a per-page PairedLead into the final EnrichedLead row."""
    has_name = bool(paired.first_name or paired.last_name)
    return EnrichedLead(
        source_url=agency.source_url,
        company_name=agency.agency_name,
        phone=agency.phone,
        address=agency.address_line,
        city=agency.city,
        state=agency.state,
        zip=agency.zip,
        website=agency.website_url,
        email=paired.email,
        first_name=paired.first_name,
        last_name=paired.last_name,
        name_source=paired.name_source,
        enrichment_status="found" if has_name else "no_name_found",
    )


def enrich_agency(
    agency: AgencyRecord,
    session: requests.Session,
    limiter: PerDomainLimiter,
    on_event: Optional[callable] = None,
) -> list[EnrichedLead]:
    """Walk an agency's website to find (name, email) pairs.

    Returns:
      []                          if agency.website_url is empty
      [fetch_failed placeholder]  if NO page on the site could be fetched
      [no_email_found placeholder] if pages loaded but no emails surfaced
      [lead_1, lead_2, ...]       otherwise, one EnrichedLead per unique email

    Each lead's enrichment_status is 'found' when a name was attached (from
    HTML or local-part parsing), 'no_name_found' when only an email exists
    (e.g. info@). name_source records HOW the name was sourced.
    """
    if not agency.website_url:
        return []

    all_leads: list[PairedLead] = []
    any_page_fetched = False

    for subpath in SUBPATHS:
        url = _build_url(agency.website_url, subpath)
        html = fetch_url(url, session, limiter, on_event=on_event)
        if html is None:
            continue
        any_page_fetched = True

        emails = extract_emails_from_html(html)
        names = find_name_candidates(html)
        page_type = _classify_page_type(subpath)
        all_leads.extend(pair_names_to_emails(emails, names, page_type=page_type))

    if not any_page_fetched:
        return [_status_lead(agency, "fetch_failed")]

    deduped = dedupe_leads_by_email(all_leads)

    if not deduped:
        return [_status_lead(agency, "no_email_found")]

    return [_lead_from_pair(agency, p) for p in deduped]
