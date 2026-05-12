# Progressive Agent Scraper

A two-stage Python scraper that pulls every independent insurance agency from
[Progressive's public agent directory](https://www.progressiveagent.com/local-agent/)
and produces a CSV ready to upload directly to Instantly for cold-email outreach.

Built for Next Call Club. Starting with Georgia; designed to scale to every US
state, and eventually to be ported to other carriers (Travelers, etc.).

---

## What you get

Two CSV files in the project folder when both stages have run:

| File | What's in it |
|---|---|
| `stage1_progressive_agents.csv` | The raw scrape from Progressive's directory: one row per agency, columns include `agency_name`, `address_line`, `city`, `state`, `zip`, `phone`, `website_url`, `email`, `source_url`. This is the data we trust most — pulled from Progressive's own structured schema.org markup. |
| `stage2_progressive_agents_enriched.csv` | The Instantly-ready file. Same data, **remapped to Instantly's required column order**, and rows missing a valid email are dropped. Upload this one. |

Stage 2's exact columns (in this order, with these lowercase headers):

```
email,first_name,last_name,company_name,phone,address,city,state,zip,website,source_url
```

`first_name` and `last_name` are blank — agencies aren't individuals.

---

## Prerequisites

- **Windows 10/11** with PowerShell (the commands below assume PowerShell).
- **Python 3.11 or newer**. Check with `python --version`. If you don't have it,
  install from [python.org/downloads](https://www.python.org/downloads/) and tick
  the "Add Python to PATH" box during install.

---

## First-time setup

Open PowerShell, navigate to the project folder, then run these once:

```powershell
cd C:\Users\victo\Documents\Scraper
```

Create a virtual environment (an isolated Python sandbox for this project):

```powershell
python -m venv venv
```

Activate it:

```powershell
.\venv\Scripts\Activate.ps1
```

> You'll know it worked when your prompt starts with `(venv)`. If you see an
> error about scripts being disabled, run this once and answer `Y`:
> ```powershell
> Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
> ```
> Then re-run the activate line.

Install the three dependencies:

```powershell
pip install -r requirements.txt
```

Quick sanity check:

```powershell
python -c "import requests, bs4, tqdm; print('all good')"
```

You should see `all good`. If not, paste the error to whoever's helping you.

---

## Running it

**Every time you open a new PowerShell session**, activate the venv first:

```powershell
cd C:\Users\victo\Documents\Scraper
.\venv\Scripts\Activate.ps1
```

### Stage 1 — scrape the directory

Full state run (this is the slow one):

```powershell
python progressive_scraper.py --stage 1 --state georgia
```

For Georgia this takes about **40–45 minutes** because of the polite 1–2 second
delays between web requests. You can leave it running and walk away — it prints
per-city and per-agency progress as it goes, and writes rows to disk one at a time.

Other states work the same way — just use the lowercase slug from the URL on
Progressive's site (`new-york`, `texas`, `north-carolina`, etc.).

#### Useful options

- `--limit 20` — stop after 20 new agencies. Use this to test before committing
  to a long run.
- `--start-from <URL>` — skip agency URLs alphabetically before this one. Useful
  if a run got partway through and you want to manually resume from a
  specific point. (Usually you don't need this — see "Resuming" below.)

#### Examples

```powershell
# Quick 20-agency test
python progressive_scraper.py --stage 1 --state georgia --limit 20

# Scrape Texas
python progressive_scraper.py --stage 1 --state texas

# Resume from a specific point if needed
python progressive_scraper.py --stage 1 --state georgia --start-from https://www.progressiveagent.com/local-agent/georgia/macon/
```

### Stage 2 — produce the Instantly-ready file

Once Stage 1 has finished (or even partially run):

```powershell
python progressive_scraper.py --stage 2
```

Runs in under a second. Reads `stage1_progressive_agents.csv`, drops rows with no
email, remaps columns, and writes `stage2_progressive_agents_enriched.csv`.

This is the file you upload to Instantly.

---

## Resuming after a crash or Ctrl+C

Stage 1 is **fully resumable**. Every agency row is written to disk *before* the
next HTTP request starts, and on startup the script reads the existing CSV and
skips any URLs already in it. So:

- Hit Ctrl+C anytime — you won't lose work.
- To resume, just run the exact same command again. It'll skip the rows already
  saved and pick up from where it stopped.

There's no need to delete the CSV between runs unless you want to start over.

---

## Output: producing a sample for review

Want a small file to eyeball before uploading 800+ rows to Instantly? After
Stage 1 has produced its CSV, you can take the first 21 lines (header + 20
agencies) like this:

```powershell
Get-Content stage1_progressive_agents.csv -TotalCount 21 | Set-Content sample_output.csv
python progressive_scraper.py --stage 2
```

The `--stage 2` part rebuilds the full Instantly file. The `sample_output.csv`
is just for spot-checking.

---

## Tuning the rate limit

The script makes one web request every 1–2 seconds (random jitter) to be polite
to Progressive's servers. If you start seeing `[retry]` or HTTP 429 lines in the
output, the server is asking us to slow down — open `progressive_scraper.py` and
bump the constant near the top:

```python
RATE_LIMIT_RANGE = (1.0, 2.0)   # change to (3.0, 5.0) if needed
```

Save, then re-run. Resume will pick up from where you left off.

**Never set the User-Agent to a fake browser.** Honest identification is the
custom string `NextCallClub-AgentScraper/1.0 (contact: victorsalazar@nextcallclub.com)`
and it should stay that way. Fake browser strings violate Progressive's terms
of service and would invite a block.

---

## Known data quirks (these are Progressive's, not ours)

When you look at the Stage 1 CSV you'll see a few things worth knowing:

1. **Duplicate agency listings.** Progressive lists some agencies twice with
   slightly different URL slugs but identical data (same phone, same email).
   Examples we've seen: "The Family Insurance Agency" in Albany, "Hilb Group of
   NC LLC" in Alpharetta. Instantly will treat duplicate emails as the same
   lead, so this rarely matters in practice.
2. **Trailing " Insurance" in some names.** Some agency `name` values in
   Progressive's structured data end with the word "Insurance" appended
   (e.g. "Rogers-Wood & Assoc., Inc Insurance"). We don't auto-strip it because
   plenty of agencies legitimately end in that word ("Trust Insurance", "IGA
   Insurance"). If it bothers your email templates, you can clean specific
   strings in Excel before upload.
3. **Placeholder rows.** Rare records where Progressive has literal `_`
   placeholders in the name and phone fields. The Stage 2 column-remap drops
   any row without a valid email, which catches most placeholder cases.
4. **`http://` vs `https://` in website URLs.** Progressive's structured data
   stores most website URLs as `http://`. Every modern site auto-redirects to
   https, so this doesn't affect functionality. We don't auto-upgrade because
   the rare site that's still http-only would break.

---

## Extending to a new state

It's just a slug change in the command line — no code edits required:

```powershell
python progressive_scraper.py --stage 1 --state north-carolina
python progressive_scraper.py --stage 2
```

The state slug is whatever appears between `/local-agent/` and the next `/` in
Progressive's URL for that state. Lowercase, hyphenated.

**Heads-up:** the script appends to the *same* `stage1_progressive_agents.csv`
file every time. If you want one CSV per state, rename the existing file before
running a new state:

```powershell
Rename-Item stage1_progressive_agents.csv stage1_progressive_agents_georgia.csv
python progressive_scraper.py --stage 1 --state texas
# Now stage1_progressive_agents.csv will only have Texas rows.
```

You'd also need to rename the Stage 2 output similarly if you re-run Stage 2.

---

## Extending to a new carrier (Travelers, etc.)

The script is Progressive-specific in two places:

1. **URL traversal** — `parse_state_page()` and `parse_city_page()` know
   Progressive's URL shape (`/local-agent/<state>/<city>/<agency>/`).
2. **Field extraction** — `parse_agency_page()` reads Progressive's schema.org
   JSON-LD block. Other carriers may or may not embed this block, and may name
   their JSON-LD fields differently.

For Travelers, the path is: scout one of their state directory pages, see how
their URLs are shaped, check whether they embed similar JSON-LD, and either
rewrite the three parsers for Travelers' structure or build a separate
`travelers_scraper.py` that shares the polite `fetch_url`, `load_seen_urls`,
and `append_row` helpers.

---

## Troubleshooting

### "lxml fails to install" or "Failed to build installable wheels"

Python 3.14 doesn't have prebuilt wheels for older `lxml` versions on Windows.
The current `requirements.txt` doesn't list `lxml` at all — we use Python's
built-in `html.parser` instead, which is plenty fast for this workload. If you
see `lxml` in any error, make sure `requirements.txt` matches the version in
this repo (no `lxml` line).

### "scripts cannot be loaded because running scripts is disabled"

You're hitting Windows' execution policy. One-time fix:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

Answer `Y`. Then activate the venv again.

### Stage 1 prints lots of `[retry]` lines

Could be your internet, could be Progressive's site being slow that day, or
could be that we're being lightly rate-limited. A handful is fine (the retries
will succeed). A flood means slow down — bump `RATE_LIMIT_RANGE` (see "Tuning
the rate limit" above).

### Stage 1 prints `[skip] no JSON-LD found`

Means an agency page rendered but didn't embed the structured-data block. Rare.
The script just skips that one and moves on. If you see it on many pages
in a row, Progressive may have changed their template — flag it and we'll
investigate.

### The CSV looks empty or has only a header

Either Stage 1 didn't get to write any rows (likely a network failure on the
state-page fetch), or the state slug is wrong. Check the spelling against the
URL on Progressive's site.

---

## Files in this project

| File | Purpose |
|---|---|
| `progressive_scraper.py` | The whole scraper. Two stages, one file. |
| `requirements.txt` | Locked Python dependencies. |
| `stage1_progressive_agents.csv` | Raw scrape output. Grows as Stage 1 runs. |
| `stage2_progressive_agents_enriched.csv` | Instantly-ready output. Rebuilt every time Stage 2 runs. |
| `venv/` | Isolated Python sandbox (created by `python -m venv venv`). |
| `README.md` | This file. |

---

## Honest scope notes

- This was built and verified on **Georgia only** (852 agencies, 100% email
  coverage from Progressive's JSON-LD). Other states may have different
  coverage. If a state comes back with significantly less than 100% email
  coverage, consider building the website-scraping fallback that was
  originally planned for Stage 2 (see the function structure comments at the
  bottom of `progressive_scraper.py`).
- We're **not bypassing any anti-bot, CAPTCHA, or login walls**. Progressive's
  directory is fully public. If they change the site to require login or add
  CAPTCHAs, the script will start failing — flag it, don't try to work around
  it.
- The User-Agent is honest and contains a real contact email. If Progressive
  ever reaches out asking us to slow down or stop, we'll hear about it. That's
  the intended design.
