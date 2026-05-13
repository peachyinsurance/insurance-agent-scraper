"""Tests for scraper.sites.travelers — state_url, parse_state_page, parse_city_page."""

from scraper.sites.travelers import (
    parse_city_page,
    parse_state_page,
    state_url,
)


# --- state_url --------------------------------------------------------------

def test_state_url_lowercases_slug():
    assert state_url("ga") == "https://agent.travelers.com/ga"
    assert state_url("GA") == "https://agent.travelers.com/ga"
    assert state_url("tx") == "https://agent.travelers.com/tx"


# --- parse_state_page -------------------------------------------------------

def test_parse_state_page_extracts_city_urls():
    html = """
    <html><body>
    <ul>
        <li><a href="ga/acworth">Acworth</a></li>
        <li><a href="ga/atlanta">Atlanta</a></li>
        <li><a href="ga/macon">Macon</a></li>
    </ul>
    </body></html>
    """
    assert parse_state_page(html, "ga") == [
        "https://agent.travelers.com/ga/acworth",
        "https://agent.travelers.com/ga/atlanta",
        "https://agent.travelers.com/ga/macon",
    ]


def test_parse_state_page_ignores_other_states():
    html = """
    <a href="ga/atlanta">Atlanta</a>
    <a href="al/birmingham">Birmingham (AL)</a>
    """
    assert parse_state_page(html, "ga") == ["https://agent.travelers.com/ga/atlanta"]


def test_parse_state_page_ignores_anchor_and_empty_hrefs():
    html = """
    <a href="ga/atlanta">Atlanta</a>
    <a href="#section">Section anchor</a>
    <a href="">Empty</a>
    """
    assert parse_state_page(html, "ga") == ["https://agent.travelers.com/ga/atlanta"]


def test_parse_state_page_ignores_deeper_paths():
    html = """
    <a href="ga/atlanta">Atlanta</a>
    <a href="ga/atlanta/5-concourse-pkwy-4152-1">Agency in Atlanta</a>
    """
    assert parse_state_page(html, "ga") == ["https://agent.travelers.com/ga/atlanta"]


def test_parse_state_page_dedupes():
    html = """
    <a href="ga/atlanta">Atlanta</a>
    <a href="ga/atlanta">Atlanta repeated</a>
    """
    assert parse_state_page(html, "ga") == ["https://agent.travelers.com/ga/atlanta"]


# --- parse_city_page --------------------------------------------------------

def test_parse_city_page_extracts_agency_urls():
    html = """
    <ul>
        <li><a href="../ga/atlanta/5-concourse-pkwy-4152-1">Agency 1</a></li>
        <li><a href="../ga/atlanta/1-concourse-pkwy-50735-8">Agency 2</a></li>
    </ul>
    """
    result = parse_city_page(html, "https://agent.travelers.com/ga/atlanta")
    assert result == [
        "https://agent.travelers.com/ga/atlanta/1-concourse-pkwy-50735-8",
        "https://agent.travelers.com/ga/atlanta/5-concourse-pkwy-4152-1",
    ]


def test_parse_city_page_ignores_other_cities():
    html = """
    <a href="../ga/atlanta/foo-1-1">My Atlanta agency</a>
    <a href="../ga/macon/bar-2-2">Wrong city</a>
    """
    result = parse_city_page(html, "https://agent.travelers.com/ga/atlanta")
    assert result == ["https://agent.travelers.com/ga/atlanta/foo-1-1"]

# --- parse_agency_page ------------------------------------------------------

from scraper.sites.travelers import parse_agency_page


HTML_YEXT_ONLY = """
<html><head>
<script type="application/ld+json">
{"credentialSubject":{"name":"Snellings Walters Insurance Agency","email":"csnellings@snellingswalters.com","telephone":"+17703969600","url":"http://www.snellingswalters.com","address":{"@type":"PostalAddress","streetAddress":"5 Concourse Pkwy\\nSte 2700","addressLocality":"Atlanta","addressRegion":"GA","postalCode":"30328","addressCountry":"US"}}}
</script>
</head><body></body></html>
"""

HTML_INSURANCE_AGENCY_ONLY = """
<html><head>
<script type="application/ld+json">
{"@graph":[{"@type":"InsuranceAgency","name":"Snellings Walters Insurance Agency","telephone":"770.396.9600","url":"https://agent.travelers.com/ga/atlanta/foo","address":{"@type":"PostalAddress","streetAddress":"5 Concourse Pkwy","addressLocality":"Atlanta","addressRegion":"GA","postalCode":"30328"}}]}
</script>
</head><body></body></html>
"""

HTML_BOTH_BLOCKS = """
<html><head>
<script type="application/ld+json">
{"@graph":[{"@type":"InsuranceAgency","name":"Snellings Walters Insurance Agency","telephone":"770.396.9600","url":"https://agent.travelers.com/ga/atlanta/foo","address":{"@type":"PostalAddress","streetAddress":"5 Concourse Pkwy","addressLocality":"Atlanta","addressRegion":"GA","postalCode":"30328"}}]}
</script>
<script type="application/ld+json">
{"credentialSubject":{"name":"Snellings Walters Insurance Agency","email":"csnellings@snellingswalters.com","telephone":"+17703969600","url":"http://www.snellingswalters.com","address":{"@type":"PostalAddress","streetAddress":"5 Concourse Pkwy\\nSte 2700","addressLocality":"Atlanta","addressRegion":"GA","postalCode":"30328"}}}
</script>
</head></html>
"""


def test_parse_agency_page_uses_yext_credential_when_present():
    record = parse_agency_page(HTML_YEXT_ONLY, "https://agent.travelers.com/ga/atlanta/foo")
    assert record.agency_name == "Snellings Walters Insurance Agency"
    assert record.email == "csnellings@snellingswalters.com"
    assert record.website_url == "http://www.snellingswalters.com"
    assert record.phone == "(770) 396-9600"
    assert record.city == "Atlanta"
    assert record.state == "GA"
    assert record.zip == "30328"


def test_parse_agency_page_collapses_newline_in_street_address():
    record = parse_agency_page(HTML_YEXT_ONLY, "https://agent.travelers.com/ga/atlanta/foo")
    assert record.address_line == "5 Concourse Pkwy Ste 2700"


def test_parse_agency_page_falls_back_to_insurance_agency_block():
    record = parse_agency_page(HTML_INSURANCE_AGENCY_ONLY, "https://agent.travelers.com/ga/atlanta/foo")
    assert record.agency_name == "Snellings Walters Insurance Agency"
    assert record.email == ""             # no email in InsuranceAgency block
    assert record.website_url == ""       # url is travelers.com -> filtered
    assert record.phone == "(770) 396-9600"
    assert record.address_line == "5 Concourse Pkwy"  # no suite in this block


def test_parse_agency_page_prefers_yext_when_both_blocks_present():
    record = parse_agency_page(HTML_BOTH_BLOCKS, "https://agent.travelers.com/ga/atlanta/foo")
    # Yext block has email + real website URL + suite-included address
    assert record.email == "csnellings@snellingswalters.com"
    assert record.website_url == "http://www.snellingswalters.com"
    assert record.address_line == "5 Concourse Pkwy Ste 2700"


def test_parse_agency_page_returns_empty_record_when_no_jsonld():
    html = "<html><body><h1>Some page</h1></body></html>"
    record = parse_agency_page(html, "https://agent.travelers.com/ga/atlanta/foo")
    assert record.source_url == "https://agent.travelers.com/ga/atlanta/foo"
    assert record.agency_name == ""
    assert record.email == ""


def test_parse_agency_page_filters_travelers_self_url_from_website():
    record = parse_agency_page(HTML_INSURANCE_AGENCY_ONLY, "https://agent.travelers.com/ga/atlanta/foo")
    assert record.website_url == ""   # https://agent.travelers.com/... should not appear here


def test_parse_agency_page_lowercases_email():
    html = HTML_YEXT_ONLY.replace("csnellings@", "CSNELLINGS@")
    record = parse_agency_page(html, "https://agent.travelers.com/ga/atlanta/foo")
    assert record.email == "csnellings@snellingswalters.com"


def test_parse_agency_page_handles_malformed_jsonld_gracefully():
    html = """
    <html><head>
    <script type="application/ld+json">{this is not valid json}</script>
    </head></html>
    """
    record = parse_agency_page(html, "https://agent.travelers.com/ga/atlanta/foo")
    assert record.agency_name == ""    # graceful empty, no exception 