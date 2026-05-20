"""Tests for scraper.sites.aaa — pure helpers for the Apify integration.

The Apify API call itself (start run, poll, fetch dataset) is an integration
boundary and not unit-tested — it's verified by smoke tests against the real
service. These tests cover the deterministic pieces: query building, payload
shape, and item→row mapping.
"""

import pytest

from scraper.sites.aaa import (
    APIFY_ACTOR_ID,
    DEFAULT_PLACES_PER_STATE,
    apify_item_to_row,
    build_apify_input,
    is_aaa_result,
    query_for_state,
    state_full_name,
)


# --- query_for_state --------------------------------------------------------

def test_query_for_state_uppercases_slug():
    assert query_for_state("tx") == "AAA agents in TX"
    assert query_for_state("TX") == "AAA agents in TX"
    assert query_for_state("ga") == "AAA agents in GA"


# --- state_full_name --------------------------------------------------------

def test_state_full_name_happy_path():
    assert state_full_name("tx") == "Texas"
    assert state_full_name("ga") == "Georgia"
    assert state_full_name("ca") == "California"
    assert state_full_name("dc") == "District of Columbia"


def test_state_full_name_is_case_insensitive():
    assert state_full_name("TX") == "Texas"
    assert state_full_name("Ga") == "Georgia"


def test_state_full_name_raises_on_unknown_slug():
    # Catches typos and made-up slugs before they reach the Apify API.
    with pytest.raises(ValueError, match="Unknown state slug"):
        state_full_name("xx")
    with pytest.raises(ValueError):
        state_full_name("texas")  # not a 2-letter code


# --- build_apify_input ------------------------------------------------------

def test_build_apify_input_uses_default_limit_when_none():
    payload = build_apify_input("tx")
    assert payload["searchStringsArray"] == ["AAA agents in TX"]
    assert payload["maxCrawledPlacesPerSearch"] == DEFAULT_PLACES_PER_STATE


def test_build_apify_input_respects_explicit_limit():
    payload = build_apify_input("ga", limit=50)
    assert payload["maxCrawledPlacesPerSearch"] == 50


def test_build_apify_input_disables_reviews_and_images():
    # We don't need reviews/images and they slow runs — must default off.
    payload = build_apify_input("tx")
    assert payload["includeReviews"] is False
    assert payload["includeImages"] is False


def test_build_apify_input_includes_structured_state_and_country():
    # Critical for result correctness: without these fields the actor's
    # proxy region biases the results (TX query returned CA on 2026-05-14).
    # countryCode is intentionally lowercase — the actor's input schema
    # rejects "US" with HTTP 400 invalid-input.
    payload = build_apify_input("tx")
    assert payload["state"] == "Texas"
    assert payload["countryCode"] == "us"


def test_build_apify_input_state_is_full_name_not_code():
    # Actor docs explicitly require full state name ("Massachusetts") not
    # 2-letter code ("MA"). Regression guard against accidentally passing
    # the slug.
    payload_ma = build_apify_input("ma")
    assert payload_ma["state"] == "Massachusetts"
    payload_ny = build_apify_input("ny")
    assert payload_ny["state"] == "New York"


def test_build_apify_input_raises_on_unknown_state():
    with pytest.raises(ValueError):
        build_apify_input("zz")


# --- apify_item_to_row ------------------------------------------------------

SAMPLE_QUERY = "AAA agents in TX"


def test_apify_item_to_row_happy_path():
    item = {
        "title": "AAA Texas - Houston Office",
        "website": "https://texas.aaa.com/houston",
        "phone": "(713) 555-0100",
        "street": "1234 Main St",
        "city": "Houston",
        "state": "TX",
        "postalCode": "77002",
        "placeId": "ChIJtest123",
        "url": "https://maps.google.com/?cid=12345",
        "categoryName": "Insurance agency",
        "totalScore": 4.5,
        "reviewsCount": 120,
    }
    row = apify_item_to_row(item, source_query=SAMPLE_QUERY)
    assert row["company_name"] == "AAA Texas - Houston Office"
    assert row["website"] == "https://texas.aaa.com/houston"
    assert row["phone"] == "(713) 555-0100"
    assert row["street_address"] == "1234 Main St"
    assert row["city"] == "Houston"
    assert row["state"] == "TX"
    assert row["postal_code"] == "77002"
    assert row["google_place_id"] == "ChIJtest123"
    assert row["google_maps_url"] == "https://maps.google.com/?cid=12345"
    assert row["category"] == "Insurance agency"
    assert row["rating"] == "4.5"
    assert row["review_count"] == "120"
    assert row["source_query"] == SAMPLE_QUERY


def test_apify_item_to_row_missing_fields_become_empty_strings():
    # Apify results vary — some places lack website, phone, or category.
    # Missing keys must not raise; they should produce '' in the CSV row.
    item = {"title": "Sparse Result", "placeId": "ChIJsparse"}
    row = apify_item_to_row(item, source_query=SAMPLE_QUERY)
    assert row["company_name"] == "Sparse Result"
    assert row["google_place_id"] == "ChIJsparse"
    assert row["website"] == ""
    assert row["phone"] == ""
    assert row["street_address"] == ""
    assert row["city"] == ""
    assert row["postal_code"] == ""
    assert row["rating"] == ""
    assert row["review_count"] == ""
    assert row["category"] == ""
    assert row["source_query"] == SAMPLE_QUERY


def test_apify_item_to_row_handles_none_values():
    # Apify sometimes returns explicit nulls for missing data, not just
    # omitting the key. _str() coerces None to '' so the CSV doesn't get
    # the literal string 'None'.
    item = {
        "title": None,
        "website": None,
        "totalScore": None,
        "reviewsCount": None,
        "placeId": "ChIJnone",
    }
    row = apify_item_to_row(item, source_query=SAMPLE_QUERY)
    assert row["company_name"] == ""
    assert row["website"] == ""
    assert row["rating"] == ""
    assert row["review_count"] == ""
    assert row["google_place_id"] == "ChIJnone"


def test_apify_item_to_row_stringifies_numeric_rating():
    # totalScore comes back as a float; CSV writer wants strings.
    item = {"title": "X", "placeId": "p", "totalScore": 4.0, "reviewsCount": 0}
    row = apify_item_to_row(item, source_query=SAMPLE_QUERY)
    assert row["rating"] == "4.0"
    assert row["review_count"] == "0"


def test_apify_item_to_row_propagates_source_query():
    item = {"title": "Y", "placeId": "p"}
    row = apify_item_to_row(item, source_query="AAA agents in CA")
    assert row["source_query"] == "AAA agents in CA"


# --- is_aaa_result ----------------------------------------------------------
# Tightened 2026-05-20: requires BOTH the "AAA " title prefix AND an
# aaa.com / *.aaa.com website host. The 118-item TX corpus showed ~50%
# impostor rate with just the name prefix (AAA Loans, AAA Bail Bonds,
# AAA Security Guard, etc. — businesses unrelated to the AAA brand).

def test_is_aaa_result_accepts_branches_on_regional_aaa_subdomain():
    # Real TX/Southwest AAA listings use ace.aaa.com. Both signals present.
    assert is_aaa_result({
        "title": "AAA Garland Insurance and Member Services",
        "website": "https://www.ace.aaa.com/locations/Garland-75040-103",
    })
    assert is_aaa_result({
        "title": "AAA Plano",
        "website": "https://www.ace.aaa.com/locations.html",
    })


def test_is_aaa_result_accepts_other_regional_aaa_subdomains():
    # AAA's federation has many regional clubs on different subdomains;
    # Mountain West Group is mwg.aaa.com. All match the *.aaa.com rule.
    assert is_aaa_result({
        "title": "AAA Newark Branch",
        "website": "https://mwg.aaa.com/agent-office-locations/ca/newark/aaa-newark-branch",
    })


def test_is_aaa_result_accepts_bare_aaa_com_host():
    # National-portal listings (no regional subdomain).
    assert is_aaa_result({"title": "AAA Insurance - Elly Greenfield",
                          "website": "http://www.aaa.com/"})
    assert is_aaa_result({"title": "AAA Something", "website": "https://aaa.com"})


def test_is_aaa_result_accepts_bare_hostname_without_scheme():
    # urlparse needs a scheme to extract hostname; helper prepends http://
    # so bare hostnames like 'www.ace.aaa.com' still work.
    assert is_aaa_result({"title": "AAA X", "website": "www.ace.aaa.com"})


def test_is_aaa_result_rejects_aaaa_prefix():
    # Title check fails before website check is even reached. The
    # AAAA-prefix impostor surfaced consistently in the TX corpus.
    assert not is_aaa_result({
        "title": "AAAA AUTO INSURANCE",
        "website": "https://www.aaaains.com/",
    })
    assert not is_aaa_result({"title": "AAAAA Something",
                              "website": "https://www.aaa.com/"})


def test_is_aaa_result_rejects_aaa_prefix_with_non_aaa_website():
    # The 2026-05-20 corpus showed many "AAA *" businesses with their own
    # domains. These passed the name-only filter; domain check catches them.
    assert not is_aaa_result({
        "title": "AAA Loans & Tax Services",
        "website": "http://aaaloanco.com/",
    })
    assert not is_aaa_result({
        "title": "AAA Security Guard Service Dallas, Texas",
        "website": "https://aaaguards.com/security-guards-dallas-tx",
    })
    assert not is_aaa_result({
        "title": "AAA Infinity Insurance",
        "website": "http://www.gotinfinity.com/",
    })
    assert not is_aaa_result({
        "title": "AAA Bail Bonds",
        "website": "https://aaabaillubbock.com/",
    })
    assert not is_aaa_result({
        "title": "AAA Insurance Center",
        "website": "https://aaainsurancecenter.com/",
    })


def test_is_aaa_result_rejects_substring_aaa_com_in_other_domains():
    # Defensive: a naive `"aaa.com" in url` check would match domains like
    # `notaaa.com` or `aaa.com.attacker.com`. Using parsed .hostname with
    # equality / .endswith('.aaa.com') excludes them correctly.
    assert not is_aaa_result({
        "title": "AAA Fake",
        "website": "https://www.notaaa.com/",
    })
    assert not is_aaa_result({
        "title": "AAA Fake",
        "website": "https://aaa.com.attacker.com/",
    })


def test_is_aaa_result_rejects_missing_or_empty_website():
    # Trade-off accepted on 2026-05-20: legit AAA listings without any
    # website get dropped (~2 per 100 results in TX). Better than letting
    # ~25 per 100 impostors through.
    assert not is_aaa_result({"title": "AAA texas"})
    assert not is_aaa_result({"title": "AAA Houston-Memorial Branch", "website": ""})
    assert not is_aaa_result({"title": "AAA Real Estate", "website": None})


def test_is_aaa_result_rejects_aaa_without_trailing_space():
    # Name prefix is still required even with a legit aaa.com website.
    # "AAA-Dallas" with hyphen breaks brand convention.
    assert not is_aaa_result({"title": "AAA", "website": "https://www.aaa.com/"})
    assert not is_aaa_result({"title": "AAA-Dallas", "website": "https://www.aaa.com/"})


def test_is_aaa_result_is_case_sensitive_on_title():
    # AAA brand is always uppercase; lowercase variants are suspect.
    assert not is_aaa_result({"title": "aaa garland",
                              "website": "https://www.ace.aaa.com/"})
    assert not is_aaa_result({"title": "Aaa Plano",
                              "website": "https://www.ace.aaa.com/"})


def test_is_aaa_result_rejects_missing_or_empty_title():
    assert not is_aaa_result({})
    assert not is_aaa_result({"title": None, "website": "https://www.aaa.com/"})
    assert not is_aaa_result({"title": "", "website": "https://www.aaa.com/"})


# --- constants --------------------------------------------------------------

def test_apify_actor_id_constant():
    # Pinning the actor ID — if it ever changes, tests should catch the rename.
    assert APIFY_ACTOR_ID == "compass/crawler-google-places"
