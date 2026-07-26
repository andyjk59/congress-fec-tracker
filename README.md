# Congress + FEC Tracker

Tracks congressional bills through their full lifecycle — introduced,
in committee, passed House/Senate, signed or vetoed — and pairs that with
FEC itemized donor retention for House/Senate campaign committees across
election cycles. Two ways to connect the two: a bill's sponsor links
directly to their campaign committee's retention numbers, and a bill's
policy area links (heuristically, via keyword matching) to industries with
financial ties to that sponsor.

The whole stack is free: Congress.gov and OpenFEC both give free API keys,
the crosswalk data is public, hosting is GitHub Actions + Streamlit
Community Cloud, and storage is a single SQLite file. Nothing here requires
a paid tier of anything.

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
4. `.github/workflows/refresh_bills.yml` runs daily;
   `.github/workflows/refresh_fec.yml` runs weekly. Each commits an updated
   `data/tracker.db`, which triggers Streamlit Cloud to auto-redeploy.

## One-time FEC backfill

The first historical pull across ~535 committees is too large for a single
scheduled run. After deploying, trigger `refresh_fec.yml` manually a
handful of times via **Actions → Refresh FEC data → Run workflow**, raising
`chunk_size` if you want fewer, larger runs. Each run is resumable — it
picks up committee/cycle pairs that aren't yet `complete` in
`fec_ingest_state`. Once every committee shows complete, the weekly
schedule is enough to stay current.

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
