"""Generic state -> city -> agency crawl loop. Site adapter is a duck-typed module."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional

import requests

from scraper.core.http import PerDomainLimiter, fetch_url
from scraper.records import AgencyRecord


@dataclass
class CrawlStats:
    """Counters from one run_crawl invocation."""
    cities_visited: int = 0
    agencies_emitted: int = 0
    agencies_skipped_seen: int = 0
    agencies_skipped_start_from: int = 0
    agencies_failed_fetch: int = 0
    agencies_failed_parse: int = 0
    limit_reached: bool = False


def run_crawl(
    site,
    state_slug: str,
    on_record: Callable[[AgencyRecord], None],
    session: requests.Session,
    limiter: PerDomainLimiter,
    seen_keys: set[tuple[str, ...]],
    limit: Optional[int] = None,
    start_from: Optional[str] = None,
    on_event: Optional[Callable[[str, dict], None]] = None,
) -> CrawlStats:
    """Walk the directory: state -> cities -> agencies. Stream records via on_record.

    site must expose: state_url, parse_state_page, parse_city_page, parse_agency_page.
    seen_keys is mutated: every emitted agency URL is added as (url,).
    """
    stats = CrawlStats()
    state_url = site.state_url(state_slug)

    state_html = fetch_url(state_url, session, limiter, on_event=on_event)
    if state_html is None:
        return stats

    city_urls = site.parse_state_page(state_html, state_slug)
    _emit(on_event, "cities_found", state=state_slug, count=len(city_urls))

    for city_idx, city_url in enumerate(city_urls, 1):
        _emit(on_event, "city_starting",
              city_url=city_url, idx=city_idx, total=len(city_urls))

        city_html = fetch_url(city_url, session, limiter, on_event=on_event)
        if city_html is None:
            continue

        agency_urls = site.parse_city_page(city_html, city_url)
        stats.cities_visited += 1

        for agency_url in agency_urls:
            if start_from is not None and agency_url < start_from:
                stats.agencies_skipped_start_from += 1
                continue
            if (agency_url,) in seen_keys:
                stats.agencies_skipped_seen += 1
                continue

            agency_html = fetch_url(agency_url, session, limiter, on_event=on_event)
            if agency_html is None:
                stats.agencies_failed_fetch += 1
                continue

            try:
                record = site.parse_agency_page(agency_html, agency_url)
            except Exception as exc:
                _emit(on_event, "parse_failed", url=agency_url, reason=str(exc))
                stats.agencies_failed_parse += 1
                continue

            if not record.agency_name:
                _emit(on_event, "parse_empty", url=agency_url)
                stats.agencies_failed_parse += 1
                continue

            on_record(record)
            seen_keys.add((agency_url,))
            stats.agencies_emitted += 1

            if limit is not None and stats.agencies_emitted >= limit:
                stats.limit_reached = True
                return stats

    return stats


def _emit(on_event, name, **data):
    if on_event is not None:
        on_event(name, data)