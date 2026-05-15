# Insurance Agent Scraper

Pulls insurance agencies from carrier directories (Progressive, Travelers,
State Farm — more to come) into a CSV ready to upload to Instantly for
cold-email outreach.

Built for Next Call Club. Currently runs against:
- **Progressive** — via the standalone `scraper/sites/progressive_scraper.py` script
- **Travelers** — via the newer `scrape.py --site travelers` CLI
- **State Farm** — via `scrape.py --site state_farm` (sitemap-driven, two-pass)

All three produce Instantly-ready output, but they use different implementations
(history: Progressive shipped first as a single file; Travelers prompted a
refactor into a pluggable `scraper/` package; State Farm slotted into that same
package as a sitemap-driven adapter — no state→city→agency walk needed because
State Farm publishes a sitemap that enumerates every agent URL).

Email policy varies by carrier:
- **Progressive** embeds emails in JSON-LD; we extract them in Stage 2.
- **Travelers** has emails for ~40% of agencies; Stage 2 scrapes the rest from
  agency websites.
- **State Farm** doesn't expose emails at all — we ship the agent list and
  hand it to **Clay** for email enrichment downstream.

---

## What you get

After a full run, the file you upload to Instantly (or hand to Clay) is:

| Carrier | Final CSV | Email handling |
|---|---|---|
| Progressive | `stage2_progressive_agents_enriched.csv` | Direct — extracted from JSON-LD |
| Travelers | `stage2_travelers_enriched.csv` | Direct — partially extracted; gaps via website scrape |
| State Farm | `stage2_state_farm_enriched.csv` | **Empty by design — Clay enriches** |

**Progressive's Stage 2 columns** (Instantly's required order):

```
email,first_name,last_name,company_name,phone,address,city,state,zip,website,source_url
```

**State Farm's Stage 2 columns** (same shape as Progressive — `email` always blank):

```
email,first_name,last_name,company_name,phone,address,city,state,zip,website,source_url
```

**Travelers' Stage 2 columns** (adds two diagnostic fields):

```
email,first_name,last_name,name_source,company_name,phone,address,city,state,zip,website,source_url,enrichment_status
```

The two extra Travelers fields:
- `name_source` — where the name came from: `team_page` | `contact_page` | `email_local_part` | `no_name_found`
- `enrichment_status` — overall outcome for the row: `found` | `no_name_found` | `no_email_found` | `fetch_failed`

You can ignore `name_source` and `enrichment_status` on upload — Instantly will just import them as extra columns. They're useful for filtering rows by quality before sending.

**State Farm also writes a Stage 1 lite CSV** (`stage1_state_farm_agents.csv`)
that's worth knowing about — it's the sitemap parsed into rows, written in
~5 minutes, and shippable to Clay on its own if you want a preview list before
the long Stage 2 enrichment finishes:

```
source_url,state,city,first_name_guess,last_name_guess,agent_id
```

The `*_guess` columns are mined from URL slugs (lossy on `Mc`/`O'` casing and
middle initials); Stage 2 overwrites them with cleaner names from JSON-LD.

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
| Scrape Progressive (Georgia) | `python scraper/sites/progressive_scraper.py --stage 1 --state georgia` then `--stage 2` |
| Scrape Travelers (Georgia) | `python scrape.py --site travelers --stage 1 --state ga` then `--stage 2` |
| Scrape State Farm (Georgia) | `python scrape.py --site state_farm --stage 1 --state ga` then `--stage 2` |
| Scrape State Farm (all 50 states) | `python scrape.py --site state_farm --stage 1` (omit `--state`) then `--stage 2` |
| Test before committing to a long run | Add `--limit 20` to the Stage 1 command |
| Resume after Ctrl+C | Re-run the same command — it skips rows already in the CSV |

---

## Progressive workflow

Progressive embeds full agency data (name, address, phone, **email**) in
schema.org JSON-LD on every detail page. We saw 100% email coverage on Georgia,
so Stage 2 is just a column remap — no website scraping needed.

**Stage 1** — crawl the directory:

```powershell
python scraper/sites/progressive_scraper.py --stage 1 --state georgia
```

Takes ~40–45 minutes for Georgia (~852 agencies, polite 1–2s delay per request).
Other states use the lowercase slug from Progressive's URL (`new-york`, `texas`,
`north-carolina`, etc.).

**Stage 2** — produce the Instantly file:

```powershell
python scraper/sites/progressive_scraper.py --stage 2
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

## State Farm workflow

State Farm publishes a sitemap (`statefarm.com/sitemap-agents.xml`) that
enumerates every agent URL in the country — about **25,890 agents**, of which
~20K are individual agent detail pages. The adapter is sitemap-driven instead
of crawl-driven: there's no state→city→agency walk, just iterate the sitemap.

State Farm doesn't expose agent emails on the page (their site routes inquiries
through contact forms). We don't try to scrape around that — Andrew's strategy
is to ship the agent list with name/phone/address/personal-website and let
**Clay** do the email enrichment. The Stage 2 CSV's `email` column is always
blank by design.

**Stage 1** — fast sitemap parse into a lite CSV (no agent-page fetches):

```powershell
python scrape.py --site state_farm --stage 1 --state ga       # one state
python scrape.py --site state_farm --stage 1                  # all 50 states
```

Per-state Stage 1 runs in ~30 seconds. The all-states run is still fast (~5
minutes — single sitemap fetch + ~25K CSV appends). When you omit `--state`
the script gives you a 5-second window to Ctrl+C before it commits to the full
national enumeration.

Stage 1 writes `stage1_state_farm_agents.csv` with one row per agent:

```
source_url,state,city,first_name_guess,last_name_guess,agent_id
```

You could ship this CSV to Clay as-is for a quick preview, but Stage 2 gets
you cleaner names + phone + full street address + the agent's personal website.

**Stage 2** — fetch each agent page, parse JSON-LD, write enriched CSV:

```powershell
python scrape.py --site state_farm --stage 2
```

Per-state Stage 2 runs ~25 minutes (avg ~880 agents @ 1.5s/req). National
Stage 2 takes ~8 hours — plan accordingly. Resumable, so safe to interrupt.

**Where the data comes from:** every State Farm agent page embeds an
`InsuranceAgency` JSON-LD block with name, phone, full address, and (for most
agents) a `sameAs` field pointing to the agent's personal website or Facebook
page. We grab `founder.name` for the cleanest agent name (the top-level `name`
field has marketing copy like " - State Farm Agent - Forest Park, GA" that
gets stripped if `founder` is missing).

**Useful flags:**

- `--state <2-letter>` — Stage 1 only. Optional. Filters the sitemap to one state.
- `--limit N` — stop after N new agents (useful for smoke testing both stages).

**Rough hit rates from the GA smoke test (8 agents):**

| Field | Coverage |
|---|---|
| Name + phone + address | 100% (in JSON-LD on every page) |
| Personal website (`sameAs`) | ~100% in our sample, but a chunk are Facebook pages rather than real personal domains |
| Email | 0% — by design; Clay handles |

The Facebook-vs-personal-domain split for `sameAs` is real-world data quality
worth knowing — Clay enriches better from a real domain than a Facebook URL.

**Akamai + the wide-block guard:** State Farm sits behind Akamai Bot Manager.
At our pace (1.5s/req, honest user-agent) we don't get blocked, but the long
national run is exposure. The HTTP layer has a `WideBlockError` guard — five
consecutive 403s from one host triggers a hard stop and clean exit (code 2),
so a wide-block can't burn hours of doomed retries. The CSV is intact on exit;
re-run after waiting or changing IP to resume from where it stopped.

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

- Progressive: `scraper/sites/progressive_scraper.py` → `RATE_LIMIT_RANGE = (1.0, 2.0)` near the top
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
├── scrape.py                       # CLI for the pluggable scraper (Travelers, State Farm, +future)
├── scraper/                        # Pluggable scraper package
│   ├── records.py                  # AgencyRecord, EnrichedLead dataclasses
│   ├── enrichment.py               # Website scraping: emails, names, dedup
│   ├── core/
│   │   ├── http.py                 # Polite HTTP fetch + per-domain rate limiter + WideBlockError
│   │   ├── csv_io.py               # Append-row + resumability lookup
│   │   ├── crawl.py                # Generic state→city→agency loop (used by Travelers)
│   │   ├── jsonld.py               # JSON-LD reader (handles @graph, malformed blocks)
│   │   └── text.py                 # clean(), format_phone(), looks_like_email()
│   └── sites/
│       ├── progressive_scraper.py  # Standalone Progressive script (co-located, not a pluggable adapter)
│       ├── state_farm.py           # State Farm adapter (sitemap-driven: iter_agency_urls)
│       └── travelers.py            # Travelers adapter (state_url, parse_*)
├── tests/                          # 192 unit tests (pytest)
│   ├── test_records.py
│   ├── test_core_http.py
│   ├── test_core_csv_io.py
│   ├── test_core_crawl.py
│   ├── test_core_jsonld.py
│   ├── test_core_text.py
│   ├── test_sites_travelers.py
│   ├── test_sites_state_farm.py
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
python scraper/sites/progressive_scraper.py --stage 1 --state north-carolina

# Travelers (2-letter postal code)
python scrape.py --site travelers --stage 1 --state nc

# State Farm (2-letter postal code; --state is optional — omit for all 50)
python scrape.py --site state_farm --stage 1 --state nc
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

## Adding a new carrier (Allstate, Liberty Mutual, etc.)

The pluggable architecture in `scraper/` supports two adapter shapes — pick
whichever matches the carrier you're scraping.

**Crawl-driven** (Travelers pattern): use this when the carrier publishes
state/city/agency pages and you need to walk that hierarchy. Adapter exposes
four functions:

```python
def state_url(state_slug: str) -> str: ...
def parse_state_page(html: str, state_slug: str) -> list[str]: ...     # city URLs
def parse_city_page(html: str, city_url: str) -> list[str]: ...        # agency URLs
def parse_agency_page(html: str, source_url: str) -> AgencyRecord: ...
```

The shared `scraper/core/crawl.py:run_crawl` does the walking; you just supply
the parsers. Use [scraper/sites/travelers.py](scraper/sites/travelers.py) as a
reference for the common patterns (relative URL resolution, JSON-LD extraction,
fallback handling).

**Sitemap-driven** (State Farm pattern): use this when the carrier publishes a
sitemap that enumerates every agent URL. Adapter exposes:

```python
SITEMAP_URL = "https://example.com/sitemap-agents.xml"

def iter_agency_urls(session, limiter, state_slug=None) -> Iterator[str]: ...
def parse_url_slug(url: str) -> dict[str, str]: ...                    # for Stage 1 lite CSV
def parse_agency_page(html: str, source_url: str) -> AgencyRecord: ...
```

Stage 1 just iterates the sitemap and writes a lite CSV (no agent-page fetches);
Stage 2 reads that CSV and enriches. Use [scraper/sites/state_farm.py](scraper/sites/state_farm.py)
as a reference. Stage 1 + Stage 2 handlers in `scrape.py` are per-carrier
(`run_state_farm_stage1`, etc.) — copy that pattern for your new carrier.

**Steps for either shape:**

1. **Scout the carrier's site.** Robots.txt, render type (server-side HTML vs
   JS-only), JSON-LD presence, anti-bot stack (Cloudflare/Akamai/Imperva), and
   — critically — **does a sitemap exist?** Check `/sitemap.xml` first. If the
   carrier enumerates agents in a sitemap, the sitemap-driven adapter is much
   less fragile than HTML scraping.
2. **Write the adapter** in `scraper/sites/<carrier>.py` matching whichever
   contract above fits.
3. **Register in `scrape.py`'s `SITES` dict.** For sitemap-driven adapters,
   also add per-carrier `run_<carrier>_stage1` / `run_<carrier>_stage2`
   handlers and branch on `args.site` in `main()`.
4. **Write tests in `tests/test_sites_<carrier>.py`** using inline HTML/XML
   snippets. Both Travelers' and State Farm's test files show the pattern.
5. **Smoke test:** `python scrape.py --site <carrier> --stage 1 --state <slug> --limit 5`

If the carrier has aggressive anti-bot protection (Cloudflare challenges,
JavaScript-only rendering with required browser fingerprints, etc.), don't try
to bypass it — fall back to Apify or another commercial scraping service.

---

## Tests

The pluggable scraper has **192 unit tests** under `tests/`. Run them all:

```powershell
pytest tests/ -v
```

Run a specific module:

```powershell
pytest tests/test_enrichment.py -v
pytest tests/test_sites_state_farm.py -v
```

Tests don't touch the network — they use the `responses` library to mock HTTP
and inline HTML/XML strings for parser inputs. The full suite runs in under
30 seconds.

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

### State Farm

1. **`agency_name` holds the agent's person name, not a company name.**
   We pull `founder.name` from JSON-LD ("Harold Mitchell Jr") rather than the
   marketing-suffixed top-level `name` ("…- State Farm Agent - Forest Park, GA").
   The HTML card on city pages does have an "Agency Name Inc"-style field
   ("Jeff Caler Ins Agency Inc") — it's just not in the JSON-LD on the agent
   detail page, and we don't fetch the city page during Stage 2.
2. **City in CSV may differ from city in URL slug.** A `/east-point/...` URL
   can have `city=Atlanta` in the CSV when the agent's mailing address says
   Atlanta even though the official municipality is East Point. JSON-LD
   address wins. If a downstream join uses the URL-slug city, that'll bite.
3. **Stage 1 slug-mined names are lossy.** `Mc`/`Mac`/`O'` casing flattens
   (`mccrory` → `Mccrory` not `McCrory`), middle initials disappear, and
   first/last split is heuristic ("Mary Jane Smith" → first="Mary",
   last="Jane Smith"). Stage 2 overwrites with the cleaner JSON-LD name —
   only an issue if you ship the Stage 1 lite CSV directly to Clay.
4. **`sameAs` website is sometimes a Facebook page.** Many agents list their
   Facebook URL in `sameAs` instead of a real personal domain. We capture
   it; Clay will enrich it less effectively than a real domain.
5. **Akamai Bot Manager fingerprints us.** Single requests pass cleanly with
   our honest user-agent + 1.5s rate limit. The `WideBlockError` guard
   (5 consecutive 403s = hard stop) protects long Stage 2 runs from a
   sustained block burning hours of doomed retries.
6. **Email is always empty.** State Farm doesn't expose agent emails on the
   page; the `email` column is blank by design. Clay handles enrichment.

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
| `scraper/sites/progressive_scraper.py` | Standalone Progressive scraper. Self-contained. |
| `scrape.py` | CLI for the pluggable scraper. Routes to a site adapter via `--site`. |
| `scraper/` | Reusable scraper package: core HTTP/CSV/crawl + per-site adapters + enrichment. |
| `scraper/sites/state_farm.py` | State Farm adapter (sitemap-driven, no state→city walk). |
| `scraper/sites/travelers.py` | Travelers adapter (state→city→agency crawl). |
| `tests/` | 192 pytest tests. Mock HTTP, no network. |
| `conftest.py` | pytest config — empty file that marks the project root. |
| `requirements.txt` | Pinned runtime deps. |
| `stage1_<carrier>_agents.csv` | Raw scrape output per carrier. Grows during Stage 1. |
| `stage2_<carrier>_enriched.csv` | Instantly-ready (or Clay-ready) output per carrier. |
| `venv/` | Python sandbox. Gitignored. |

---

## Honest scope notes

- **Tested at scale on:** Progressive Georgia (852 agencies, 100% email
  coverage from JSON-LD), Travelers Georgia (50-agency sample, ~56% usable
  email coverage after Stage 2), State Farm Georgia (8-agent smoke sample —
  Stage 1 enumerated 884 agents from sitemap; Stage 2 produced clean
  name/phone/address/website for all 8). Larger State Farm runs and other
  states/carriers will vary.
- **Not bypassing anything.** All three scrapers respect robots.txt (verified
  for each carrier), use polite rate limits, and send an honest User-Agent.
  If a carrier ever adds login walls or CAPTCHAs, the scraper will start
  failing — flag it, don't try to work around it.
- **The User-Agent contains a real contact email.** If a carrier reaches out
  asking us to slow down or stop, we'll hear about it. That's the intended
  design.
- **Stage 2 for Travelers takes hours.** Plan accordingly — leave it running
  overnight or kick it off and walk away. The CSV is durable; even if your
  machine sleeps mid-run you don't lose work.
- **State Farm national Stage 2 takes ~8 hours.** Per-state runs are ~25 min
  each. The `WideBlockError` guard hard-stops if Akamai escalates so you
  don't burn hours on doomed retries — but the bigger the run, the more
  exposure. Prefer state-by-state runs for new IPs/sessions.
- **State Farm emails come from Clay, not from us.** This is by design —
  State Farm doesn't expose emails on their pages. If you upload the State
  Farm Stage 2 CSV directly to Instantly, every row's `email` column will be
  blank. Run it through Clay first.
