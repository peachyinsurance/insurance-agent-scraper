"""CLI dispatcher for the per-site scraping pipeline.

Usage:
  Stage 1 — crawl directory:
    python scrape.py --site travelers --stage 1 --state ga
    python scrape.py --site travelers --stage 1 --state ga --limit 50

  Stage 2 — enrich each agency's website for (name, email):
    python scrape.py --site travelers --stage 2
    python scrape.py --site travelers --stage 2 --limit 50

  State Farm (sitemap-driven, two-pass):
    python scrape.py --site state_farm --stage 1 --state ga       # ~5 min, lite CSV
    python scrape.py --site state_farm --stage 1                  # all 50 states (~25K rows)
    python scrape.py --site state_farm --stage 2                  # ~8 hrs national; per-state much shorter

Output files (per site):
  stage1_<site>_agents.csv      — raw rows from the directory (or sitemap)
  stage2_<site>_enriched.csv    — Instantly-ready rows

Both stages are resumable: rerunning with the same args skips rows that are
already in the output CSV. Ctrl+C is safe — rows flush to disk before each
next HTTP request.
"""

import argparse
import csv
import os
import sys
import time
from dataclasses import asdict, fields

import requests

from scraper.core.crawl import run_crawl
from scraper.core.csv_io import append_row, load_seen_keys
from scraper.core.http import PerDomainLimiter, WideBlockError, fetch_url
from scraper.enrichment import enrich_agency, extract_name_from_email_local_part
from scraper.records import AgencyRecord, EnrichedLead
from scraper.sites import state_farm, travelers


SITES = {
    "travelers": travelers,
    "state_farm": state_farm,
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

# State Farm Stage 1 is sitemap-only — no agent-page fetches, no email/phone/
# address yet. Just the URL + slug-mined preview fields per row.
STAGE1_STATE_FARM_COLUMNS = [
    "source_url", "state", "city",
    "first_name_guess", "last_name_guess", "agent_id",
]

# State Farm Stage 2 produces the same flat shape as Progressive (no diagnostic
# columns — every row goes through the same JSON-LD path; emptiness in the
# website/phone fields is the only signal needed downstream).
STAGE2_STATE_FARM_COLUMNS = [
    "email", "first_name", "last_name", "company_name",
    "phone", "address", "city", "state", "zip", "website", "source_url",
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


# --- State Farm Stage 1 (sitemap parse → lite CSV) -------------------------

def run_state_farm_stage1(state: str | None, limit: int | None) -> None:
    """Fast sitemap walk: write one row per agent URL, no agent-page fetches.

    Doesn't use run_crawl — the sitemap IS the directory, no state→city→agency
    walk is needed. Slug-mines each URL into preview columns (state/city/name/id)
    that Andrew can review before committing to the long Stage 2 enrichment run.

    Resumable on source_url. If --state is omitted, enumerates all 50 states
    (~25K rows, still fast — single sitemap fetch + CSV writes).
    """
    csv_path = _stage1_csv_path("state_farm")
    seen_keys = load_seen_keys(csv_path, ("source_url",))

    print(f"Stage 1: site=state_farm  state={state or 'ALL STATES'}")
    print(f"  Output: {csv_path}")
    print(f"  Already saved: {len(seen_keys)} rows (will be skipped)")
    if limit:
        print(f"  Limit: stop after {limit} new agents")
    if state is None:
        print(f"  WARNING: no --state filter — enumerating all ~25K US agents.")
        print(f"  Press Ctrl+C in 5 seconds to abort.")
        time.sleep(5)
    print()

    limiter = PerDomainLimiter()
    written = 0
    skipped = 0

    with requests.Session() as session:
        for url in state_farm.iter_agency_urls(session, limiter, state_slug=state):
            if (url,) in seen_keys:
                skipped += 1
                continue

            slug = state_farm.parse_url_slug(url)
            row = {
                "source_url": url,
                "state": slug["state"],
                "city": slug["city"],
                "first_name_guess": slug["first_name_guess"],
                "last_name_guess": slug["last_name_guess"],
                "agent_id": slug["agent_id"],
            }
            append_row(csv_path, row, STAGE1_STATE_FARM_COLUMNS)
            seen_keys.add((url,))
            written += 1

            if written % 200 == 0:
                print(f"  [{written}] {row['state']}/{row['city']} — "
                      f"{row['first_name_guess']} {row['last_name_guess']}")

            if limit and written >= limit:
                print(f"\n  Limit reached.")
                break

    print()
    print(f"Stage 1 done.")
    print(f"  New rows written:           {written}")
    print(f"  Skipped (already in CSV):   {skipped}")


# --- State Farm Stage 2 (lite CSV → enriched CSV) --------------------------

def run_state_farm_stage2(limit: int | None) -> None:
    """Per-row HTTP fetch + JSON-LD parse + write to enriched CSV.

    Reads each source_url from the Stage 1 CSV, fetches the agent's State Farm
    detail page, parses the InsuranceAgency JSON-LD, splits the founder.name
    into first/last, and writes a Clay-ready row. Email column always blank —
    Clay handles email enrichment downstream.

    Failed fetches write a placeholder row (slug-mined names + empty contact
    fields) so the URL doesn't get re-fetched on resume. To retry a failed URL,
    delete its row from the Stage 2 CSV and re-run.
    """
    stage1_path = _stage1_csv_path("state_farm")
    stage2_path = _stage2_csv_path("state_farm")

    if not os.path.exists(stage1_path):
        print(f"FATAL: {stage1_path} not found. Run --stage 1 first.")
        sys.exit(1)

    seen_source_urls = load_seen_keys(stage2_path, ("source_url",))

    print(f"Stage 2: site=state_farm")
    print(f"  Input:  {stage1_path}")
    print(f"  Output: {stage2_path}")
    print(f"  Already enriched: {len(seen_source_urls)} agents (will be skipped)")
    if limit:
        print(f"  Limit: stop after {limit} new agents")
    print()

    limiter = PerDomainLimiter()
    enriched_count = 0
    by_outcome = {"with_website": 0, "no_website": 0, "parse_empty": 0, "fetch_failed": 0}

    with open(stage1_path, "r", newline="", encoding="utf-8") as fin, \
         requests.Session() as session:
        reader = csv.DictReader(fin)
        for row in reader:
            source_url = row.get("source_url", "")
            if not source_url:
                continue
            if (source_url,) in seen_source_urls:
                continue

            html = fetch_url(source_url, session, limiter, on_event=_print_event)
            if html is None:
                placeholder = _state_farm_placeholder_row(row)
                append_row(stage2_path, placeholder, STAGE2_STATE_FARM_COLUMNS)
                seen_source_urls.add((source_url,))
                by_outcome["fetch_failed"] += 1
                enriched_count += 1
                print(f"[{enriched_count}] (fetch_failed) {source_url}")
                if limit and enriched_count >= limit:
                    print(f"\n  Limit reached.")
                    break
                continue

            agency = state_farm.parse_agency_page(html, source_url)
            output_row = _state_farm_enriched_row(agency, row)
            append_row(stage2_path, output_row, STAGE2_STATE_FARM_COLUMNS)
            seen_source_urls.add((source_url,))
            enriched_count += 1

            if not agency.agency_name:
                by_outcome["parse_empty"] += 1
            elif agency.website_url:
                by_outcome["with_website"] += 1
            else:
                by_outcome["no_website"] += 1

            tag = agency.website_url or "(no website)"
            print(f"[{enriched_count}] {output_row['first_name']} {output_row['last_name']} | "
                  f"{agency.phone} | {tag}")

            if limit and enriched_count >= limit:
                print(f"\n  Limit reached.")
                break

    print()
    print(f"Stage 2 done.")
    print(f"  Agents processed (this run): {enriched_count}")
    if enriched_count:
        print(f"  Outcomes:")
        for label in ("with_website", "no_website", "parse_empty", "fetch_failed"):
            count = by_outcome.get(label, 0)
            pct = 100 * count / enriched_count
            print(f"    {label:18}{count:6d}  ({pct:.1f}%)")


def _state_farm_enriched_row(agency: AgencyRecord, stage1_row: dict) -> dict:
    """Map an AgencyRecord to one Stage 2 CSV row.

    Splits the agent name (founder.name from JSON-LD, e.g. "Harold Mitchell Jr")
    on the first space. If parse came back empty, falls back to the slug-mined
    names from the Stage 1 row so we still emit a usable placeholder.
    """
    full_name = agency.agency_name
    if full_name:
        tokens = full_name.split(maxsplit=1)
        first_name = tokens[0]
        last_name = tokens[1] if len(tokens) > 1 else ""
    else:
        first_name = stage1_row.get("first_name_guess", "")
        last_name = stage1_row.get("last_name_guess", "")

    return {
        "email": "",  # Clay handles
        "first_name": first_name,
        "last_name": last_name,
        "company_name": agency.agency_name,
        "phone": agency.phone,
        "address": agency.address_line,
        "city": agency.city or stage1_row.get("city", ""),
        "state": agency.state or stage1_row.get("state", ""),
        "zip": agency.zip,
        "website": agency.website_url,
        "source_url": agency.source_url,
    }


def _state_farm_placeholder_row(stage1_row: dict) -> dict:
    """Row written when a Stage 2 fetch fails — slug-mined preview, empty contact.

    Marks the URL as processed (so resume skips it) without losing the
    Stage 1 preview data. To retry, delete the row from the Stage 2 CSV.
    """
    return {
        "email": "",
        "first_name": stage1_row.get("first_name_guess", ""),
        "last_name": stage1_row.get("last_name_guess", ""),
        "company_name": "",
        "phone": "",
        "address": "",
        "city": stage1_row.get("city", ""),
        "state": stage1_row.get("state", ""),
        "zip": "",
        "website": "",
        "source_url": stage1_row.get("source_url", ""),
    }


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
                        help="State slug (Stage 1 only). 2-letter code for Travelers and "
                             "State Farm (e.g. 'ga'). Optional for state_farm — omit to "
                             "enumerate all states.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Stop after N new agencies (useful for testing)")
    parser.add_argument("--start-from", default=None,
                        help="Skip agency URLs alphabetically before this (Travelers Stage 1 only)")

    args = parser.parse_args()

    try:
        if args.site == "state_farm":
            if args.stage == 1:
                run_state_farm_stage1(args.state, args.limit)
            else:
                run_state_farm_stage2(args.limit)
        else:
            if args.stage == 1:
                if not args.state:
                    parser.error("--state is required for --stage 1")
                run_stage1(args.site, args.state, args.limit, args.start_from)
            else:
                run_stage2(args.site, args.limit)
    except WideBlockError as exc:
        # Bot manager (Akamai/Cloudflare) wide-blocked us. Output CSV is
        # intact — the per-row append flushes before each fetch. Wait,
        # change IP/UA, and re-run; resume picks up where this stopped.
        print(f"\nFATAL: {exc}")
        print(f"  Output CSV is intact — re-run after waiting or changing IP.")
        sys.exit(2)


if __name__ == "__main__":
    main()
