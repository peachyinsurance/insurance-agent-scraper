"""Tests for scraper.crm_backfill — filling Carrier/State on a HubSpot export.

Rows are modeled on real contacts from the 2026-10-08 export.
"""

import pytest

from scraper import crm_backfill as cb


def _row(first="", last="", email="", phone="", state="", carrier="", company=""):
    return {
        cb.FIRST_NAME_COL: first, cb.LAST_NAME_COL: last, cb.EMAIL_COL: email,
        cb.PHONE_COL: phone, cb.STATE_COL: state, cb.CARRIER_COL: carrier,
        cb.COMPANY_COL: company,
    }


# --- state_code / state_name ----------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("PA", "PA"), ("wv", "WV"), (" GA ", "GA"), ("Pennsylvania", "PA"),
    ("new mexico", "NM"), ("Ontario", ""), ("", ""), ("XX", ""),
])
def test_state_code(raw, expected):
    assert cb.state_code(raw) == expected


def test_state_name_round_trip():
    assert cb.state_name("NC") == "North Carolina"
    assert cb.state_name("") == ""


# --- carrier / internal / website -----------------------------------------

@pytest.mark.parametrize("email,expected", [
    ("jacks@allstate.com", "Allstate"),
    ("jeremywolcott@alltstate.com", "Allstate"),
    ("rblock@farmersagent.com", "Farmers"),
    ("tran.jacosta2@farmersagency.com", "Farmers"),
    ("lori.henry.lts9@statefarm.com", "State Farm"),
    ("dsmorrison@acg.aaa.com", "AAA"),
    ("tommy@sfgeorgia.com", ""),
    ("neitagroup@gmail.com", ""),
    ("", ""),
])
def test_carrier_from_email(email, expected):
    assert cb.carrier_from_email(email) == expected


def test_carrier_from_company_only_exact_carrier_names():
    assert cb.carrier_from_company("Allstate") == "Allstate"
    assert cb.carrier_from_company("AutoLife Insurance Group") == ""


@pytest.mark.parametrize("row", [
    _row("Sample", "Account", "admin@nextcallclub.com"),
    _row("Data", "", "data@peachyinsurance.com"),
    _row("test doe", "Doe", "p4horan@gmail.com", company="Pao Test"),
])
def test_is_internal(row):
    assert cb.is_internal(row)


def test_is_internal_does_not_match_substrings():
    assert not cb.is_internal(_row("Celeste", "Testa", "c@testaagency.com"))


@pytest.mark.parametrize("email,expected", [
    ("kevin@purdygoodcoverage.com", "https://purdygoodcoverage.com/"),
    ("jacks@allstate.com", ""),
    ("dsmorrison@acg.aaa.com", ""),
    ("neitagroup@gmail.com", ""),
    ("admin@nextcallclub.com", ""),
    ("not-an-email", ""),
])
def test_agency_website(email, expected):
    assert cb.agency_website(email) == expected


# --- names ----------------------------------------------------------------

@pytest.mark.parametrize("first,last,expected", [
    ("Jessica", "Guay", ("jessica", ["guay"])),
    ("Sean Dammann", "Dammann", ("sean", ["dammann"])),
    ("Jake Greco", "Greco II", ("jake", ["greco"])),
    ("Robert A", "Irwin", ("robert", ["irwin"])),
    ("DANIELLE", "LEE-BREEDY", ("danielle", ["lee", "breedy"])),
    ("Kevin", "O'Neil", ("kevin", ["oneil"])),
    ("Mary ", "Forrest", ("mary", ["forrest"])),
    ("Rob", "", ("rob", [])),
])
def test_split_name(first, last, expected):
    assert cb.split_name(first, last) == expected


@pytest.mark.parametrize("first,last,expected", [
    ("Sean Dammann", "Dammann", "Sean Dammann"),
    ("Catherine Van", "Tiem", "Catherine Van Tiem"),
    ("Jake Greco", "Greco II", "Jake Greco"),
    ("Alexandra Rivera", "Rivera", "Alexandra Rivera"),
    ("Rob", "", ""),
])
def test_display_name(first, last, expected):
    assert cb.display_name(first, last) == expected


@pytest.mark.parametrize("carrier,expected", [
    ("Farmers", '"Doug Pemberton" site:agents.farmers.com'),
    ("Allstate", '"Doug Pemberton" site:agents.allstate.com'),
    ("State Farm", '"Doug Pemberton" site:statefarm.com/agent'),
])
def test_search_query_restricts_to_known_carrier_locator(carrier, expected):
    assert cb.search_query("Doug", "Pemberton", carrier) == expected


@pytest.mark.parametrize("carrier", ["", "Independent", "AAA"])
def test_search_query_broad_when_no_locator(carrier):
    assert cb.search_query("Kevin", "Purdy", carrier) == '"Kevin Purdy" insurance agent'


def test_search_query_empty_without_last_name():
    assert cb.search_query("Rob", "", "") == ""


@pytest.mark.parametrize("slug,first,last,expected", [
    ("jessica-guay-rochester", "jessica", ["guay"], True),
    ("timothy-dangelo", "tim", ["dangelo"], True),
    ("jess-kaur", "jessica", ["kaur"], True),
    ("danielle-lee-breedy-baltimore", "danielle", ["lee", "breedy"], True),
    ("jessica-lopez", "jessica", ["jessen"], False),
    ("michael-hild", "mike", ["hild"], False),  # nickname, not a prefix
    ("jo-smith", "jo", ["smith"], True),  # exact short first name still works
    ("joe-smith", "jo", ["smith"], False),  # but not as a prefix under 3 chars
])
def test_slug_matches_name(slug, first, last, expected):
    assert cb.slug_matches_name(slug, first, last) is expected


# --- locator URLs ---------------------------------------------------------

@pytest.mark.parametrize("url,carrier,state,slug", [
    ("https://agents.allstate.com/jessica-guay-rochester-ny.html",
     "Allstate", "NY", "jessica-guay-rochester"),
    ("https://agents.farmers.com/mn/lino lakes/jesse-bentrup-sr/",
     "Farmers", "MN", "jesse-bentrup-sr"),
    ("https://agents.farmers.com/mn/lino-lakes/jesse-bentrup-sr",
     "Farmers", "MN", "jesse-bentrup-sr"),
    ("https://www.statefarm.com/agent/us/mn/mounds-view/jessica-thompson-vlz1b5gf7ak",
     "State Farm", "MN", "jessica-thompson-vlz1b5gf7ak"),
    ("https://agency.nationwide.com/paul-mears-in-murphy-nc",
     "Nationwide", "NC", "paul-mears"),
    # Directory listings from the first live run — carrier read from the slug.
    ("https://www.mapquest.com/us/alabama/clay-colvard-allstate-insurance-412710515",
     "Allstate", "AL", "clay-colvard-allstate-insurance"),
    ("https://www.mapquest.com/us/new-york/marc-desimone-allstate-insurance-346630340",
     "Allstate", "NY", "marc-desimone-allstate-insurance"),
    ("https://www.yellowpages.com/loveland-co/allstate-insurance-bj-hill",
     "Allstate", "CO", "allstate-insurance-bj-hill"),
    ("https://www.yellowpages.com/austin-tx/state-farm-insurance-jane-doe",
     "State Farm", "TX", "state-farm-insurance-jane-doe"),
])
def test_parse_locator_url(url, carrier, state, slug):
    hit = cb.parse_locator_url(url)
    assert (hit.carrier, hit.state, hit.slug) == (carrier, state, slug)


@pytest.mark.parametrize("url", [
    "https://agents.allstate.ca/on/windsor/3920-dougall-ave/jessica-rupcich",
    "https://agents.allstate.com/usa/pa/philadelphia",
    "https://agents.allstate.com/locator.html",
    "https://www.statefarm.com/agent/us/mn",
    "https://www.goosehead.com/agents/al/troy/jessica-goolsby/",
    "https://agents.allstate.com/someone-somewhere-xx.html",
    # Directory listing with no carrier in the slug.
    "https://www.mapquest.com/us/new-jersey/stephen-desimone-insurance-553323950",
    "https://www.mapquest.com/directions/to/us/new-york/marc-desimone-allstate-insurance-346630340",
    "https://www.mylocalservices.com/Marc+DeSimone+Allstate+Insurance-New+Hyde+Park-New+York-20026898.html",
    "https://www.whitepages.com/name/Stephanie-Hamilton/NJ",
    "not a url",
])
def test_parse_locator_url_rejects_non_agent_pages(url):
    assert cb.parse_locator_url(url) is None


def test_urls_by_query_groups_organic_results():
    items = [
        {"searchQuery": {"term": "q1"}, "organicResults": [{"url": "a"}, {"url": "b"}]},
        {"searchQuery": {"term": "q2"}, "organicResults": []},
        {"searchQuery": {"term": "q1", "page": 2}, "organicResults": [{"url": "c"}]},
        {"organicResults": [{"url": "orphan"}]},
    ]
    assert cb.urls_by_query(items) == {"q1": ["a", "b", "c"], "q2": []}


def test_google_search_payload_joins_queries():
    payload = cb.google_search_payload(["a", "b"])
    assert payload["queries"] == "a\nb"
    assert payload["maxPagesPerQuery"] == 1


# --- website / phone ------------------------------------------------------

def test_state_from_website_prefers_jsonld():
    html = """<script type="application/ld+json">
      {"@type": "InsuranceAgency", "address": {"addressRegion": "Georgia"}}
    </script><p>Visit us: 1 Main St, Austin, TX 78701</p>"""
    assert cb.state_from_website_html(html) == "GA"


def test_state_from_website_falls_back_to_address_text():
    html = "<footer>123 Roswell Rd, Marietta, GA 30062</footer>"
    assert cb.state_from_website_html(html) == "GA"


def test_state_from_website_ambiguous_when_offices_in_several_states():
    html = "<p>Marietta, GA 30062</p><p>Austin, TX 78701</p>"
    assert cb.state_from_website_html(html) == ""


def test_state_from_website_requires_zip():
    assert cb.state_from_website_html("<p>We do IT, OR anything</p>") == ""


@pytest.mark.parametrize("raw,expected", [
    (" +1 319-363-4008", "IA"),
    ("(504) 214-9702", "LA"),
    ("19085311866", "NJ"),
    ("17023711393", "NV"),
    (" +55 11 4949-9729", ""),   # Brazil
    ("9999999999", ""),          # placeholder
    ("1.18029E+11", ""),         # Excel-mangled
    ("", ""),
])
def test_state_from_phone(raw, expected):
    assert cb.state_from_phone(raw) == expected


# --- fill pipeline --------------------------------------------------------

def test_initial_fill_keeps_existing_values():
    fill = cb.initial_fill(_row("Jayne", "Young", "jayneyoung@allstate.com",
                                state="Missouri", carrier="Allstate"))
    assert (fill.carrier, fill.carrier_source) == ("Allstate", "existing")
    assert (fill.state, fill.state_source) == ("MO", "existing")
    assert fill.complete


def test_initial_fill_carrier_from_email_then_company():
    by_email = cb.initial_fill(_row(email="rblock@farmersagent.com"))
    assert (by_email.carrier, by_email.carrier_source) == ("Farmers", "email domain")
    by_company = cb.initial_fill(_row(email="tim@dieterinsurance.com", company="Allstate"))
    assert (by_company.carrier, by_company.carrier_source) == ("Allstate", "associated company")


def test_initial_fill_existing_carrier_beats_email_domain():
    # Don Neita: gmail address, CRM already says Allstate.
    fill = cb.initial_fill(_row(email="neitagroup@gmail.com", carrier="Allstate"))
    assert fill.carrier_source == "existing"


def test_initial_fill_unrecognized_state_is_kept_not_overwritten():
    row = _row("A", "B", phone="(504) 214-9702", state="Ontario")
    fill = cb.initial_fill(row)
    assert not fill.needs_state
    cb.apply_phone(fill, row)
    assert cb.output_row(row, fill)[cb.STATE_COL] == "Ontario"


def test_initial_fill_skips_internal():
    assert cb.initial_fill(_row("Sample", "Account", "admin@nextcallclub.com")).skipped


def test_apply_search_results_fills_carrier_and_state():
    row = _row("Kevin", "Purdy", "kevin@purdygoodcoverage.com")
    fill = cb.initial_fill(row)
    cb.apply_search_results(fill, row, [
        "https://www.linkedin.com/in/kevinpurdy",
        "https://agents.allstate.com/kevin-purdy-huntington-ny.html",
    ])
    assert (fill.carrier, fill.state) == ("Allstate", "NY")
    assert fill.carrier_source.startswith("locator: https://agents.allstate.com/")


def test_apply_search_results_bj_hill_from_live_run():
    # Real results: his Allstate page is under the agency name, so only the
    # Yellow Pages listing names him.
    row = _row("BJ", "Hill", "bj@summitvc.com")
    fill = cb.initial_fill(row)
    cb.apply_search_results(fill, row, [
        "https://agents.allstate.com/summit-insurance-loveland-co.html",
        "https://www.linkedin.com/in/belinda-bj-hill-53364247",
        "https://www.yellowpages.com/loveland-co/allstate-insurance-bj-hill",
    ])
    assert (fill.carrier, fill.state) == ("Allstate", "CO")


@pytest.mark.parametrize("slug,email,expected", [
    ("purdy-good-coverage-corp-commack", "kevin@purdygoodcoverage.com", True),
    ("summit-insurance-loveland", "bj@summitvc.com", False),
    ("someone-else-austin", "kevin@purdygoodcoverage.com", False),
    ("allstate-agency-x", "jacks@allstate.com", False),   # carrier domain
    ("gmail-insurance", "neitagroup@gmail.com", False),    # free mailbox
    ("best-ins-group", "x@ins.com", False),                # label too short
])
def test_slug_matches_email_domain(slug, email, expected):
    assert cb.slug_matches_email_domain(slug, email) is expected


def test_apply_search_results_kevin_purdy_from_live_run():
    # Real results: his Allstate page is listed under the agency name.
    row = _row("Kevin", "Purdy", "kevin@purdygoodcoverage.com", phone="16316823138")
    fill = cb.initial_fill(row)
    cb.apply_search_results(fill, row, [
        "https://www.linkedin.com/in/kevin-purdy",
        "https://agents.allstate.com/purdy-good-coverage-corp-commack-ny.html",
    ])
    assert (fill.carrier, fill.state) == ("Allstate", "NY")


def test_apply_search_results_flags_phone_disagreement():
    # Real case: 402 (Nebraska) number, matched an Allstate page in Indiana.
    row = _row("Ben", "Bucher", "ben@heartlandpolicy.com", phone=" +1 402-971-0155")
    fill = cb.initial_fill(row)
    cb.apply_search_results(fill, row, ["https://agents.allstate.com/ben-bucher-fort-wayne-in.html"])
    assert (fill.carrier, fill.state) == ("Allstate", "IN")
    assert fill.state_source.endswith("(CHECK: phone area code says NE)")
    assert fill.carrier_source.endswith("(CHECK: phone area code says NE)")


def test_apply_search_results_no_flag_when_phone_agrees():
    row = _row("Chris", "DeSimone", carrier="Allstate", phone="19085311866")
    fill = cb.initial_fill(row)
    cb.apply_search_results(fill, row, ["https://agents.allstate.com/chris-desimone-brick-nj.html"])
    assert fill.state_source == "locator: https://agents.allstate.com/chris-desimone-brick-nj.html"


def test_apply_search_results_ignores_other_names():
    row = _row("Jessica", "Jessen", carrier="Farmers")
    fill = cb.initial_fill(row)
    cb.apply_search_results(fill, row, ["https://agents.farmers.com/mn/fergus falls/jessica-lopez"])
    assert fill.state == ""


def test_apply_search_results_respects_known_carrier():
    # Same-name agent at another carrier must not set the state.
    row = _row("Doug", "Pemberton", carrier="Farmers")
    fill = cb.initial_fill(row)
    cb.apply_search_results(fill, row, [
        "https://www.statefarm.com/agent/us/tx/austin/doug-pemberton-abcdefghijk",
    ])
    assert fill.state == ""


def test_choose_hit_ambiguous_without_tiebreak():
    fill = cb.Fill(carrier="Allstate")
    hits = [cb.LocatorHit("Allstate", "TX", "s", "u1"), cb.LocatorHit("Allstate", "FL", "s", "u2")]
    assert cb.choose_hit(hits, fill) is None


def test_choose_hit_phone_breaks_tie():
    fill = cb.Fill(carrier="Allstate")
    hits = [cb.LocatorHit("Allstate", "TX", "s", "u1"), cb.LocatorHit("Allstate", "FL", "s", "u2")]
    assert cb.choose_hit(hits, fill, phone_state="FL").url == "u2"


def test_choose_hit_duplicate_urls_same_answer():
    fill = cb.Fill()
    hits = [cb.LocatorHit("Farmers", "MN", "s", "u1"), cb.LocatorHit("Farmers", "MN", "s", "u2")]
    assert cb.choose_hit(hits, fill).url == "u1"


def test_apply_phone_only_when_state_missing():
    row = _row(phone="(319) 530-5239")
    fill = cb.initial_fill(row)
    cb.apply_phone(fill, row)
    assert (fill.state, fill.state_source) == ("IA", "phone area code (low confidence)")


def test_apply_website_does_not_override_existing_state():
    fill = cb.Fill(state="GA", state_source="existing")
    cb.apply_website(fill, "<p>Austin, TX 78701</p>", "https://x.com/")
    assert fill.state == "GA"


def test_apply_website_flags_phone_disagreement():
    fill = cb.Fill()
    html = '<script type="application/ld+json">{"address": {"addressRegion": "CA"}}</script>'
    cb.apply_website(fill, html, "https://brambleinsurance.com/", phone_state="GA")
    assert fill.state == "CA"
    assert fill.state_source.endswith("(CHECK: phone area code says GA)")


def test_apply_website_no_flag_when_phone_agrees():
    fill = cb.Fill()
    cb.apply_website(fill, "<p>Corona, CA 92879</p>", "https://x.com/", phone_state="CA")
    assert fill.state_source == "website: https://x.com/"


def test_output_row_normalizes_state_and_adds_sources():
    row = _row("Kevin", "Kelly", "kevinkelly@kellyagency.us", state="PA")
    out = cb.output_row(row, cb.initial_fill(row))
    assert out[cb.STATE_COL] == "Pennsylvania"
    assert out[cb.STATE_SOURCE_COL] == "existing"
    assert out[cb.CARRIER_SOURCE_COL] == "not found"


def test_output_row_leaves_internal_rows_untouched():
    row = _row("Jason", "Boyd", "jason@peachyinsurance.com", state="GA")
    out = cb.output_row(row, cb.initial_fill(row))
    assert out[cb.STATE_COL] == "GA"
    assert out[cb.STATE_SOURCE_COL] == "skipped (internal/test)"
