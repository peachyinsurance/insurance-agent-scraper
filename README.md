# Insurance Agent Scraper

Pulls insurance agencies from carrier directories (Progressive, Travelers — more
to come) into a CSV ready to upload to Instantly for cold-email outreach.

Built for Next Call Club. Currently runs against:
- **Progressive** — via the standalone `progressive_scraper.py` script
- **Travelers** — via the newer `scrape.py --site travelers` CLI

Both produce the same Instantly-ready output shape, but they use different
implementations (history: Progressive shipped first as a single file; Travelers
prompted a refactor into a pluggable `scraper/` package that future carriers
plug into).

---

## What you get

After a full run, the file you upload to Instantly is:

| Carrier | Final CSV |
|---|---|
| Progressive | `stage2_progressive_agents_enriched.csv` |
| Travelers | `stage2_travelers_enriched.csv` |

**Progressive's Stage 2 columns** (Instantly's required order):

```
email,first_name,last_name,company_name,phone,address,city,state,zip,website,source_url
```

**Travelers' Stage 2 columns** (adds two diagnostic fields):

```
email,first_name,last_name,name_source,company_name,phone,address,city,state,zip,website,source_url,enrichment_status
```

The two extra fields:
- `name_source` — where the name came from: `team_page` | `contact_page` | `email_local_part` | `no_name_found`
- `enrichment_status` — overall outcome for the row: `found` | `no_name_found` | `no_email_found` | `fetch_failed`

You can ignore `name_source` and `enrichment_status` on upload — Instantly will just import them as extra columns. They're useful for filtering rows by quality before sending.

---

## Prerequisites

- **Windows 10/11** with PowerShell (commands below assume PowerShell; Git Bash works too with minor path tweaks).
- **Python 3.11 or newer**. Verify with `python --version`. If missing, install from [python.org/downloads](https://www.python.org/downloads/) and tick "Add Python to PATH".

---

## First-time setup

Once per machine:

```powershell
cd C:\Users\victo\Documents\Scraper
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

> If `Activate.ps1` errors with "running scripts is disabled," run this once and answer `Y`:
> ```powershell
> Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
> ```

You'll know the venv is active when your prompt starts with `(venv)`.

Sanity check:

```powershell
python -c "import requests, bs4, tqdm; print('all good')"
```

For running tests (optional, dev-time only):

```powershell
pip install pytest responses
```

---

## Daily use

**Every new PowerShell session, activate the venv first:**

```powershell
cd C:\Users\victo\Documents\Scraper
.\venv\Scripts\Activate.ps1
```

### Quick reference

| Goal | Command |
|---|---|
| Scrape Progressive (Georgia) | `python progressive_scraper.py --stage 1 --state georgia` then `--stage 2` |
| Scrape Travelers (Georgia) | `python scrape.py --site travelers --stage 1 --state ga` then `--stage 2` |
| Test before committing to a long run | Add `--limit 20` to the Stage 1 command |
| Resume after Ctrl+C | Re-run the same command — it skips rows already in the CSV |

---

## Progressive workflow

Progressive embeds full agency data (name, address, phone, **email**) in
schema.org JSON-LD on every detail page. We saw 100% email coverage on Georgia,
so Stage 2 is just a column remap — no website scraping needed.

**Stage 1** — crawl the directory:

```powershell
python progressive_scraper.py --stage 1 --state georgia
```

Takes ~40–45 minutes for Georgia (~852 agencies, polite 1–2s delay per request).
Other states use the lowercase slug from Progressive's URL (`new-york`, `texas`,
`north-carolina`, etc.).

**Stage 2** — produce the Instantly file:

```powershell
python progressive_scraper.py --stage 2
```

Runs in under a second. Reads `stage1_progressive_agents.csv`, drops rows with
no email, reorders columns, writes `stage2_progressive_agents_enriched.csv`.

**Useful flags:**

- `--limit 20` — stop after 20 new agencies (testing)
- `--start-from <URL>` — skip URLs alphabetically before this one

---

## Travelers workflow

Travelers' directory gives us name/address/phone reliably (and email for ~40% of
agencies via a Yext verifiable-credential block). For the remaining ~60%, we
visit each agency's own website to find emails — that's what Stage 2 does here.

**Stage 1** — crawl the Travelers directory:

```powershell
python scrape.py --site travelers --stage 1 --state ga
```

Travelers uses **2-letter state codes** (`ga`, `tx`, `ca`), not the spelled-out
names Progressive uses. ~10–15 min for Georgia (~870 agencies).

**Stage 2** — scrape agency websites for (name, email) pairs:

```powershell
python scrape.py --site travelers --stage 2
```

This is the **slow one — plan for 3–5 hours on a full state.** Each agency
triggers up to 11 page fetches (homepage + `/contact`, `/team`, `/staff`, etc.).
SSL errors fail fast (no retries); other transient failures retry with backoff.
Safe to interrupt — fully resumable.

**Useful flags** (same shape as Progressive, plus `--site`):

- `--site travelers` — required, picks the adapter
- `--limit 50` — stop after N new agencies (testing)
- `--start-from <URL>` — skip URLs alphabetically before this one

**Realistic hit rates (50-agency Georgia sample):**

| Status | Rough share |
|---|---|
| `found` (email + name) | ~32% |
| `no_name_found` (email only) | ~38% |
| `no_email_found` | ~22% |
| `fetch_failed` (site blocked us) | ~5% |

So roughly **56% of agencies produce at least one usable email**. The
`no_name_found` rows are still leads — they have a real email, just no person
name. Instantly templates can use `{{first_name|"there"}}` fallback syntax.

---

## Resuming after a crash or Ctrl+C

Both scrapers are **fully resumable**:

- Every row writes to disk *before* the next HTTP request starts.
- On startup, the script reads the existing CSV and skips URLs already in it.
- Hit Ctrl+C any time. Re-run the same command. It picks up where it stopped.

No need to delete CSVs between runs unless you want a clean slate (e.g. to test
parser changes).

---

## Tuning the rate limit

Both scrapers send one HTTP request per host every 1–2 seconds (random
jittered). If you start seeing `[retry]` or HTTP 429 lines, the server is asking
us to slow down — open the relevant file and bump the constant:

- Progressive: `progressive_scraper.py` → `RATE_LIMIT_RANGE = (1.0, 2.0)` near the top
- Travelers: `scraper/core/http.py` → `DEFAULT_RATE_LIMIT = (1.0, 2.0)`

Change to `(3.0, 5.0)` if needed, save, re-run.

**Never set the User-Agent to a fake browser string.** Both scrapers ship an
honest identifier (`NextCallClub-AgentScraper/1.0 (contact: victorsalazar@nextcallclub.com)`).
That's the design — Travelers' robots.txt allows us as `User-agent: *`, and a
fake browser would violate their terms.

---

## Project structure

```
Scraper/
├── progressive_scraper.py          # Standalone Progressive script (kept as-is)
├── scrape.py                       # CLI for the pluggable scraper (Travelers + future)
├── scraper/                        # Pluggable scraper package
│   ├── records.py                  # AgencyRecord, EnrichedLead dataclasses
│   ├── enrichment.py               # Website scraping: emails, names, dedup
│   ├── core/
│   │   ├── http.py                 # Polite HTTP fetch + per-domain rate limiter
│   │   ├── csv_io.py               # Append-row + resumability lookup
│   │   └── crawl.py                # Generic state→city→agency loop
│   └── sites/
│       └── travelers.py            # Travelers adapter (state_url, parse_*)
├── tests/                          # 120 unit tests (pytest)
│   ├── test_records.py
│   ├── test_core_http.py
│   ├── test_core_csv_io.py
│   ├── test_core_crawl.py
│   ├── test_sites_travelers.py
│   └── test_enrichment.py
├── conftest.py                     # pytest config (adds project root to sys.path)
├── requirements.txt                # Locked runtime deps
├── stage1_*.csv                    # Per-carrier raw scrape output
├── stage2_*.csv                    # Per-carrier Instantly-ready output
├── venv/                           # Python sandbox (created by `python -m venv venv`)
└── README.md                       # This file
```

---

## Adding a new state

No code change — just a slug change on the command line.

```powershell
# Progressive (full state name, lowercase, hyphenated)
python progressive_scraper.py --stage 1 --state north-carolina

# Travelers (2-letter postal code)
python scrape.py --site travelers --stage 1 --state nc
```

The slugs are whatever the carrier's URL uses for that state.

**Heads-up:** each scraper appends to the same per-carrier CSV every time.
To keep one CSV per state, rename the existing file before running a new state:

```powershell
Rename-Item stage1_travelers_agents.csv stage1_travelers_agents_ga.csv
python scrape.py --site travelers --stage 1 --state tx
# Now stage1_travelers_agents.csv only contains Texas rows.
```

---

## Adding a new carrier (Allstate, State Farm, etc.)

The pluggable architecture in `scraper/` makes this straightforward — write one
adapter file, register it, done.

**Steps:**

1. **Scout the carrier's site.** What's the URL shape for state/city/agency
   pages? Do they embed JSON-LD or do you need to scrape HTML directly? Do they
   have anti-bot measures (Cloudflare, etc.)?

2. **Write `scraper/sites/<carrier>.py`** with four functions matching the
   adapter contract:
   ```python
   def state_url(state_slug: str) -> str: ...
   def parse_state_page(html: str, state_slug: str) -> list[str]: ...     # city URLs
   def parse_city_page(html: str, city_url: str) -> list[str]: ...        # agency URLs
   def parse_agency_page(html: str, source_url: str) -> AgencyRecord: ...
   ```
   Use Travelers as a reference — it covers the common patterns (relative URL
   resolution, JSON-LD extraction, fallback handling).

3. **Register in `scrape.py`:** import your module and add it to the `SITES`
   dict near the top of the file.

4. **Write tests in `tests/test_sites_<carrier>.py`** using inline HTML
   snippets. Travelers' test file shows the pattern.

5. **Smoke test:** `python scrape.py --site <carrier> --stage 1 --state <slug> --limit 5`

If the carrier has aggressive anti-bot protection (Cloudflare challenges,
JavaScript-only rendering with required browser fingerprints, etc.), don't try
to bypass it — fall back to Apify or another commercial scraping service.

---

## Tests

The pluggable scraper has **120 unit tests** under `tests/`. Run them all:

```powershell
pytest tests/ -v
```

Run a specific module:

```powershell
pytest tests/test_enrichment.py -v
```

Tests don't touch the network — they use the `responses` library to mock HTTP
and inline HTML strings for parser inputs. Every test should run in under a
second.

`progressive_scraper.py` doesn't have its own test file — it was built before
the test-first refactor and is verified by its production output (852 Georgia
agencies, manually spot-checked).

---

## Known data quirks

### Progressive

1. **Duplicate listings.** Progressive lists some agencies twice with different
   slugs but identical data. Instantly dedupes by email at upload, so this
   rarely matters.
2. **Trailing " Insurance" in some names** ("Rogers-Wood & Assoc., Inc
   Insurance"). Not auto-stripped because plenty of agencies legitimately end
   in that word.
3. **Placeholder rows** with literal `_` in name/phone — Stage 2's "drop rows
   without valid email" filter catches them.
4. **`http://` website URLs** that modern sites redirect to `https://` — no
   functional issue.

### Travelers

1. **Same agency at multiple addresses.** "Marsh & McLennan Agency LLC"
   appeared 3× in one Georgia 50-agency sample. Each row has the same email,
   so Instantly will dedupe.
2. **Name extraction false positives.** Agency websites with CTA text like
   "Email Dale Hodges" historically parsed as `first_name="Email"`. The
   `LEADING_LABELS` strip in `scraper/enrichment.py` handles `Email`/`Call`/`Contact`/`Meet`/`Ask`/`Message`
   prefixes — add more if you spot new patterns.
3. **403/blocked sites.** A small percentage of agency websites refuse
   automated traffic. The row gets `enrichment_status="fetch_failed"` so you
   can filter it out cleanly.
4. **JavaScript-only rendering.** Travelers' directory pages are SPAs — we
   parse the embedded JSON-LD blocks instead of the rendered DOM, which works
   because Travelers ships both a schema.org `InsuranceAgency` block AND a Yext
   verifiable credential. If they ever stop emitting those, the scraper breaks.

---

## Troubleshooting

### "lxml fails to install" / "Failed to build installable wheels"

`requirements.txt` doesn't list `lxml` — we use Python's built-in `html.parser`.
If you see an `lxml` error, check that your `requirements.txt` matches this
repo (no `lxml` line).

### "scripts cannot be loaded because running scripts is disabled"

Windows execution policy. One-time fix:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

### Lots of `[retry]` or `[fail]` lines

Single-digit count: ignore — network blips, retries recovered. Flood: slow down
(see "Tuning the rate limit"). One specific domain producing many failures: that
site is blocking us — keep going, we just won't get their leads.

### `ModuleNotFoundError: No module named 'scraper'` when running pytest

Missing `conftest.py` at the project root. Create an empty file:

```powershell
New-Item conftest.py -ItemType File
```

### Stage 2 CSV is missing rows for some agencies

Check `enrichment_status` — `fetch_failed` rows exist but might have empty
emails. Use Excel/Pandas to filter `enrichment_status in ('found', 'no_name_found')`
before uploading to Instantly.

---

## Files in this project

| File / Folder | Purpose |
|---|---|
| `progressive_scraper.py` | Standalone Progressive scraper. Self-contained. |
| `scrape.py` | CLI for the pluggable scraper. Routes to a site adapter via `--site`. |
| `scraper/` | Reusable scraper package: core HTTP/CSV/crawl + per-site adapters + enrichment. |
| `tests/` | 120 pytest tests. Mock HTTP, no network. |
| `conftest.py` | pytest config — empty file that marks the project root. |
| `requirements.txt` | Pinned runtime deps. |
| `stage1_<carrier>_agents.csv` | Raw scrape output per carrier. Grows during Stage 1. |
| `stage2_<carrier>_enriched.csv` | Instantly-ready output per carrier. |
| `venv/` | Python sandbox. Gitignored. |

---

## Honest scope notes

- **Tested at scale on:** Progressive Georgia (852 agencies, 100% email
  coverage from JSON-LD), Travelers Georgia (50-agency sample, ~56% usable
  email coverage after Stage 2). Other states/carriers will vary.
- **Not bypassing anything.** Both scrapers respect robots.txt (verified for
  both carriers), use polite rate limits, and send an honest User-Agent. If a
  carrier ever adds login walls or CAPTCHAs, the scraper will start failing —
  flag it, don't try to work around it.
- **The User-Agent contains a real contact email.** If a carrier reaches out
  asking us to slow down or stop, we'll hear about it. That's the intended
  design.
- **Stage 2 for Travelers takes hours.** Plan accordingly — leave it running
  overnight or kick it off and walk away. The CSV is durable; even if your
  machine sleeps mid-run you don't lose work.
