"""Fill empty Carrier and State/Region columns on a HubSpot contacts export.

Usage (PowerShell):
  python backfill_crm.py contacts.csv --no-search      # free signals only
  python backfill_crm.py contacts.csv --dry-run        # list the Google queries, spend nothing
  python backfill_crm.py contacts.csv                  # full run (APIFY_TOKEN in .env)
  python backfill_crm.py contacts.csv --from-run <id> [<id> ...]  # reuse finished Apify runs

Writes <input>_backfilled.csv next to the input: same columns, empty
Carrier/State cells filled where we found something, plus "State Source" and
"Carrier Source" columns saying where each value came from. Existing values
are never overwritten. Leave the source columns unmapped when importing back
into HubSpot.

See scraper/crm_backfill.py for the lookup order and why each step exists.
"""

import argparse
import csv
import os
import sys
from collections import Counter
from pathlib import Path

import requests
from dotenv import load_dotenv

from scraper import crm_backfill as cb
from scraper.core.http import PerDomainLimiter, fetch_url


def _read_rows(path: Path) -> tuple[list[dict], list[str]]:
    # utf-8-sig: HubSpot and Excel both like to prepend a BOM.
    with open(path, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return list(reader), list(reader.fieldnames or [])


def _write_rows(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    columns = fieldnames + [
        c for c in (cb.STATE_SOURCE_COL, cb.CARRIER_SOURCE_COL) if c not in fieldnames
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _fill_from_websites(rows: list[dict], fills: list[cb.Fill]) -> None:
    targets = [
        (row, fill, cb.agency_website(row.get(cb.EMAIL_COL, "")))
        for row, fill in zip(rows, fills)
        if not fill.skipped and fill.needs_state
    ]
    targets = [t for t in targets if t[2]]
    if not targets:
        return
    print(f"Checking {len(targets)} agency websites for an address...")
    limiter = PerDomainLimiter()
    with requests.Session() as session:
        for row, fill, url in targets:
            html = fetch_url(url, session, limiter)
            phone_state = cb.state_from_phone(row.get(cb.PHONE_COL, ""))
            cb.apply_website(fill, html or "", url, phone_state)
            found = cb.state_name(fill.state) if fill.state else "-"
            print(f"  {url:<45} {found}")


def _search_items(queries: list[str], from_runs: list[str] | None) -> list[dict]:
    # Imported here so --no-search / --dry-run work without the Apify plumbing.
    from scrape import _resume_apify_run, _run_apify_actor

    token = os.environ.get("APIFY_TOKEN")
    if not token:
        print("FATAL: APIFY_TOKEN is not set.")
        print("  Put it in .env next to backfill_crm.py (copy .env.example).")
        print("  Or rerun with --no-search to use only the free signals.")
        sys.exit(1)
    if from_runs:
        # Google results vary run to run, so pooling several paid runs finds
        # more than any one of them. Queries are matched by exact text, so
        # runs made with older query formats just contribute nothing.
        items = []
        for run_id in from_runs:
            items.extend(_resume_apify_run(run_id, token))
        return items
    print(f"  Apify actor: {cb.APIFY_GOOGLE_ACTOR_ID}")
    return _run_apify_actor(
        cb.APIFY_GOOGLE_ACTOR_ID, cb.google_search_payload(queries), token
    )


def _fill_from_search(
    rows: list[dict], fills: list[cb.Fill], args: argparse.Namespace
) -> None:
    pending = []
    for row, fill in zip(rows, fills):
        if fill.skipped or fill.complete:
            continue
        query = cb.search_query(
            row.get(cb.FIRST_NAME_COL, ""), row.get(cb.LAST_NAME_COL, ""), fill.carrier
        )
        if query:
            pending.append((row, fill, query))
    if args.limit is not None:
        pending = pending[: args.limit]

    queries = sorted({q for _, _, q in pending})
    if not queries:
        return
    print(f"{len(pending)} contacts still incomplete -> {len(queries)} Google queries.")
    if args.dry_run:
        for q in queries:
            print(f"  {q}")
        return

    items = _search_items(queries, args.from_run)
    urls = cb.urls_by_query(items)
    print(f"  Got results for {len(urls)} of {len(queries)} queries.")
    for row, fill, query in pending:
        cb.apply_search_results(fill, row, urls.get(query, []))


def _print_summary(fills: list[cb.Fill]) -> None:
    active = [f for f in fills if not f.skipped]

    def bucket(source: str) -> str:
        if not source:
            return "not found"
        return source.split(":")[0]

    print()
    print(f"Contacts: {len(fills)} ({len(fills) - len(active)} internal/test skipped)")
    for label, attr in (("Carrier", "carrier_source"), ("State", "state_source")):
        counts = Counter(bucket(getattr(f, attr)) for f in active)
        parts = ", ".join(f"{k}: {v}" for k, v in counts.most_common())
        print(f"  {label:<8} {parts}")
    print(f"  Complete (carrier + state): {sum(f.complete for f in active)}/{len(active)}")


def main() -> None:
    load_dotenv(Path(__file__).with_name(".env"))

    parser = argparse.ArgumentParser(
        description="Fill empty Carrier / State columns on a HubSpot contacts export.",
        epilog=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input", type=Path, help="HubSpot contacts export (CSV)")
    parser.add_argument("--output", type=Path, default=None,
                        help="Output CSV (default: <input>_backfilled.csv)")
    parser.add_argument("--no-search", action="store_true",
                        help="Skip the Apify Google search step (free run)")
    parser.add_argument("--no-websites", action="store_true",
                        help="Skip fetching agency homepages")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the Google queries instead of running them")
    parser.add_argument("--limit", type=int, default=None,
                        help="Search at most N contacts (for a cheap test run)")
    parser.add_argument("--from-run", nargs="+", default=None, metavar="RUN_ID",
                        help="Reuse results from one or more previous Apify runs "
                             "instead of searching again (free)")
    args = parser.parse_args()

    rows, fieldnames = _read_rows(args.input)
    missing = [c for c in (cb.STATE_COL, cb.CARRIER_COL, cb.EMAIL_COL) if c not in fieldnames]
    if missing:
        print(f"FATAL: input is missing columns: {', '.join(missing)}")
        sys.exit(1)

    fills = [cb.initial_fill(row) for row in rows]
    if not args.no_websites:
        _fill_from_websites(rows, fills)
    if not args.no_search:
        _fill_from_search(rows, fills, args)
    for row, fill in zip(rows, fills):
        if not fill.skipped:
            cb.apply_phone(fill, row)

    output = args.output or args.input.with_name(f"{args.input.stem}_backfilled.csv")
    _write_rows(output, [cb.output_row(r, f) for r, f in zip(rows, fills)], fieldnames)
    _print_summary(fills)
    print(f"\nWrote {output}")


if __name__ == "__main__":
    main()
