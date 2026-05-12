"""
Progressive insurance agent directory scraper.

Stage 1: Walk progressiveagent.com/local-agent/ to collect every agency's
         name, address, phone, website URL, and email (read from each
         page's schema.org JSON-LD block).
Stage 2: Read the Stage 1 CSV and write it in Instantly's required column
         format. Drops rows that have no email. Website-scraping email
         enrichment is NOT performed by default — Progressive's JSON-LD
         provided an email for 100% of Georgia agencies, so enrichment
         was unnecessary. If you port this to a carrier where coverage
         is lower, add the enrichment step to run_stage2().

Final output is an Instantly-ready CSV with columns:
  email, first_name, last_name, company_name, phone,
  address, city, state, zip, website, source_url

Run:
  python progressive_scraper.py --stage 1 --state georgia
  python progressive_scraper.py --stage 1 --state georgia --limit 20
  python progressive_scraper.py --stage 1 --state georgia --start-from <url>
  python progressive_scraper.py --stage 2
"""

import argparse
import csv
import json
import os
import random
import re
import sys
import time
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

# --- constants --------------------------------------------------------------

HUB_URL = "https://www.progressiveagent.com/local-agent/"
USER_AGENT = "NextCallClub-AgentScraper/1.0 (contact: victorsalazar@nextcallclub.com)"
RATE_LIMIT_RANGE = (1.0, 2.0)   # seconds, jittered, applied before each request
MAX_RETRIES = 3
REQUEST_TIMEOUT = 20            # seconds

STAGE1_CSV = "stage1_progressive_agents.csv"
STAGE1_COLUMNS = [
    "agency_name", "address_line", "city", "state", "zip",
    "phone", "website_url", "email", "source_url",
]

STAGE2_CSV = "stage2_progressive_agents_enriched.csv"
STAGE2_COLUMNS = [
    "email", "first_name", "last_name", "company_name", "phone",
    "address", "city", "state", "zip", "website", "source_url",
]


# --- HTTP -------------------------------------------------------------------

def fetch_url(url, session):
    """GET a URL politely. Returns response text on success, or None on failure.

    - Sleeps a random 1-2s before every request (including retries) so we
      never hammer the server.
    - Retries up to MAX_RETRIES on 429, 5xx, or connection errors with
      exponential backoff.
    - Gives up (returns None) on other 4xx like 404 — those won't get better
      with retries.
    """
    headers = {"User-Agent": USER_AGENT}
    backoff = 2.0
    for attempt in range(1, MAX_RETRIES + 1):
        time.sleep(random.uniform(*RATE_LIMIT_RANGE))
        try:
            resp = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as e:
            print(f"  [retry {attempt}/{MAX_RETRIES}] {type(e).__name__}: {e}")
            time.sleep(backoff)
            backoff *= 2
            continue

        if resp.status_code == 200:
            return resp.text
        if resp.status_code == 429 or resp.status_code >= 500:
            print(f"  [retry {attempt}/{MAX_RETRIES}] HTTP {resp.status_code} on {url}")
            time.sleep(backoff)
            backoff *= 2
            continue
        print(f"  [fail] HTTP {resp.status_code} on {url}")
        return None

    print(f"  [fail] gave up after {MAX_RETRIES} retries on {url}")
    return None


# --- parsing (Stage 1) ------------------------------------------------------

def parse_state_page(html, state_slug):
    """Return a sorted, deduplicated list of city-page URLs from a state page.

    A city page URL looks like:
        https://www.progressiveagent.com/local-agent/<state>/<city-slug>/

    We accept only links whose path is exactly that shape — no deeper (which
    would be an agency detail page) and no shallower (which would be the state
    page itself or the hub).
    """
    soup = BeautifulSoup(html, "html.parser")
    expected_prefix = f"/local-agent/{state_slug}/"
    city_urls = set()

    for anchor in soup.find_all("a", href=True):
        parsed = urlparse(anchor["href"])
        if parsed.netloc and parsed.netloc != "www.progressiveagent.com":
            continue
        path = parsed.path
        if not path.startswith(expected_prefix):
            continue
        remainder = path[len(expected_prefix):]
        if not remainder or not remainder.endswith("/") or remainder.count("/") != 1:
            continue
        city_url = f"https://www.progressiveagent.com{path}"
        city_urls.add(city_url)

    return sorted(city_urls)


def parse_city_page(html, city_url):
    """Return a sorted, deduplicated list of agency detail page URLs from a city page.

    Agency detail URLs look like:
        https://www.progressiveagent.com/local-agent/<state>/<city>/<agency-slug>/

    i.e. exactly one path segment deeper than the city page URL.
    """
    soup = BeautifulSoup(html, "html.parser")
    city_path = urlparse(city_url).path
    if not city_path.endswith("/"):
        city_path += "/"
    agency_urls = set()

    for anchor in soup.find_all("a", href=True):
        parsed = urlparse(anchor["href"])
        if parsed.netloc and parsed.netloc != "www.progressiveagent.com":
            continue
        path = parsed.path
        if not path.startswith(city_path):
            continue
        remainder = path[len(city_path):]
        if not remainder or not remainder.endswith("/") or remainder.count("/") != 1:
            continue
        agency_url = f"https://www.progressiveagent.com{path}"
        agency_urls.add(agency_url)

    return sorted(agency_urls)


def _clean(value):
    """Collapse internal whitespace and strip ends. Returns '' for None / non-strings."""
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


def _format_phone(raw):
    """Normalize a phone number to '(NXX) NXX-XXXX'.

    Handles inputs like '+14046336332', '1-404-633-6332', or '(404) 633-6332'.
    Returns the cleaned string, or the original input if it has an unexpected shape.
    """
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"({digits[0:3]}) {digits[3:6]}-{digits[6:10]}"
    return raw.strip()


def _extract_agency_jsonld(soup):
    """Return the first JSON-LD block on the page that looks like an agency record.

    Progressive embeds a schema.org structured-data block on every agency page
    (inside <script type="application/ld+json">) with clean, parsed fields for
    name, address, telephone, url, and often email. Much more reliable than
    scraping the rendered HTML.

    Returns the dict, or {} if none found.
    """
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if (
                isinstance(item, dict)
                and item.get("name")
                and isinstance(item.get("address"), dict)
            ):
                return item
    return {}


def parse_agency_page(html, source_url):
    """Extract agency fields from one agency detail page.

    Uses the page's JSON-LD structured-data block as the source of truth.

    Returns a dict with these keys (empty string if not found):
      agency_name, address_line, city, state, zip,
      phone, website_url, email, source_url
    """
    soup = BeautifulSoup(html, "html.parser")
    data = _extract_agency_jsonld(soup)
    address = data.get("address") or {}

    return {
        "agency_name": _clean(data.get("name")),
        "address_line": _clean(address.get("streetAddress")),
        "city": _clean(address.get("addressLocality")),
        "state": _clean(address.get("addressRegion")),
        "zip": _clean(address.get("postalCode")),
        "phone": _format_phone(data.get("telephone") or ""),
        "website_url": _clean(data.get("url")),
        "email": _clean(data.get("email")).lower(),
        "source_url": source_url,
    }


# --- CSV I/O (resumability) -------------------------------------------------

def load_seen_urls(csv_path):
    """Return the set of source_urls already saved in csv_path. Empty if no file."""
    seen = set()
    if not os.path.exists(csv_path):
        return seen
    with open(csv_path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            url = row.get("source_url")
            if url:
                seen.add(url)
    return seen


def append_row(csv_path, row, columns):
    """Append one row. Writes the header first if the file doesn't exist yet.

    Opens, writes, closes per call — slower than keeping the file open, but
    guarantees every row is flushed to disk before the next request starts,
    so Ctrl+C can't lose work.
    """
    new_file = not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        if new_file:
            writer.writeheader()
        writer.writerow(row)


# --- Stage 1 orchestrator ---------------------------------------------------

def run_stage1(state_slug, limit=None, start_from=None):
    """Walk Progressive's directory for one state and write rows to STAGE1_CSV.

    Resumable: any source_url already in the output CSV is skipped.
    """
    state_url = f"https://www.progressiveagent.com/local-agent/{state_slug}/"
    seen = load_seen_urls(STAGE1_CSV)

    print(f"Stage 1: scraping state '{state_slug}'")
    print(f"  Output: {STAGE1_CSV}")
    print(f"  Already saved: {len(seen)} rows (will be skipped)")
    if limit:
        print(f"  Limit: stop after {limit} new agencies")
    if start_from:
        print(f"  Start-from: skipping URLs alphabetically before {start_from}")
    print()

    with requests.Session() as session:
        state_html = fetch_url(state_url, session)
        if state_html is None:
            print(f"FATAL: couldn't fetch state page {state_url}")
            return

        city_urls = parse_state_page(state_html, state_slug)
        print(f"  Found {len(city_urls)} cities in {state_slug}\n")

        new_count = 0
        with_email = 0
        without_email = 0

        for city_idx, city_url in enumerate(city_urls, 1):
            print(f"[city {city_idx}/{len(city_urls)}] {city_url}")
            city_html = fetch_url(city_url, session)
            if city_html is None:
                print("  ... fetch failed, skipping this city")
                continue
            agency_urls = parse_city_page(city_html, city_url)
            print(f"  {len(agency_urls)} agencies in this city")

            for agency_url in agency_urls:
                if start_from and agency_url < start_from:
                    continue
                if agency_url in seen:
                    continue

                agency_html = fetch_url(agency_url, session)
                if agency_html is None:
                    print(f"    [skip] fetch failed: {agency_url}")
                    continue

                row = parse_agency_page(agency_html, agency_url)
                if not row.get("agency_name"):
                    print(f"    [skip] no JSON-LD found: {agency_url}")
                    continue

                append_row(STAGE1_CSV, row, STAGE1_COLUMNS)
                seen.add(agency_url)
                new_count += 1
                if row["email"]:
                    with_email += 1
                else:
                    without_email += 1

                tag = row["email"] or "(no email)"
                print(f"    [{new_count}] {row['agency_name']} | {row['phone']} | {tag}")

                if limit and new_count >= limit:
                    print()
                    _print_stage1_summary(new_count, with_email, without_email)
                    return

        print()
        _print_stage1_summary(new_count, with_email, without_email)


def _print_stage1_summary(total, with_email, without_email):
    print(f"Stage 1 done. Wrote {total} new rows to {STAGE1_CSV}.")
    if total:
        pct = 100 * with_email / total
        print(f"  With email from directory:    {with_email}  ({pct:.1f}%)")
        print(f"  Need website enrichment:      {without_email}")


# --- Stage 2 (column remap to Instantly format) -----------------------------

def _looks_like_email(value):
    """Cheap sanity check: a non-empty string with @ and a dot in the domain part."""
    if not value or "@" not in value:
        return False
    local, _, domain = value.partition("@")
    return bool(local) and "." in domain


def _stage1_to_instantly(row):
    """Map a Stage 1 row to an Instantly-format row."""
    return {
        "email":        row.get("email", ""),
        "first_name":   "",
        "last_name":    "",
        "company_name": row.get("agency_name", ""),
        "phone":        row.get("phone", ""),
        "address":      row.get("address_line", ""),
        "city":         row.get("city", ""),
        "state":        row.get("state", ""),
        "zip":          row.get("zip", ""),
        "website":      row.get("website_url", ""),
        "source_url":   row.get("source_url", ""),
    }


def run_stage2():
    """Convert Stage 1 CSV to Instantly-ready Stage 2 CSV. Drops rows with no email.

    Always rebuilds Stage 2 from scratch — it's a pure transform of the
    Stage 1 CSV and runs in well under a second, so resumability isn't worth
    the complexity.
    """
    if not os.path.exists(STAGE1_CSV):
        print(f"FATAL: {STAGE1_CSV} not found. Run --stage 1 first.")
        sys.exit(1)

    total = 0
    kept = 0
    dropped_no_email = 0

    with open(STAGE1_CSV, "r", newline="", encoding="utf-8") as fin, \
         open(STAGE2_CSV, "w", newline="", encoding="utf-8") as fout:
        reader = csv.DictReader(fin)
        writer = csv.DictWriter(fout, fieldnames=STAGE2_COLUMNS, extrasaction="ignore")
        writer.writeheader()

        for row in reader:
            total += 1
            if not _looks_like_email(row.get("email", "")):
                dropped_no_email += 1
                continue
            writer.writerow(_stage1_to_instantly(row))
            kept += 1

    print(f"Stage 2 done. Read {total} rows from {STAGE1_CSV}.")
    print(f"  Kept (valid email):           {kept}")
    print(f"  Dropped (no/invalid email):   {dropped_no_email}")
    print(f"  Output: {STAGE2_CSV}")


# --- CLI --------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Scrape Progressive's agent directory.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--stage", type=int, required=True, choices=[1, 2],
                        help="1 = crawl directory, 2 = enrich emails (not yet implemented)")
    parser.add_argument("--state", default=None,
                        help="State slug for Stage 1, e.g. 'georgia'")
    parser.add_argument("--limit", type=int, default=None,
                        help="Stop after N new agencies (useful for testing)")
    parser.add_argument("--start-from", default=None,
                        help="Skip agency URLs alphabetically before this one")

    args = parser.parse_args()

    if args.stage == 1:
        if not args.state:
            parser.error("--state is required for --stage 1")
        run_stage1(args.state, limit=args.limit, start_from=args.start_from)
    else:
        run_stage2()


if __name__ == "__main__":
    main()
