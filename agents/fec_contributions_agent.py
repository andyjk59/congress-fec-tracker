"""OpenFEC Schedule A (itemized individual contributions) ingestion.

Resumable and chunked: each (committee_id, cycle) pair is checkpointed in
fec_ingest_state so a run can be re-triggered repeatedly (via
`workflow_dispatch --chunk-size N`) until every committee is `complete`,
without ever re-pulling data already fetched. Writes only to the gitignored
`.cache/fec_raw.db` -- raw contributor rows are never committed to git and
never touch `data/tracker.db`; only aggregated fingerprint totals are kept,
and even those never leave the cache DB (retention_agent.py reads them to
produce the aggregate-only `donor_retention` table).

Per-committee-per-cycle pull is capped at
config.FEC_MAX_ROWS_PER_COMMITTEE_CYCLE, taken in chronological
(contribution_receipt_date) order -- documented in the README as a bound on
the very highest-dollar campaigns, not true even-sampling.
"""

import hashlib
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

import config
import db

SUFFIXES = (" JR", " SR", " II", " III", " IV")


def fingerprint(contributor_name: str, zip_code: str) -> str | None:
    if not contributor_name or not zip_code:
        return None
    name = contributor_name.upper().strip()
    for suffix in SUFFIXES:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    if "," in name:
        last, rest = name.split(",", 1)
        first_initial = rest.strip()[:1]
    else:
        parts = name.split()
        last = parts[-1] if parts else name
        first_initial = parts[0][:1] if parts else ""
    last = "".join(ch for ch in last if ch.isalnum())
    zip5 = (zip_code or "").strip()[:5]
    if not last or not zip5:
        return None
    return hashlib.sha256(f"{last}|{first_initial}|{zip5}".encode()).hexdigest()


def _get(session: requests.Session, api_key: str, params: dict) -> dict:
    params = dict(params, api_key=api_key)
    # OpenFEC enforces both a per-minute burst limit and a per-hour quota. A
    # sustained backfill will hit the hourly quota even while respecting the
    # per-minute throttle, so on 429 we wait out Retry-After (or an
    # escalating backoff up to 5 minutes) rather than giving up after a few
    # seconds -- a multi-hour run should stall and resume, not crash. Plain
    # network hiccups (timeouts, connection resets) get the same patience.
    for attempt in range(8):
        try:
            resp = session.get(f"{config.FEC_API_BASE}/schedules/schedule_a/", params=params, timeout=30)
        except requests.exceptions.RequestException:
            if attempt == 7:
                raise
            time.sleep(min(5 * 2 ** attempt, 300))
            continue
        if resp.status_code == 429 or resp.status_code >= 500:
            wait = int(resp.headers.get("Retry-After", min(5 * 2 ** attempt, 300)))
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()
    return {}


def _get_state(main_conn, committee_id: str, cycle: int) -> dict:
    row = main_conn.execute(
        "SELECT * FROM fec_ingest_state WHERE committee_id = ? AND cycle = ?", (committee_id, cycle)
    ).fetchone()
    return dict(row) if row else {"complete": 0, "rows_pulled": 0, "last_index": None,
                                    "last_contribution_receipt_date": None}


def _save_state(main_conn, committee_id, cycle, last_index, last_date, complete, rows_pulled):
    import datetime

    main_conn.execute(
        """
        INSERT INTO fec_ingest_state (committee_id, cycle, last_index, last_contribution_receipt_date,
                                       complete, rows_pulled, last_run_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(committee_id, cycle) DO UPDATE SET
          last_index=excluded.last_index, last_contribution_receipt_date=excluded.last_contribution_receipt_date,
          complete=excluded.complete, rows_pulled=excluded.rows_pulled, last_run_at=excluded.last_run_at
        """,
        (committee_id, cycle, last_index, last_date, int(complete), rows_pulled,
         datetime.datetime.now(datetime.UTC).isoformat()),
    )
    main_conn.commit()


def _upsert_fingerprint(cache_conn, committee_id, cycle, fp, employer, occupation, entity_type, amount):
    cache_conn.execute(
        """
        INSERT INTO donor_fingerprints (committee_id, cycle, fingerprint_hash, employer, occupation,
                                         entity_type, total_amount, contribution_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1)
        ON CONFLICT(committee_id, cycle, fingerprint_hash) DO UPDATE SET
          total_amount = total_amount + excluded.total_amount,
          contribution_count = contribution_count + 1
        """,
        (committee_id, cycle, fp, employer, occupation, entity_type, amount),
    )


def _upsert_org_donor(main_conn, committee_id, cycle, donor_name, entity_type, amount):
    # Organizational/PAC contributor names are public FEC disclosure, unlike
    # individual donor identity -- safe to write to the committed main DB
    # rather than the gitignored cache. See schema.sql for the rationale.
    main_conn.execute(
        """
        INSERT INTO fec_committee_org_donors (committee_id, cycle, donor_name, entity_type,
                                               total_amount, contribution_count)
        VALUES (?, ?, ?, ?, ?, 1)
        ON CONFLICT(committee_id, cycle, donor_name) DO UPDATE SET
          total_amount = total_amount + excluded.total_amount,
          contribution_count = contribution_count + 1
        """,
        (committee_id, cycle, donor_name, entity_type, amount),
    )


def ingest_committee_cycle(session, main_conn, cache_conn, api_key, committee_id, cycle) -> bool:
    """Pulls one (committee, cycle) to completion or the row cap. Returns True if now complete."""
    state = _get_state(main_conn, committee_id, cycle)
    if state["complete"]:
        return True

    last_index = state["last_index"]
    last_date = state["last_contribution_receipt_date"]
    rows_pulled = state["rows_pulled"]
    sleep_s = 60.0 / config.FEC_RATE_LIMIT_PER_MINUTE

    while rows_pulled < config.FEC_MAX_ROWS_PER_COMMITTEE_CYCLE:
        params = {
            "committee_id": committee_id,
            "two_year_transaction_period": cycle,
            "per_page": config.FEC_PAGE_SIZE,
            "sort": "contribution_receipt_date",
            "sort_hide_null": "true",
        }
        if last_index is not None:
            params["last_index"] = last_index
            params["last_contribution_receipt_date"] = last_date

        data = _get(session, api_key, params)
        results = data.get("results", [])
        if not results:
            _save_state(main_conn, committee_id, cycle, last_index, last_date, complete=True, rows_pulled=rows_pulled)
            return True

        for r in results:
            entity_type = r.get("entity_type")
            amount = r.get("contribution_receipt_amount") or 0
            fp = fingerprint(r.get("contributor_name"), r.get("contributor_zip"))
            if fp:
                _upsert_fingerprint(
                    cache_conn, committee_id, cycle, fp,
                    r.get("contributor_employer"), r.get("contributor_occupation"),
                    entity_type, amount,
                )
            if entity_type and entity_type != "IND" and r.get("contributor_name"):
                _upsert_org_donor(main_conn, committee_id, cycle, r["contributor_name"], entity_type, amount)
            last_index = r.get("index")
            last_date = r.get("contribution_receipt_date")
        cache_conn.commit()
        main_conn.commit()
        rows_pulled += len(results)
        _save_state(main_conn, committee_id, cycle, last_index, last_date, complete=False, rows_pulled=rows_pulled)
        time.sleep(sleep_s)

    _save_state(main_conn, committee_id, cycle, last_index, last_date, complete=True, rows_pulled=rows_pulled)
    return True


def principal_committees(main_conn) -> list[str]:
    rows = main_conn.execute("SELECT committee_id FROM fec_committees WHERE designation = 'P'").fetchall()
    return [r["committee_id"] for r in rows]


def cycles_to_ingest() -> list[int]:
    current = config.current_cycle()
    return [current, current - 2, current - 4]  # current + 2 prior, enough for two retention pairs


def refresh(main_conn, cache_conn, api_key: str, chunk_size: int | None = None) -> int:
    session = requests.Session()
    committees = principal_committees(main_conn)
    pending = [
        (cid, cycle)
        for cid in committees
        for cycle in cycles_to_ingest()
        if not _get_state(main_conn, cid, cycle)["complete"]
    ]
    if chunk_size:
        pending = pending[:chunk_size]

    for committee_id, cycle in pending:
        ingest_committee_cycle(session, main_conn, cache_conn, api_key, committee_id, cycle)
    return len(pending)


if __name__ == "__main__":
    import argparse
    import os

    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--chunk-size", type=int, default=50, help="committee/cycle pairs per run")
    args = parser.parse_args()

    api_key = os.environ["FEC_API_KEY"]
    main_connection = db.connect()
    db.init_main_db(main_connection)
    cache_connection = db.connect(config.CACHE_DB_PATH)
    db.init_cache_db(cache_connection)

    n = refresh(main_connection, cache_connection, api_key, chunk_size=args.chunk_size)
    print(f"Ingested {n} committee/cycle pairs this run")
