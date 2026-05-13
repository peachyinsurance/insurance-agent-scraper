"""Tests for scraper.core.crawl — run_crawl orchestrator."""

import time
import types

import pytest
import requests
import responses

from scraper.core.crawl import run_crawl
from scraper.core.http import MAX_RETRIES, PerDomainLimiter
from scraper.records import AgencyRecord


# --- fixtures ---------------------------------------------------------------

@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)


@pytest.fixture
def session():
    return requests.Session()


@pytest.fixture
def limiter():
    return PerDomainLimiter(interval_range=(0.0, 0.0))


def make_site(
    parse_state_page_fn=None,
    parse_city_page_fn=None,
    parse_agency_page_fn=None,
):
    """Build a stub site adapter as a SimpleNamespace."""
    if parse_state_page_fn is None:
        parse_state_page_fn = lambda html, slug: [
            "https://fake.com/state/city1/",
            "https://fake.com/state/city2/",
        ]
    if parse_city_page_fn is None:
        parse_city_page_fn = lambda html, city: [
            f"{city}agency1/",
            f"{city}agency2/",
        ]
    if parse_agency_page_fn is None:
        parse_agency_page_fn = lambda html, url: AgencyRecord(
            source_url=url, agency_name="ACME"
        )
    return types.SimpleNamespace(
        state_url=lambda slug: "https://fake.com/state/",
        parse_state_page=parse_state_page_fn,
        parse_city_page=parse_city_page_fn,
        parse_agency_page=parse_agency_page_fn,
    )


def register_200(*urls):
    for url in urls:
        responses.add(responses.GET, url, body="ok", status=200)


# --- happy path -------------------------------------------------------------

@responses.activate
def test_run_crawl_happy_path(no_sleep, session, limiter):
    register_200(
        "https://fake.com/state/",
        "https://fake.com/state/city1/",
        "https://fake.com/state/city2/",
        "https://fake.com/state/city1/agency1/",
        "https://fake.com/state/city1/agency2/",
        "https://fake.com/state/city2/agency1/",
        "https://fake.com/state/city2/agency2/",
    )
    records = []
    seen = set()
    stats = run_crawl(make_site(), "ga", records.append, session, limiter, seen)
    assert stats.cities_visited == 2
    assert stats.agencies_emitted == 4
    assert len(records) == 4
    assert seen == {(r.source_url,) for r in records}


# --- resumability / filtering -----------------------------------------------

@responses.activate
def test_run_crawl_skips_seen_urls(no_sleep, session, limiter):
    register_200(
        "https://fake.com/state/",
        "https://fake.com/state/city1/",
        "https://fake.com/state/city2/",
        "https://fake.com/state/city1/agency2/",
        "https://fake.com/state/city2/agency1/",
        "https://fake.com/state/city2/agency2/",
    )
    records = []
    seen = {("https://fake.com/state/city1/agency1/",)}
    stats = run_crawl(make_site(), "ga", records.append, session, limiter, seen)
    assert stats.agencies_emitted == 3
    assert stats.agencies_skipped_seen == 1
    assert len(records) == 3


@responses.activate
def test_run_crawl_respects_limit(no_sleep, session, limiter):
    register_200(
        "https://fake.com/state/",
        "https://fake.com/state/city1/",
        "https://fake.com/state/city1/agency1/",
        "https://fake.com/state/city1/agency2/",
    )
    records = []
    stats = run_crawl(make_site(), "ga", records.append, session, limiter, set(), limit=1)
    assert stats.agencies_emitted == 1
    assert stats.limit_reached is True


@responses.activate
def test_run_crawl_start_from_skips_earlier_urls(no_sleep, session, limiter):
    register_200(
        "https://fake.com/state/",
        "https://fake.com/state/city1/",
        "https://fake.com/state/city2/",
        "https://fake.com/state/city1/agency2/",
        "https://fake.com/state/city2/agency1/",
        "https://fake.com/state/city2/agency2/",
    )
    records = []
    stats = run_crawl(
        make_site(), "ga", records.append, session, limiter, set(),
        start_from="https://fake.com/state/city1/agency2/",
    )
    assert stats.agencies_skipped_start_from == 1
    assert stats.agencies_emitted == 3


# --- failure modes ----------------------------------------------------------

@responses.activate
def test_run_crawl_state_page_fetch_failure(no_sleep, session, limiter):
    for _ in range(MAX_RETRIES):
        responses.add(responses.GET, "https://fake.com/state/", status=500)
    records = []
    stats = run_crawl(make_site(), "ga", records.append, session, limiter, set())
    assert stats.cities_visited == 0
    assert stats.agencies_emitted == 0


@responses.activate
def test_run_crawl_city_page_fetch_failure(no_sleep, session, limiter):
    responses.add(responses.GET, "https://fake.com/state/", body="ok", status=200)
    for _ in range(MAX_RETRIES):
        responses.add(responses.GET, "https://fake.com/state/city1/", status=500)
    register_200(
        "https://fake.com/state/city2/",
        "https://fake.com/state/city2/agency1/",
        "https://fake.com/state/city2/agency2/",
    )
    records = []
    stats = run_crawl(make_site(), "ga", records.append, session, limiter, set())
    assert stats.cities_visited == 1
    assert stats.agencies_emitted == 2


@responses.activate
def test_run_crawl_agency_fetch_failure(no_sleep, session, limiter):
    register_200(
        "https://fake.com/state/",
        "https://fake.com/state/city1/",
        "https://fake.com/state/city2/",
    )
    for _ in range(MAX_RETRIES):
        responses.add(responses.GET, "https://fake.com/state/city1/agency1/", status=500)
    register_200(
        "https://fake.com/state/city1/agency2/",
        "https://fake.com/state/city2/agency1/",
        "https://fake.com/state/city2/agency2/",
    )
    records = []
    stats = run_crawl(make_site(), "ga", records.append, session, limiter, set())
    assert stats.agencies_failed_fetch == 1
    assert stats.agencies_emitted == 3


@responses.activate
def test_run_crawl_agency_parse_returns_empty_record(no_sleep, session, limiter):
    register_200(
        "https://fake.com/state/",
        "https://fake.com/state/city1/",
        "https://fake.com/state/city1/agency1/",
        "https://fake.com/state/city1/agency2/",
    )
    def parse_agency(html, url):
        if url.endswith("agency1/"):
            return AgencyRecord(source_url=url, agency_name="")
        return AgencyRecord(source_url=url, agency_name="ACME")
    site = make_site(
        parse_state_page_fn=lambda h, s: ["https://fake.com/state/city1/"],
        parse_agency_page_fn=parse_agency,
    )
    records = []
    stats = run_crawl(site, "ga", records.append, session, limiter, set())
    assert stats.agencies_failed_parse == 1
    assert stats.agencies_emitted == 1


@responses.activate
def test_run_crawl_agency_parse_raises(no_sleep, session, limiter):
    register_200(
        "https://fake.com/state/",
        "https://fake.com/state/city1/",
        "https://fake.com/state/city1/agency1/",
        "https://fake.com/state/city1/agency2/",
    )
    def parse_agency(html, url):
        if url.endswith("agency1/"):
            raise ValueError("bad HTML")
        return AgencyRecord(source_url=url, agency_name="ACME")
    site = make_site(
        parse_state_page_fn=lambda h, s: ["https://fake.com/state/city1/"],
        parse_agency_page_fn=parse_agency,
    )
    events = []
    stats = run_crawl(
        site, "ga", lambda r: None, session, limiter, set(),
        on_event=lambda n, d: events.append((n, d)),
    )
    assert stats.agencies_failed_parse == 1
    assert stats.agencies_emitted == 1
    parse_failed = [e for e in events if e[0] == "parse_failed"]
    assert len(parse_failed) == 1
    assert "bad HTML" in parse_failed[0][1]["reason"]


# --- events -----------------------------------------------------------------

@responses.activate
def test_run_crawl_emits_events(no_sleep, session, limiter):
    register_200(
        "https://fake.com/state/",
        "https://fake.com/state/city1/",
        "https://fake.com/state/city2/",
        "https://fake.com/state/city1/agency1/",
        "https://fake.com/state/city1/agency2/",
        "https://fake.com/state/city2/agency1/",
        "https://fake.com/state/city2/agency2/",
    )
    events = []
    run_crawl(
        make_site(), "ga", lambda r: None, session, limiter, set(),
        on_event=lambda n, d: events.append((n, d)),
    )
    names = [e[0] for e in events]
    assert "cities_found" in names
    assert names.count("city_starting") == 2