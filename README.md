# Congress + FEC Tracker

**Live site: https://congress-fec-tracker-j4fuyjgjbdfjakdgdgmypo.streamlit.app/**
(hosted on Streamlit's free tier, which puts idle apps to sleep — if you see
a "gone to sleep" page, click the button and it's back in about 30 seconds.)

Tracks congressional bills through their full lifecycle — introduced,
in committee, passed House/Senate, signed or vetoed — and pairs that with
FEC itemized donor retention for House/Senate campaign committees across
election cycles. Two ways to connect the two: a bill's sponsor links
directly to their campaign committee's retention numbers, and a bill's
policy area links (heuristically, via keyword matching) to industries with
financial ties to that sponsor.

The whole stack is free: Congress.gov and OpenFEC both give free API keys,
the crosswalk data is public, hosting is Streamlit Community Cloud, scheduled
refreshes run on GitHub Actions (bills) and a local macOS job (FEC), and
storage is a single SQLite file. Nothing here requires a paid tier of
anything.

**Coverage:** bills for the current Congress, and FEC contributions for the
principal campaign committees of current House/Senate members across the
current and two prior election cycles (2022, 2024, 2026).

## How it works

1. **Ingestion** (`agents/legislators_agent.py`, `agents/bills_agent.py`,
   `agents/fec_candidates_agent.py`, `agents/fec_contributions_agent.py`) —
   pulls the bioguide↔FEC crosswalk from
   [unitedstates/congress-legislators](https://github.com/unitedstates/congress-legislators),
   bills/actions/subjects/cosponsors from Congress.gov, and candidates/
   committees/itemized Schedule A contributions from OpenFEC. Runs entirely
   offline, on a schedule, never at request time.
2. **Retention math** (`agents/retention_agent.py`) — for each campaign
   committee, matches donors between consecutive election cycles via a
   name+ZIP fingerprint (SQL self-join on `donor_fingerprints`), and writes
   only the aggregate **"Itemized Donor Retention"** percentage to
   `data/tracker.db`. Individual contributor rows live only in the
   gitignored `.cache/fec_raw.db` and are never committed or displayed.
3. **Industry signal** (`agents/industry_link_agent.py`) — keyword-matches
   the same cached contributor employer/occupation/PAC-name text against
   `industry_keywords.py`'s policy-area table, producing a directional,
   approximate signal — not an authoritative industry classification.
4. **Data-pairing layer** (`sql/views.sql`) — `v_active_bills_overview`,
   `v_bill_sponsor_retention`, and `v_bill_industry_signal` express every
   cross-table join the app needs, as plain SQL views.
5. `app.py` is the Streamlit UI. It makes **zero network calls** — it only
   reads the committed `data/tracker.db`, so no API keys are ever needed to
   run or deploy the app itself.

## Local setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

`data/tracker.db` ships with the repo (once you've run a refresh at least
once — see below), so the app works locally without any API keys. Keys are
only needed to *run a refresh*.

## API keys (both free, instant signup, no card required)

- **Congress.gov** — https://api.congress.gov/sign-up/ → `CONGRESS_API_KEY`
- **OpenFEC** — https://api.data.gov/signup/ → `FEC_API_KEY`

```bash
cp .env.example .env   # then fill in both keys
python scripts/refresh_all.py   # populates data/tracker.db for the first time
```

Never commit `.env` — it's already in `.gitignore`.

## Deploying

1. Push this repo to GitHub.
2. Add `CONGRESS_API_KEY` and `FEC_API_KEY` as **GitHub repo secrets**
   (Settings → Secrets and variables → Actions) — these are used only by
   the scheduled workflows, never by the deployed app.
3. Go to https://share.streamlit.io, connect the repo, set the main file to
   `app.py`. **No secrets need to be set in Streamlit Cloud** — the app
   only reads the committed database.
4. `.github/workflows/refresh_bills.yml` runs daily on GitHub Actions. FEC
   data refreshes weekly from a local macOS job instead (see "Local weekly
   FEC refresh" below); `refresh_fec.yml` remains only as a manual trigger.
   Each publishes an updated `data/tracker.db` via `scripts/publish_db.py`,
   which triggers Streamlit Cloud to auto-redeploy.

## One-time FEC backfill

The first historical pull across ~535 committees is large — days of
wall-clock time under OpenFEC's rate limits. Run it locally, since retention
and industry signals need the local raw fingerprint cache:

    python scripts/refresh_fec.py --chunk-size 999999

It's resumable — each run picks up committee/cycle pairs that aren't yet
`complete` in `fec_ingest_state`, so it's safe to interrupt and re-run.
Closed cycles stay complete forever once backfilled; the current cycle is
periodically reopened (see limitations below) so the weekly refresh keeps it
current too. Then publish with
`python scripts/publish_db.py --owner fec`.

## Known limitations

Stated plainly rather than glossed over:

- **"Itemized Donor Retention" only reflects itemized donors** — FEC
  Schedule A only includes contributions that aggregate over $200 per
  election. Smaller donors are invisible to this metric by construction,
  not by a matching failure.
- **Donor matching is a name+ZIP fingerprint, not a stable person ID.**
  Nicknames, marriage-name changes, and moves cause undercounting; common
  names in dense ZIP codes and joint filers cause overcounting. This isn't
  fixable without a paid identity-resolution service.
- **Industry/interest-group linking is keyword-based**, against a small
  hand-curated table (`industry_keywords.py`), not an authoritative
  CRP/OpenSecrets industry classification. Treat it as a directional signal
  worth investigating further, not a definitive tie.
- **Very high-dollar campaigns' itemized contributions are capped** at
  `config.FEC_MAX_ROWS_PER_COMMITTEE_CYCLE` per committee-cycle, taken in
  chronological order — this only meaningfully affects the very largest
  committees' precision.
- **Bill stage is derived from keyword-matching action text**
  (`agents/bills_agent.py:derive_stage`), not a first-party stage field —
  Congress.gov doesn't expose one directly.
- **Only the current election cycle is kept fresh after its initial
  backfill.** A completed committee/cycle pair is normally never re-polled
  — correct for closed cycles like 2022 and 2024, whose books don't
  change. But the current cycle keeps accumulating new filings while it's
  still active, so those pairs are reopened once their last run is older
  than `config.FEC_CURRENT_CYCLE_REOPEN_STALE_DAYS` (21 days), letting the
  weekly refresh pick up contributions filed since the pair was first
  completed. This means current-cycle numbers can lag reality by up to a
  few weeks between reopenings, not update in real time.
- **Retention and industry signals are only recomputed where the raw
  fingerprint cache exists.** That cache is deliberately gitignored (it
  holds hashed individual-donor data), so it lives only on the machine that
  ran the backfill — a fresh CI runner doesn't have it. CI therefore can't
  recompute them, and the weekly FEC refresh runs locally instead (see
  below). CI's retention step skips pairs with no cached data
  rather than overwriting stored values with empty results.

### Local weekly FEC refresh (macOS)

`scripts/local_fec_refresh.sh` ingests new FEC data, recomputes retention
and industry signals from the local cache, and publishes the result.
`scripts/publish_db.py` is what makes that safe: `data/tracker.db` is one
binary file written by two jobs (CI owns the bills tables, the local job owns
the FEC tables), so publishing takes the latest `origin/main` database and
replaces only the publishing job's own tables before pushing, retrying if the
other job pushed in between. Schedule it with launchd (the plist hardcodes this checkout's path, so edit
it if yours lives elsewhere; keep the repo outside `~/Downloads`, `~/Desktop`
and `~/Documents`, which macOS blocks background jobs from reading):

    cp scripts/com.andykim.congress-fec-refresh.plist ~/Library/LaunchAgents/
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.andykim.congress-fec-refresh.plist

It runs Sundays at 18:00 local (once on wake if the Mac was asleep), logs to
`~/Library/Logs/congress-fec-refresh.log`, and posts a notification on
failure. Run it on demand with
`launchctl kickstart gui/$(id -u)/com.andykim.congress-fec-refresh`.
