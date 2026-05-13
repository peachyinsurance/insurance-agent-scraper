"""CLI dispatcher for the per-site scraping pipeline.

Usage:
  Stage 1 — crawl directory:
    python scrape.py --site travelers --stage 1 --state ga
    python scrape.py --site travelers --stage 1 --state ga --limit 50

  Stage 2 — enrich each agency's website for (name, email):
    python scrape.py --site travelers --stage 2
    python scrape.py --site travelers --stage 2 --limit 50

Output files (per site):
  stage1_<site>_agents.csv      — raw AgencyRecord rows from the directory
  stage2_<site>_enriched.csv    — Instantly-ready EnrichedLead rows (one per person)

Both stages are resumable: rerunning with the same args skips rows that are
already in the output CSV. Ctrl+C is safe — rows flush to disk before each
next HTTP request.
"""

import argparse
import csv
import os
import sys
from dataclasses import asdict, fields

import requests

from scraper.core.crawl import run_crawl
from scraper.core.csv_io import append_row, load_seen_keys
from scraper.core.http import PerDomainLimiter
from scraper.enrichment import enrich_agency, extract_name_from_email_local_part
from scraper.records import AgencyRecord, EnrichedLead
from scraper.sites import travelers


SITES = {
    "travelers": travelers,
    # progressive: not ported into the new architecture yet — use the
    # standalone progressive_scraper.py at the project root for that carrier.
}


STAGE1_COLUMNS = [
    "agency_name", "address_line", "city", "state", "zip",
    "phone", "website_url", "email", "source_url",
]

STAGE2_COLUMNS = [
    "email", "first_name", "last_name", "name_source", "company_name",
    "phone", "address", "city", "state", "zip", "website",
    "source_url", "enrichment_status",
]


# --- output paths ----------------------------------------------------------

def _stage1_csv_path(site_name: str) -> str:
    return f"stage1_{site_name}_agents.csv"


def _stage2_csv_path(site_name: str) -> str:
    return f"stage2_{site_name}_enriched.csv"


# --- event printing --------------------------------------------------------

def _print_event(name: str, data: dict) -> None:
    """Single sink for HTTP + crawl events.

    Silent for 'success' and 'retry' — retries are usually one-off transient
    blips and the final 'fail' line carries the same info. If you need
    per-attempt visibility for debugging, swap this for a verbose sink.
    """
    if name == "fail":
        print(f"  [fail] {data['reason']} on {data['url']}")
    elif name == "cities_found":
        print(f"  Found {data['count']} cities in {data['state']}\n")
    elif name == "city_starting":
        print(f"[city {data['idx']}/{data['total']}] {data['city_url']}")
    elif name == "parse_failed":
        print(f"    [parse_failed] {data['url']}: {data['reason']}")
    elif name == "parse_empty":
        print(f"    [parse_empty] {data['url']}")


# --- Stage 1 ---------------------------------------------------------------

def run_stage1(site_name: str, state: str, limit: int | None, start_from: str | None) -> None:
    site = SITES[site_name]
    csv_path = _stage1_csv_path(site_name)
    seen_keys = load_seen_keys(csv_path, ("source_url",))

    print(f"Stage 1: site={site_name}  state={state}")
    print(f"  Output: {csv_path}")
    print(f"  Already saved: {len(seen_keys)} rows (will be skipped)")
    if limit:
        print(f"  Limit: stop after {limit} new agencies")
    if start_from:
        print(f"  Start-from: skip URLs alphabetically before {start_from}")
    print()

    limiter = PerDomainLimiter()
    count_with_email = 0
    count_without_email = 0

    def on_record(record):
        nonlocal count_with_email, count_without_email
        append_row(csv_path, asdict(record), STAGE1_COLUMNS)
        tag = record.email or "(no email)"
        print(f"    {record.agency_name} | {record.phone} | {tag}")
        if record.email:
            count_with_email += 1
        else:
            count_without_email += 1

    with requests.Session() as session:
        stats = run_crawl(
            site=site,
            state_slug=state,
            on_record=on_record,
            session=session,
            limiter=limiter,
            seen_keys=seen_keys,
            limit=limit,
            start_from=start_from,
            on_event=_print_event,
        )

    print()
    print(f"Stage 1 done.")
    print(f"  Cities visited:                 {stats.cities_visited}")
    print(f"  Agencies emitted:               {stats.agencies_emitted}")
    print(f"    with email in directory:      {count_with_email}")
    print(f"    needing Stage 2 enrichment:   {count_without_email}")
    print(f"  Skipped (already in CSV):       {stats.agencies_skipped_seen}")
    print(f"  Skipped (start-from):           {stats.agencies_skipped_start_from}")
    print(f"  Failed fetch:                   {stats.agencies_failed_fetch}")
    print(f"  Failed parse:                   {stats.agencies_failed_parse}")
    if stats.limit_reached:
        print(f"  Limit reached — stopped early.")


# --- Stage 2 helpers -------------------------------------------------------

def _lead_from_directory_email(agency: AgencyRecord) -> EnrichedLead:
    """Build an EnrichedLead from an agency's Stage 1 email (no website scrape).

    Used when Stage 1 already gave us an email (Yext VC / JSON-LD path).
    Name is best-effort from the email local-part since we never visited
    the agency's site to find a real name.
    """
    first, last = extract_name_from_email_local_part(agency.email)
    has_name = bool(first or last)
    return EnrichedLead(
        source_url=agency.source_url,
        company_name=agency.agency_name,
        phone=agency.phone,
        address=agency.address_line,
        city=agency.city,
        state=agency.state,
        zip=agency.zip,
        website=agency.website_url,
        email=agency.email,
        first_name=first,
        last_name=last,
        name_source="email_local_part" if has_name else "no_name_found",
        enrichment_status="found" if has_name else "no_name_found",
    )


def _no_data_placeholder(agency: AgencyRecord) -> EnrichedLead:
    """Placeholder lead for agencies with no Stage 1 email AND no website."""
    return EnrichedLead(
        source_url=agency.source_url,
        company_name=agency.agency_name,
        phone=agency.phone,
        address=agency.address_line,
        city=agency.city,
        state=agency.state,
        zip=agency.zip,
        website="",
        enrichment_status="no_email_found",
        name_source="no_name_found",
    )


def _merge_and_dedupe_leads(
    directory_lead: EnrichedLead | None,
    website_leads: list[EnrichedLead],
) -> list[EnrichedLead]:
    """Combine the Stage 1 email lead with website-scraped leads.

    Deduplicates by email. When the same email appears in both, the lead
    with a more complete name wins (full first+last > one name > no name).
    Returns sorted by email for determinism. Placeholder leads (no email)
    are kept only if there are zero real-email leads.
    """
    all_leads = ([directory_lead] if directory_lead else []) + list(website_leads)
    real = [L for L in all_leads if L.email]

    if real:
        by_email: dict[str, EnrichedLead] = {}
        for lead in real:
            existing = by_email.get(lead.email)
            if existing is None or _enriched_priority(lead) > _enriched_priority(existing):
                by_email[lead.email] = lead
        return sorted(by_email.values(), key=lambda L: L.email)

    # No real emails — keep the most informative placeholder we got
    return all_leads[:1] if all_leads else []


def _enriched_priority(lead: EnrichedLead) -> tuple[int, int, int]:
    has_full_name = 1 if (lead.first_name and lead.last_name) else 0
    has_any_name = 1 if (lead.first_name or lead.last_name) else 0
    name_chars = len(lead.first_name) + len(lead.last_name)
    return (has_full_name, has_any_name, name_chars)


# --- Stage 2 ---------------------------------------------------------------

def run_stage2(site_name: str, limit: int | None) -> None:
    stage1_path = _stage1_csv_path(site_name)
    stage2_path = _stage2_csv_path(site_name)

    if not os.path.exists(stage1_path):
        print(f"FATAL: {stage1_path} not found. Run --stage 1 first.")
        sys.exit(1)

    seen_source_urls = load_seen_keys(stage2_path, ("source_url",))

    print(f"Stage 2: site={site_name}")
    print(f"  Input:  {stage1_path}")
    print(f"  Output: {stage2_path}")
    print(f"  Already enriched: {len(seen_source_urls)} agencies (will be skipped)")
    if limit:
        print(f"  Limit: stop after {limit} new agencies")
    print()

    # Only known AgencyRecord fields are passed to the constructor; CSV may
    # have stray columns from a prior schema and we don't want to crash on those.
    known_fields = {f.name for f in fields(AgencyRecord)}

    limiter = PerDomainLimiter()
    enriched_count = 0
    leads_emitted = 0
    by_status: dict[str, int] = {
        "found": 0, "no_name_found": 0, "no_email_found": 0, "fetch_failed": 0,
    }

    with open(stage1_path, "r", newline="", encoding="utf-8") as fin, \
         requests.Session() as session:
        reader = csv.DictReader(fin)
        for row in reader:
            source_url = row.get("source_url", "")
            if not source_url:
                continue
            if (source_url,) in seen_source_urls:
                continue

            agency = AgencyRecord(**{k: v for k, v in row.items() if k in known_fields})
            print(f"[{enriched_count + 1}] {agency.agency_name}  ({agency.website_url or 'no website'})")

            website_leads = enrich_agency(agency, session, limiter, on_event=_print_event)
            directory_lead = _lead_from_directory_email(agency) if agency.email else None
            leads = _merge_and_dedupe_leads(directory_lead, website_leads)

            if not leads:
                leads = [_no_data_placeholder(agency)]

            for lead in leads:
                append_row(stage2_path, asdict(lead), STAGE2_COLUMNS)
                leads_emitted += 1
                by_status[lead.enrichment_status] = by_status.get(lead.enrichment_status, 0) + 1
                tag = f"{lead.first_name} {lead.last_name}".strip() or "(no name)"
                print(f"    -> {lead.email or '(no email)'} | {tag} | {lead.enrichment_status}")

            seen_source_urls.add((source_url,))
            enriched_count += 1

            if limit and enriched_count >= limit:
                print(f"\n  Limit reached — stopped early.")
                break

    print()
    print(f"Stage 2 done.")
    print(f"  Agencies enriched (this run):   {enriched_count}")
    print(f"  Leads emitted:                  {leads_emitted}")
    if leads_emitted:
        print(f"  By enrichment_status:")
        for status in ("found", "no_name_found", "no_email_found", "fetch_failed"):
            count = by_status.get(status, 0)
            pct = 100 * count / leads_emitted
            print(f"    {status:18}{count:6d}  ({pct:.1f}%)")


# --- CLI -------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape insurance agent directories into Instantly-ready CSVs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--site", required=True, choices=sorted(SITES.keys()),
                        help="Which site adapter to use")
    parser.add_argument("--stage", type=int, required=True, choices=[1, 2],
                        help="1 = crawl directory, 2 = enrich emails from agency websites")
    parser.add_argument("--state", default=None,
                        help="State slug (Stage 1 only). For Travelers, use 2-letter codes like 'ga'.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Stop after N new agencies (useful for testing)")
    parser.add_argument("--start-from", default=None,
                        help="Skip agency URLs alphabetically before this (Stage 1 only)")

    args = parser.parse_args()

    if args.stage == 1:
        if not args.state:
            parser.error("--state is required for --stage 1")
        run_stage1(args.site, args.state, args.limit, args.start_from)
    else:
        run_stage2(args.site, args.limit)


if __name__ == "__main__":
    main()
