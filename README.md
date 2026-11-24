# Congress + FEC Tracker

Live site: https://congress-fec-tracker-j4fuyjgjbdfjakdgdgmypo.streamlit.app/

The site runs on Streamlit's free tier, which puts idle apps to sleep. If you
see a "gone to sleep" page, click the button and wait about 30 seconds.

Tracks congressional bills from introduction through committee, House and
Senate votes, and signing or veto, and pairs them with FEC donor retention for
House and Senate campaign committees. Bills connect to donor data in two ways.
A bill's sponsor links directly to their campaign committee's retention
numbers, and a bill's policy area links, through keyword matching, to
industries with financial ties to that sponsor.

Everything used here is free. Congress.gov and OpenFEC give out free API keys,
the legislator crosswalk is public data, the app is hosted on Streamlit
Community Cloud, scheduled refreshes run on GitHub Actions (bills) and a local
macOS job (FEC), and storage is one SQLite file.

Coverage: bills for the current Congress, and FEC contributions for the
principal campaign committees of current House and Senate members in the 2022,
2024, and 2026 cycles.

## How it works

1. Ingestion (`agents/legislators_agent.py`, `agents/bills_agent.py`,
   `agents/fec_candidates_agent.py`, `agents/fec_contributions_agent.py`).
   Pulls the bioguide-to-FEC crosswalk from
   [unitedstates/congress-legislators](https://github.com/unitedstates/congress-legislators),
   bills, actions, subjects and cosponsors from Congress.gov, and candidates,
   committees and itemized Schedule A contributions from OpenFEC. It runs
   offline on a schedule and never at request time.
2. Retention (`agents/retention_agent.py`). For each campaign committee,
   matches donors across consecutive election cycles by a name and ZIP
   fingerprint (a SQL self-join on `donor_fingerprints`) and writes only the
   aggregate "Itemized Donor Retention" percentage to `data/tracker.db`.
   Individual contributor rows stay in the gitignored `.cache/fec_raw.db` and
   are never committed or shown.
3. Industry signal (`agents/industry_link_agent.py`). Matches cached
   contributor employer, occupation and PAC-name text against the policy-area
   keywords in `industry_keywords.py`. The result is an approximate signal,
   not an industry classification.
4. SQL views (`sql/views.sql`). `v_active_bills_overview`,
   `v_bill_sponsor_retention` and `v_bill_industry_signal` hold every
   cross-table join the app uses.
5. `app.py` is the Streamlit UI. It makes no network calls and only reads the
   committed `data/tracker.db`, so the app needs no API keys.

## Local setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

`data/tracker.db` is committed, so the app runs locally without API keys. Keys
are only needed to run a refresh.

## API keys

Both are free and need no card.

- Congress.gov: https://api.congress.gov/sign-up/ (`CONGRESS_API_KEY`)
- OpenFEC: https://api.data.gov/signup/ (`FEC_API_KEY`)

```bash
cp .env.example .env   # then fill in both keys
python scripts/refresh_all.py   # populates data/tracker.db for the first time
```

Never commit `.env`. It is already in `.gitignore`.

## Deploying

1. Push the repo to GitHub.
2. Add `CONGRESS_API_KEY` and `FEC_API_KEY` as GitHub repo secrets (Settings >
   Secrets and variables > Actions). Only the scheduled workflows use them.
3. At https://share.streamlit.io, connect the repo and set the main file to
   `app.py`. The app needs no secrets there.
4. `.github/workflows/refresh_bills.yml` runs daily on GitHub Actions. FEC data
   refreshes weekly from a local macOS job (see "Local weekly FEC refresh"
   below), and `refresh_fec.yml` is a manual trigger only. Each job publishes
   `data/tracker.db` with `scripts/publish_db.py`, and each push redeploys the
   Streamlit app.

## Initial FEC backfill

The first pull across about 535 committees takes days because of OpenFEC's
rate limits. Run it locally, since retention and industry signals need the
local fingerprint cache:

    python scripts/refresh_fec.py --chunk-size 999999

Each run resumes from `fec_ingest_state`, so you can interrupt and re-run it.
When it finishes, publish with `python scripts/publish_db.py --owner fec`.
Completed pairs for past cycles are never fetched again. Pairs for the current
cycle are reopened periodically (see limitations below).

## Known limitations

- Retention counts itemized donors only. FEC Schedule A includes contributions
  that total over $200 per election, so smaller donors never appear.
- Donors are matched by name and ZIP, not a stable ID. Nicknames, name changes
  and moves cause undercounting. Common names in dense ZIP codes and joint
  filers cause overcounting. Fixing this would take a paid identity-resolution
  service.
- Industry linking uses keywords from a small hand-written table
  (`industry_keywords.py`), not a standard classification such as CRP or
  OpenSecrets. Treat it as a rough signal.
- Itemized contributions are capped at `config.FEC_MAX_ROWS_PER_COMMITTEE_CYCLE`
  per committee and cycle, taken in date order. This only affects the largest
  committees.
- Bill stage comes from keyword matching on action text
  (`agents/bills_agent.py:derive_stage`). Congress.gov has no stage field.
- Only the current cycle stays up to date after its first backfill. Completed
  pairs for 2022 and 2024 are never fetched again because those cycles are
  closed. Pairs for the current cycle are reopened once their last run is more
  than `config.FEC_CURRENT_CYCLE_REOPEN_STALE_DAYS` (21) days old, so the
  weekly refresh picks up newer filings. Current-cycle numbers can lag by a few
  weeks.
- Retention and industry signals need the raw fingerprint cache. The cache is
  gitignored because it holds hashed individual-donor data, so it exists only
  on the machine that ran the backfill, and GitHub Actions can't recompute
  these values. The FEC refresh therefore runs locally (see below). The
  Actions retention step skips pairs with no cached data so it can't overwrite
  stored values with empty ones.

### Local weekly FEC refresh (macOS)

`scripts/local_fec_refresh.sh` ingests new FEC data, recomputes retention and
industry signals from the local cache, and publishes the result.

`data/tracker.db` is one binary file written by two jobs: GitHub Actions owns
the bills tables and the local job owns the FEC tables. Git can't merge it, so
`scripts/publish_db.py` takes the latest `origin/main` database, replaces only
the publishing job's tables, and pushes. If the other job pushed in between, it
retries.

To schedule the local job with launchd:

    cp scripts/com.andykim.congress-fec-refresh.plist ~/Library/LaunchAgents/
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.andykim.congress-fec-refresh.plist

The plist hardcodes this checkout's path, so edit it if yours is elsewhere. Keep
the repo outside `~/Downloads`, `~/Desktop` and `~/Documents`, because macOS
blocks background jobs from reading those folders.

It runs Sundays at 18:00 local, or once on wake if the Mac was asleep. Logs go
to `~/Library/Logs/congress-fec-refresh.log`, and it posts a notification if it
fails. To run it now:
`launchctl kickstart gui/$(id -u)/com.andykim.congress-fec-refresh`.
