"""Tests for scraper.sites.state_farm — sitemap iteration, slug parsing, JSON-LD parse."""

import pytest
import requests
import responses

from scraper.core.http import PerDomainLimiter
from scraper.sites.state_farm import (
    SITEMAP_URL,
    iter_agency_urls,
    parse_agency_page,
    parse_url_slug,
)


# --- shared fixtures --------------------------------------------------------

@pytest.fixture
def session():
    return requests.Session()


@pytest.fixture
def limiter():
    return PerDomainLimiter(interval_range=(0.0, 0.0))


# --- iter_agency_urls (mocked HTTP) -----------------------------------------

# Mix of 3-segment state pages, 4-segment city pages, and 5-segment agent
# pages — same shape as the real State Farm sitemap.
SAMPLE_SITEMAP = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <url><loc>https://www.statefarm.com/agent/us/ga</loc></url>
    <url><loc>https://www.statefarm.com/agent/us/ga/atlanta</loc></url>
    <url><loc>https://www.statefarm.com/agent/us/ga/atlanta/jane-smith-abc12345xyz</loc></url>
    <url><loc>https://www.statefarm.com/agent/us/ga/buckeye/john-doe-def67890abc</loc></url>
    <url><loc>https://www.statefarm.com/agent/us/tx/austin/sarah-wong-ghi98765mno</loc></url>
    <url><loc>https://www.statefarm.com/agent/us/tx</loc></url>
</urlset>
"""


@responses.activate
def test_iter_agency_urls_yields_only_5_segment_agent_urls(session, limiter):
    responses.add(responses.GET, SITEMAP_URL, body=SAMPLE_SITEMAP, status=200)
    urls = list(iter_agency_urls(session, limiter))
    assert urls == [
        "https://www.statefarm.com/agent/us/ga/atlanta/jane-smith-abc12345xyz",
        "https://www.statefarm.com/agent/us/ga/buckeye/john-doe-def67890abc",
        "https://www.statefarm.com/agent/us/tx/austin/sarah-wong-ghi98765mno",
    ]


@responses.activate
def test_iter_agency_urls_filters_by_state(session, limiter):
    responses.add(responses.GET, SITEMAP_URL, body=SAMPLE_SITEMAP, status=200)
    urls = list(iter_agency_urls(session, limiter, state_slug="ga"))
    assert urls == [
        "https://www.statefarm.com/agent/us/ga/atlanta/jane-smith-abc12345xyz",
        "https://www.statefarm.com/agent/us/ga/buckeye/john-doe-def67890abc",
    ]


@responses.activate
def test_iter_agency_urls_state_filter_is_case_insensitive(session, limiter):
    responses.add(responses.GET, SITEMAP_URL, body=SAMPLE_SITEMAP, status=200)
    urls = list(iter_agency_urls(session, limiter, state_slug="GA"))
    assert len(urls) == 2
    assert all("/ga/" in u for u in urls)


@responses.activate
def test_iter_agency_urls_skips_malformed_loc_entries(session, limiter):
    sitemap = """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <url><loc>https://www.statefarm.com/agent/us/ga/atlanta/jane-smith-abc12345xyz</loc></url>
    <url><loc></loc></url>
    <url></url>
</urlset>
"""
    responses.add(responses.GET, SITEMAP_URL, body=sitemap, status=200)
    urls = list(iter_agency_urls(session, limiter))
    assert urls == ["https://www.statefarm.com/agent/us/ga/atlanta/jane-smith-abc12345xyz"]


@responses.activate
def test_iter_agency_urls_raises_runtimeerror_on_fetch_failure(session, limiter):
    responses.add(responses.GET, SITEMAP_URL, status=500)
    responses.add(responses.GET, SITEMAP_URL, status=500)
    responses.add(responses.GET, SITEMAP_URL, status=500)
    with pytest.raises(RuntimeError, match="Failed to fetch"):
        list(iter_agency_urls(session, limiter))


@responses.activate
def test_iter_agency_urls_raises_runtimeerror_on_malformed_xml(session, limiter):
    responses.add(responses.GET, SITEMAP_URL, body="not xml at all", status=200)
    with pytest.raises(RuntimeError, match="did not parse"):
        list(iter_agency_urls(session, limiter))


# --- parse_url_slug (pure) --------------------------------------------------

def test_parse_url_slug_happy_path():
    result = parse_url_slug(
        "https://www.statefarm.com/agent/us/ga/forest-park/harold-mitchell-vp4jn1ys000"
    )
    assert result == {
        "state": "ga",
        "city": "forest-park",
        "first_name_guess": "Harold",
        "last_name_guess": "Mitchell",
        "agent_id": "vp4jn1ys000",
    }


def test_parse_url_slug_preserves_multi_word_last_name():
    result = parse_url_slug(
        "https://www.statefarm.com/agent/us/ga/atlanta/terri-cade-hill-1s26m1ys000"
    )
    assert result["first_name_guess"] == "Terri"
    assert result["last_name_guess"] == "Cade Hill"
    assert result["agent_id"] == "1s26m1ys000"


def test_parse_url_slug_preserves_jr_suffix():
    result = parse_url_slug(
        "https://www.statefarm.com/agent/us/ga/forest-park/harold-mitchell-jr-vp4jn1ys000"
    )
    assert result["first_name_guess"] == "Harold"
    assert result["last_name_guess"] == "Mitchell Jr"
    assert result["agent_id"] == "vp4jn1ys000"


def test_parse_url_slug_single_token_name():
    result = parse_url_slug(
        "https://www.statefarm.com/agent/us/ga/atlanta/cher-abc12345xyz"
    )
    assert result["first_name_guess"] == "Cher"
    assert result["last_name_guess"] == ""
    assert result["agent_id"] == "abc12345xyz"


def test_parse_url_slug_no_id_when_trailing_token_wrong_length():
    # Trailing token "abc" is 3 chars, not 11 — whole slug treated as name.
    result = parse_url_slug(
        "https://www.statefarm.com/agent/us/ga/atlanta/john-smith-abc"
    )
    assert result["first_name_guess"] == "John"
    assert result["last_name_guess"] == "Smith Abc"
    assert result["agent_id"] == ""


def test_parse_url_slug_returns_empty_for_state_url():
    result = parse_url_slug("https://www.statefarm.com/agent/us/ga")
    assert result == {
        "state": "", "city": "",
        "first_name_guess": "", "last_name_guess": "",
        "agent_id": "",
    }


def test_parse_url_slug_returns_empty_for_city_url():
    result = parse_url_slug("https://www.statefarm.com/agent/us/ga/atlanta")
    assert all(v == "" for v in result.values())


def test_parse_url_slug_returns_empty_for_unrelated_url():
    result = parse_url_slug("https://www.statefarm.com/about/leadership")
    assert all(v == "" for v in result.values())


# --- parse_agency_page ------------------------------------------------------

# Real-shape JSON-LD modeled on Harold Mitchell's actual State Farm page.
# Includes one Offer block + the InsuranceAgency block to mirror production
# (production pages have ~9 JSON-LD blocks, mostly product Offers).
HTML_FULL = """
<html><head>
<script type="application/ld+json">
{"@type": "Offer", "name": "Auto Insurance"}
</script>
<script type="application/ld+json">
{
    "@context": "http://schema.org/",
    "@type": ["InsuranceAgency"],
    "name": "Harold Mitchell Jr - State Farm Agent - Forest Park, GA",
    "telephone": ["404-366-0059"],
    "address": {
        "@type": "PostalAddress",
        "addressLocality": "Forest Park",
        "addressRegion": "GA",
        "postalCode": "30297-1472",
        "streetAddress": "4972 Phillips Drive"
    },
    "url": "https://www.statefarm.com/agent/us/ga/forest-park/harold-mitchell-vp4jn1ys000",
    "sameAs": ["www.haroldmitchell.net"],
    "founder": {"@type": "Person", "name": "Harold Mitchell Jr"}
}
</script>
</head><body></body></html>
"""

SOURCE_URL = "https://www.statefarm.com/agent/us/ga/forest-park/harold-mitchell-vp4jn1ys000"


def test_parse_agency_page_happy_path():
    record = parse_agency_page(HTML_FULL, SOURCE_URL)
    assert record.source_url == SOURCE_URL
    assert record.agency_name == "Harold Mitchell Jr"
    assert record.address_line == "4972 Phillips Drive"
    assert record.city == "Forest Park"
    assert record.state == "GA"
    assert record.zip == "30297-1472"
    assert record.phone == "(404) 366-0059"
    assert record.website_url == "https://www.haroldmitchell.net"
    assert record.email == ""


def test_parse_agency_page_email_always_empty():
    # State Farm doesn't expose emails; field stays empty even if some future
    # parser change tried to surface one. Clay handles email enrichment.
    record = parse_agency_page(HTML_FULL, SOURCE_URL)
    assert record.email == ""


def test_parse_agency_page_handles_string_telephone():
    html = HTML_FULL.replace('"telephone": ["404-366-0059"]', '"telephone": "404-366-0059"')
    record = parse_agency_page(html, SOURCE_URL)
    assert record.phone == "(404) 366-0059"


def test_parse_agency_page_handles_string_sameAs():
    html = HTML_FULL.replace(
        '"sameAs": ["www.haroldmitchell.net"]',
        '"sameAs": "www.haroldmitchell.net"',
    )
    record = parse_agency_page(html, SOURCE_URL)
    assert record.website_url == "https://www.haroldmitchell.net"


def test_parse_agency_page_missing_sameAs_yields_empty_website():
    html = HTML_FULL.replace('"sameAs": ["www.haroldmitchell.net"],', "")
    record = parse_agency_page(html, SOURCE_URL)
    assert record.website_url == ""


def test_parse_agency_page_filters_statefarm_self_link_from_website():
    html = HTML_FULL.replace(
        '"sameAs": ["www.haroldmitchell.net"]',
        '"sameAs": ["https://www.statefarm.com/agent/us/ga/forest-park/harold-mitchell-vp4jn1ys000"]',
    )
    record = parse_agency_page(html, SOURCE_URL)
    assert record.website_url == ""


def test_parse_agency_page_filters_st8fm_self_link_from_website():
    html = HTML_FULL.replace(
        '"sameAs": ["www.haroldmitchell.net"]',
        '"sameAs": ["https://ac1.st8fm.com/some/asset"]',
    )
    record = parse_agency_page(html, SOURCE_URL)
    assert record.website_url == ""


def test_parse_agency_page_keeps_explicit_https_url_unchanged():
    html = HTML_FULL.replace(
        '"sameAs": ["www.haroldmitchell.net"]',
        '"sameAs": ["https://haroldmitchell.com"]',
    )
    record = parse_agency_page(html, SOURCE_URL)
    assert record.website_url == "https://haroldmitchell.com"


def test_parse_agency_page_falls_back_to_top_level_name_when_founder_empty():
    html = HTML_FULL.replace(
        '"founder": {"@type": "Person", "name": "Harold Mitchell Jr"}',
        '"founder": {}',
    )
    record = parse_agency_page(html, SOURCE_URL)
    # Top-level name has marketing suffix that should be stripped.
    assert record.agency_name == "Harold Mitchell Jr"


def test_parse_agency_page_strips_marketing_suffix_from_top_level_name():
    # Verify the suffix-strip path works when there's no founder block at all.
    html = HTML_FULL.replace(
        ',\n    "founder": {"@type": "Person", "name": "Harold Mitchell Jr"}',
        "",
    )
    record = parse_agency_page(html, SOURCE_URL)
    assert record.agency_name == "Harold Mitchell Jr"


def test_parse_agency_page_handles_string_type_not_list():
    # @type can be either a string or a single-element list — handle both.
    html = HTML_FULL.replace('"@type": ["InsuranceAgency"]', '"@type": "InsuranceAgency"')
    record = parse_agency_page(html, SOURCE_URL)
    assert record.agency_name == "Harold Mitchell Jr"


def test_parse_agency_page_handles_partial_address():
    html = HTML_FULL.replace(
        '"addressLocality": "Forest Park",\n        "addressRegion": "GA",\n        '
        '"postalCode": "30297-1472",\n        "streetAddress": "4972 Phillips Drive"',
        '"addressLocality": "Forest Park",\n        "addressRegion": "GA"',
    )
    record = parse_agency_page(html, SOURCE_URL)
    assert record.city == "Forest Park"
    assert record.state == "GA"
    assert record.zip == ""
    assert record.address_line == ""


def test_parse_agency_page_no_insurance_agency_block_returns_empty_record():
    # Page has Offer JSON-LD but no InsuranceAgency block — defensive empty.
    html = """
    <html><body>
    <script type="application/ld+json">{"@type": "Offer", "name": "Auto"}</script>
    </body></html>
    """
    record = parse_agency_page(html, SOURCE_URL)
    assert record.source_url == SOURCE_URL
    assert record.agency_name == ""
    assert record.email == ""
    assert record.phone == ""


def test_parse_agency_page_no_jsonld_returns_empty_record():
    html = "<html><body><h1>nothing here</h1></body></html>"
    record = parse_agency_page(html, SOURCE_URL)
    assert record.source_url == SOURCE_URL
    assert record.agency_name == ""


def test_parse_agency_page_handles_malformed_jsonld_gracefully():
    html = """
    <html><body>
    <script type="application/ld+json">{not valid json}</script>
    </body></html>
    """
    record = parse_agency_page(html, SOURCE_URL)
    # Malformed block is silently skipped by iter_jsonld_blocks; no crash.
    assert record.agency_name == ""
