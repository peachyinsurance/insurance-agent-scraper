"""Tests for scraper.enrichment — email + name extraction, dedup, orchestrator."""

from scraper.enrichment import extract_emails_from_html


# --- extract_emails_from_html -----------------------------------------------

def test_extract_emails_from_mailto_link():
    assert extract_emails_from_html('<a href="mailto:foo@bar.com">x</a>') == ["foo@bar.com"]


def test_extract_emails_strips_mailto_subject_param():
    html = '<a href="mailto:foo@bar.com?subject=Hello&body=hi">x</a>'
    assert extract_emails_from_html(html) == ["foo@bar.com"]


def test_extract_emails_handles_comma_separated_mailto():
    html = '<a href="mailto:a@x.com,b@y.com">Both</a>'
    assert extract_emails_from_html(html) == ["a@x.com", "b@y.com"]


def test_extract_emails_from_plain_text():
    html = '<p>Contact us at sales@example.com today.</p>'
    assert extract_emails_from_html(html) == ["sales@example.com"]


def test_extract_emails_dedupes_across_sources():
    html = '<a href="mailto:foo@bar.com">link</a> Or email foo@bar.com directly.'
    assert extract_emails_from_html(html) == ["foo@bar.com"]


def test_extract_emails_lowercases():
    assert extract_emails_from_html('<a href="mailto:Foo@Bar.COM">x</a>') == ["foo@bar.com"]


def test_extract_emails_handles_plus_in_local_part():
    html = '<p>Email me: agent+sales@example.com</p>'
    assert extract_emails_from_html(html) == ["agent+sales@example.com"]


def test_extract_emails_ignores_scripts():
    html = '<script>var x = "tracker@thirdparty.com";</script><p>real@example.com</p>'
    assert extract_emails_from_html(html) == ["real@example.com"]


def test_extract_emails_ignores_styles():
    html = '<style>/* contact@css.com */</style><p>real@example.com</p>'
    assert extract_emails_from_html(html) == ["real@example.com"]


def test_extract_emails_ignores_invalid_strings():
    html = '<p>Not an email: foo@bar (no TLD)</p>'
    assert extract_emails_from_html(html) == []


def test_extract_emails_returns_empty_list_when_none_found():
    html = '<html><body><h1>No emails here</h1></body></html>'
    assert extract_emails_from_html(html) == []


def test_extract_emails_sorted_alphabetically():
    html = '<a href="mailto:z@x.com">z</a> <a href="mailto:a@x.com">a</a>'
    assert extract_emails_from_html(html) == ["a@x.com", "z@x.com"]

from scraper.enrichment import extract_name_from_email_local_part


# --- extract_name_from_email_local_part -------------------------------------

def test_extract_name_returns_empty_for_empty_input():
    assert extract_name_from_email_local_part("") == ("", "")
    assert extract_name_from_email_local_part("not-an-email") == ("", "")


def test_extract_name_skips_generic_locals():
    assert extract_name_from_email_local_part("info@example.com") == ("", "")
    assert extract_name_from_email_local_part("sales@example.com") == ("", "")
    assert extract_name_from_email_local_part("noreply@example.com") == ("", "")


def test_extract_name_known_single_word_first_name():
    assert extract_name_from_email_local_part("kristine@example.com") == ("Kristine", "")
    assert extract_name_from_email_local_part("larry@example.com") == ("Larry", "")


def test_extract_name_unknown_single_word_returns_empty():
    # 'celisa' isn't in our common-names list — conservative path stays empty
    assert extract_name_from_email_local_part("celisa@example.com") == ("", "")
    assert extract_name_from_email_local_part("mwhatley@example.com") == ("", "")


def test_extract_name_firstname_dot_lastname():
    assert extract_name_from_email_local_part("caitlin.martin@example.com") == ("Caitlin", "Martin")


def test_extract_name_relaxed_dot_pattern_for_unknown_first():
    # 'randall' isn't in the common-names list, but the dot pattern is enough
    assert extract_name_from_email_local_part("randall.osborne@example.com") == ("Randall", "Osborne")


def test_extract_name_single_letter_first_skipped_in_dot_pattern():
    # j.smith@ -> dot pattern, but first is a single letter -> reject
    assert extract_name_from_email_local_part("j.smith@example.com") == ("", "")


def test_extract_name_skips_dot_pattern_if_either_side_generic():
    assert extract_name_from_email_local_part("info.team@example.com") == ("", "")
    assert extract_name_from_email_local_part("john.sales@example.com") == ("", "")


def test_extract_name_multi_dot_takes_first_two_parts():
    # foo.bar.baz@ -> take first two segments
    assert extract_name_from_email_local_part("foo.bar.baz@example.com") == ("Foo", "Bar")


def test_extract_name_lowercases_input():
    assert extract_name_from_email_local_part("KRISTINE@example.com") == ("Kristine", "")
    assert extract_name_from_email_local_part("CAITLIN.MARTIN@example.com") == ("Caitlin", "Martin")


def test_extract_name_rejects_non_alpha_segments():
    assert extract_name_from_email_local_part("john123@example.com") == ("", "")
    assert extract_name_from_email_local_part("john.smith2@example.com") == ("", "")


def test_extract_name_rejects_too_long_segments():
    # Pathological local-part with absurdly long segment
    very_long = "a" * 25
    assert extract_name_from_email_local_part(f"{very_long}.smith@example.com") == ("", "")

# --- find_name_candidates ---------------------------------------------------

from scraper.enrichment import find_name_candidates


def test_find_names_in_h2():
    html = '<h2>John Smith</h2>'
    candidates = find_name_candidates(html)
    assert len(candidates) == 1
    assert candidates[0].full_name == "John Smith"
    assert candidates[0].first_name == "John"
    assert candidates[0].last_name == "Smith"
    assert candidates[0].source == "h2"


def test_find_names_in_h3_and_h4():
    html = '<h3>Jane Doe</h3><h4>Bob Roberts</h4>'
    candidates = find_name_candidates(html)
    names = {c.full_name for c in candidates}
    assert names == {"Jane Doe", "Bob Roberts"}


def test_find_names_rejects_company_names():
    assert find_name_candidates('<h2>Smith Insurance Agency</h2>') == []
    assert find_name_candidates('<h2>Acme LLC</h2>') == []


def test_find_names_rejects_single_word():
    assert find_name_candidates('<h2>Smith</h2>') == []


def test_find_names_handles_middle_initial():
    html = '<h2>John M. Smith</h2>'
    candidates = find_name_candidates(html)
    assert len(candidates) == 1
    assert candidates[0].first_name == "John"
    assert candidates[0].last_name == "Smith"


def test_find_names_handles_suffix_jr():
    html = '<h2>John Smith Jr</h2>'
    candidates = find_name_candidates(html)
    assert len(candidates) == 1
    assert candidates[0].last_name == "Smith"


def test_find_names_handles_hyphenated_lastname():
    html = '<h2>Jane Smith-Jones</h2>'
    candidates = find_name_candidates(html)
    assert len(candidates) == 1
    assert candidates[0].last_name == "Smith-Jones"


def test_find_names_rejects_all_caps():
    assert find_name_candidates('<h2>JOHN SMITH</h2>') == []


def test_find_names_rejects_all_lowercase():
    assert find_name_candidates('<h2>john smith</h2>') == []


def test_find_names_dedupes_repeated():
    html = '<h2>John Smith</h2><h3>John Smith</h3>'
    candidates = find_name_candidates(html)
    assert len(candidates) == 1


def test_find_names_detects_title_word_nearby():
    html = '<div><h3>John Smith</h3><p>Senior Agent</p></div>'
    candidates = find_name_candidates(html)
    assert len(candidates) == 1
    assert candidates[0].has_title_nearby is True


def test_find_names_no_title_when_absent():
    html = '<div><h3>John Smith</h3><p>Born in 1970.</p></div>'
    candidates = find_name_candidates(html)
    assert candidates[0].has_title_nearby is False


def test_find_names_from_mailto_anchor_text():
    html = '<a href="mailto:js@x.com">John Smith</a>'
    candidates = find_name_candidates(html)
    assert len(candidates) == 1
    assert candidates[0].source == "near_mailto"


def test_find_names_ignores_scripts():
    html = '<script>var x = "John Smith"</script>'
    assert find_name_candidates(html) == []


def test_find_names_returns_empty_when_no_names():
    assert find_name_candidates('<html><body><h1>Welcome</h1></body></html>') == []


def test_find_names_title_word_uses_word_boundary():
    # "Insurance Agency" contains "agent" as substring — must NOT trigger
    html = '<div><h3>John Smith</h3><p>Snellings Walters Insurance Agency</p></div>'
    candidates = find_name_candidates(html)
    assert candidates[0].has_title_nearby is False
# --- pair_names_to_emails + dedupe_leads_by_email --------------------------

from scraper.enrichment import (
    NameCandidate,
    PairedLead,
    dedupe_leads_by_email,
    pair_names_to_emails,
)


def _name(first, last, has_title=False):
    """Helper to build a NameCandidate for tests."""
    return NameCandidate(
        full_name=f"{first} {last}",
        first_name=first,
        last_name=last,
        source="h3",
        has_title_nearby=has_title,
    )


def test_pair_matches_full_first_and_last_in_local_part():
    emails = ["jane.doe@x.com"]
    names = [_name("Jane", "Doe")]
    result = pair_names_to_emails(emails, names, page_type="team")
    assert len(result) == 1
    assert result[0].first_name == "Jane"
    assert result[0].last_name == "Doe"
    assert result[0].name_source == "team_page"


def test_pair_matches_first_only():
    emails = ["jane@x.com"]
    names = [_name("Jane", "Doe")]
    result = pair_names_to_emails(emails, names, page_type="contact")
    assert result[0].first_name == "Jane"
    assert result[0].name_source == "contact_page"


def test_pair_matches_last_only():
    emails = ["doe@x.com"]
    names = [_name("Jane", "Doe")]
    result = pair_names_to_emails(emails, names, page_type="team")
    assert result[0].last_name == "Doe"


def test_pair_picks_highest_score_when_multiple_candidates_match():
    # Both names overlap "jane" but Jane Doe matches first+last; Jane Smith only first
    emails = ["jane.doe@x.com"]
    names = [_name("Jane", "Smith"), _name("Jane", "Doe")]
    result = pair_names_to_emails(emails, names, page_type="team")
    assert result[0].last_name == "Doe"


def test_pair_title_word_boost_breaks_ties():
    # Both candidates match "jane" only; the one with has_title_nearby wins
    emails = ["jane@x.com"]
    names = [_name("Jane", "Smith"), _name("Jane", "Doe", has_title=True)]
    result = pair_names_to_emails(emails, names, page_type="contact")
    assert result[0].last_name == "Doe"


def test_pair_falls_back_to_local_part_when_no_name_matches():
    emails = ["randall.osborne@x.com"]
    names = [_name("Jane", "Doe")]  # no overlap
    result = pair_names_to_emails(emails, names, page_type="home")
    assert result[0].first_name == "Randall"
    assert result[0].last_name == "Osborne"
    assert result[0].name_source == "email_local_part"


def test_pair_marks_no_name_found_when_local_part_unparseable():
    emails = ["xyz123@x.com"]
    result = pair_names_to_emails(emails, [], page_type="home")
    assert result[0].name_source == "no_name_found"
    assert result[0].first_name == ""


def test_pair_generic_emails_get_no_name_found():
    emails = ["info@x.com", "sales@x.com"]
    names = [_name("Jane", "Doe")]  # would otherwise be a candidate
    result = pair_names_to_emails(emails, names, page_type="contact")
    assert all(L.name_source == "no_name_found" for L in result)
    assert all(L.first_name == "" for L in result)


def test_pair_empty_emails_returns_empty():
    assert pair_names_to_emails([], [_name("Jane", "Doe")], page_type="team") == []


def test_pair_uses_contact_page_label_for_about_and_home():
    # Spec only has team_page / contact_page; home/about lump into contact_page
    emails = ["jane.doe@x.com"]
    names = [_name("Jane", "Doe")]
    assert pair_names_to_emails(emails, names, page_type="home")[0].name_source == "contact_page"
    assert pair_names_to_emails(emails, names, page_type="about")[0].name_source == "contact_page"


def test_pair_team_page_outscores_contact_page_in_confidence():
    emails = ["jane.doe@x.com"]
    names = [_name("Jane", "Doe")]
    team = pair_names_to_emails(emails, names, page_type="team")[0]
    contact = pair_names_to_emails(emails, names, page_type="contact")[0]
    assert team.confidence > contact.confidence


# --- dedupe ---

def test_dedupe_keeps_higher_confidence():
    high = PairedLead(email="x@y.com", first_name="Jane", last_name="Doe",
                      name_source="team_page", confidence=200)
    low  = PairedLead(email="x@y.com", first_name="Jane", last_name="Doe",
                      name_source="contact_page", confidence=100)
    result = dedupe_leads_by_email([low, high])
    assert len(result) == 1
    assert result[0].confidence == 200
    assert result[0].name_source == "team_page"


def test_dedupe_prefers_full_name_on_tied_confidence():
    full = PairedLead(email="x@y.com", first_name="Jane", last_name="Doe",
                     name_source="team_page", confidence=100)
    partial = PairedLead(email="x@y.com", first_name="Jane", last_name="",
                        name_source="team_page", confidence=100)
    result = dedupe_leads_by_email([partial, full])
    assert result[0].last_name == "Doe"


def test_dedupe_returns_distinct_emails():
    a = PairedLead(email="a@x.com", first_name="A", last_name="X",
                   name_source="team_page", confidence=100)
    b = PairedLead(email="b@x.com", first_name="B", last_name="Y",
                   name_source="team_page", confidence=100)
    result = dedupe_leads_by_email([a, b])
    assert {L.email for L in result} == {"a@x.com", "b@x.com"}


def test_dedupe_sorts_by_email():
    a = PairedLead(email="z@x.com", first_name="Z", last_name="",
                   name_source="team_page", confidence=10)
    b = PairedLead(email="a@x.com", first_name="A", last_name="",
                   name_source="team_page", confidence=10)
    result = dedupe_leads_by_email([a, b])
    assert [L.email for L in result] == ["a@x.com", "z@x.com"]

# --- enrich_agency (full orchestrator) -------------------------------------

import time

import pytest
import requests
import responses

from scraper.core.http import PerDomainLimiter
from scraper.enrichment import enrich_agency
from scraper.records import AgencyRecord


# All subpaths the orchestrator visits — used by tests that want to register
# 404s for everything except the page they care about.
ALL_SUBPATHS = [
    "/contact", "/contact-us", "/about", "/about-us",
    "/team", "/our-team", "/meet-the-team", "/staff",
    "/agents", "/our-agents",
]


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)


@pytest.fixture
def session():
    return requests.Session()


@pytest.fixture
def limiter():
    return PerDomainLimiter(interval_range=(0.0, 0.0))


def _make_agency(website="http://example.com"):
    return AgencyRecord(
        source_url="https://progressive.test/agency/foo",
        agency_name="ACME Insurance",
        address_line="123 Main St",
        city="Anytown",
        state="GA",
        zip="30000",
        phone="(555) 123-4567",
        website_url=website,
    )


def _register_404s_for_subpaths(website, except_for=()):
    """Register 404 for every subpath EXCEPT those in except_for."""
    for sp in ALL_SUBPATHS:
        if sp in except_for:
            continue
        responses.add(responses.GET, website + sp, status=404)


def test_enrich_agency_returns_empty_when_no_website(no_sleep, session, limiter):
    agency = _make_agency(website="")
    assert enrich_agency(agency, session, limiter) == []


@responses.activate
def test_enrich_agency_fetch_failed_when_all_pages_fail(no_sleep, session, limiter):
    website = "http://example.com"
    responses.add(responses.GET, website, status=404)
    _register_404s_for_subpaths(website)
    leads = enrich_agency(_make_agency(website=website), session, limiter)
    assert len(leads) == 1
    assert leads[0].enrichment_status == "fetch_failed"


@responses.activate
def test_enrich_agency_no_email_found_when_pages_have_no_emails(no_sleep, session, limiter):
    website = "http://example.com"
    responses.add(responses.GET, website, body="<h1>Welcome</h1>", status=200)
    _register_404s_for_subpaths(website)
    leads = enrich_agency(_make_agency(website=website), session, limiter)
    assert len(leads) == 1
    assert leads[0].enrichment_status == "no_email_found"


@responses.activate
def test_enrich_agency_emits_one_lead_per_email(no_sleep, session, limiter):
    website = "http://example.com"
    home = '<a href="mailto:jane.doe@x.com">Jane Doe</a><a href="mailto:bob@x.com">Bob</a>'
    responses.add(responses.GET, website, body=home, status=200)
    _register_404s_for_subpaths(website)
    leads = enrich_agency(_make_agency(website=website), session, limiter)
    assert {L.email for L in leads} == {"jane.doe@x.com", "bob@x.com"}


@responses.activate
def test_enrich_agency_pairs_names_with_emails(no_sleep, session, limiter):
    website = "http://example.com"
    home = '<h3>Jane Doe</h3><a href="mailto:jane.doe@x.com">email</a>'
    responses.add(responses.GET, website, body=home, status=200)
    _register_404s_for_subpaths(website)
    leads = enrich_agency(_make_agency(website=website), session, limiter)
    assert len(leads) == 1
    assert leads[0].first_name == "Jane"
    assert leads[0].last_name == "Doe"
    assert leads[0].enrichment_status == "found"


@responses.activate
def test_enrich_agency_dedupes_email_across_pages(no_sleep, session, limiter):
    website = "http://example.com"
    home = '<a href="mailto:jane@x.com">jane</a>'
    team = '<h3>Jane Doe</h3><a href="mailto:jane@x.com">jane</a>'
    responses.add(responses.GET, website, body=home, status=200)
    responses.add(responses.GET, website + "/team", body=team, status=200)
    _register_404s_for_subpaths(website, except_for=("/team",))
    leads = enrich_agency(_make_agency(website=website), session, limiter)
    assert len(leads) == 1
    # Team page version (with full name) should win
    assert leads[0].first_name == "Jane"
    assert leads[0].last_name == "Doe"


@responses.activate
def test_enrich_agency_falls_back_to_local_part(no_sleep, session, limiter):
    website = "http://example.com"
    responses.add(responses.GET, website,
                  body="Reach us at randall.osborne@x.com", status=200)
    _register_404s_for_subpaths(website)
    leads = enrich_agency(_make_agency(website=website), session, limiter)
    assert len(leads) == 1
    assert leads[0].first_name == "Randall"
    assert leads[0].last_name == "Osborne"
    assert leads[0].name_source == "email_local_part"


@responses.activate
def test_enrich_agency_status_no_name_for_generic_email(no_sleep, session, limiter):
    website = "http://example.com"
    responses.add(responses.GET, website, body="Email: info@example.com", status=200)
    _register_404s_for_subpaths(website)
    leads = enrich_agency(_make_agency(website=website), session, limiter)
    assert len(leads) == 1
    assert leads[0].email == "info@example.com"
    assert leads[0].enrichment_status == "no_name_found"
    assert leads[0].first_name == ""


@responses.activate
def test_enrich_agency_carries_all_agency_fields(no_sleep, session, limiter):
    website = "http://example.com"
    responses.add(responses.GET, website,
                  body='<a href="mailto:foo@x.com">x</a>', status=200)
    _register_404s_for_subpaths(website)
    leads = enrich_agency(_make_agency(website=website), session, limiter)
    assert leads[0].company_name == "ACME Insurance"
    assert leads[0].phone == "(555) 123-4567"
    assert leads[0].address == "123 Main St"
    assert leads[0].city == "Anytown"
    assert leads[0].state == "GA"
    assert leads[0].zip == "30000"
    assert leads[0].website == website
    assert leads[0].source_url == "https://progressive.test/agency/foo"


@responses.activate
def test_enrich_agency_works_when_only_a_subpath_responds(no_sleep, session, limiter):
    website = "http://example.com"
    responses.add(responses.GET, website, status=404)
    responses.add(responses.GET, website + "/contact",
                  body='<a href="mailto:jane@x.com">jane</a>', status=200)
    _register_404s_for_subpaths(website, except_for=("/contact",))
    leads = enrich_agency(_make_agency(website=website), session, limiter)
    assert len(leads) == 1
    assert leads[0].email == "jane@x.com"
    assert leads[0].first_name == "Jane"  # 'jane' is in COMMON_FIRST_NAMES, falls back to local-part
    assert leads[0].enrichment_status == "found"

# --- LEADING_LABELS and updated NON_PERSON_WORDS ---------------------------

def test_find_names_strips_leading_email_label():
    """'Email Dale Hodges' should parse as Dale Hodges, not first=Email."""
    candidates = find_name_candidates('<h3>Email Dale Hodges</h3>')
    assert len(candidates) == 1
    assert candidates[0].full_name == "Dale Hodges"
    assert candidates[0].first_name == "Dale"
    assert candidates[0].last_name == "Hodges"


def test_find_names_strips_leading_call_label():
    candidates = find_name_candidates('<h3>Call Jane Doe</h3>')
    assert len(candidates) == 1
    assert candidates[0].first_name == "Jane"


def test_find_names_strips_leading_contact_label():
    candidates = find_name_candidates('<h3>Contact Bob Roberts</h3>')
    assert len(candidates) == 1
    assert candidates[0].first_name == "Bob"


def test_find_names_rejects_email_with_no_name_after_label():
    # "Email" alone shouldn't produce a candidate
    assert find_name_candidates('<h3>Email</h3>') == []


def test_find_names_rejects_general_inquiries():
    assert find_name_candidates('<h3>General Inquiries</h3>') == []


def test_find_names_dedupes_label_prefixed_against_bare_name():
    # If "John Smith" and "Email John Smith" both appear, only one candidate
    html = '<h2>John Smith</h2><a href="mailto:js@x.com">Email John Smith</a>'
    candidates = find_name_candidates(html)
    assert len(candidates) == 1
    assert candidates[0].full_name == "John Smith"
