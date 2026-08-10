"""OpenFEC ingestion: candidates + principal campaign committees for current
House/Senate members, using the FEC candidate IDs already resolved by
legislators_agent.py (via the congress-legislators crosswalk).
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import http_utils


def _get(path: str, api_key: str, params: dict | None = None) -> dict:
    params = dict(params or {})
    params["api_key"] = api_key
    # See http_utils.get_with_retry for the rate-limit backoff and the hard
    # wall-clock deadline (requests' own timeout doesn't bound total request
    # duration, which let a frozen connection hang the FEC backfill twice).
    return http_utils.get_with_retry(f"{config.FEC_API_BASE}{path}", params)


def current_member_fec_ids(conn) -> list[tuple[str, str]]:
    """Returns (bioguide_id, fec_candidate_id) pairs for current House/Senate members."""
    rows = conn.execute(
        "SELECT bioguide_id, fec_candidate_ids FROM legislators WHERE chamber IN ('house', 'senate')"
    ).fetchall()
    pairs = []
    for row in rows:
        for fec_id in json.loads(row["fec_candidate_ids"] or "[]"):
            pairs.append((row["bioguide_id"], fec_id))
    return pairs


def fetch_candidate(candidate_id: str, api_key: str) -> dict | None:
    data = _get(f"/candidate/{candidate_id}/", api_key)
    results = data.get("results", [])
    return results[0] if results else None


def fetch_principal_committees(candidate_id: str, api_key: str) -> list[dict]:
    data = _get(f"/candidate/{candidate_id}/committees/", api_key, {"per_page": 100})
    return [c for c in data.get("results", []) if c.get("designation") == "P"]


def upsert_candidate(conn, candidate_id, bioguide_id, info):
    conn.execute(
        """
        INSERT INTO fec_candidates (candidate_id, bioguide_id, name, party, office, state, district)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(candidate_id) DO UPDATE SET
          bioguide_id=excluded.bioguide_id, name=excluded.name, party=excluded.party,
          office=excluded.office, state=excluded.state, district=excluded.district
        """,
        (
            candidate_id, bioguide_id, info.get("name"), info.get("party"),
            info.get("office_full"), info.get("state"), info.get("district"),
        ),
    )


def upsert_committee(conn, committee: dict, candidate_id: str):
    conn.execute(
        """
        INSERT INTO fec_committees (committee_id, candidate_id, name, designation)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(committee_id) DO UPDATE SET
          candidate_id=excluded.candidate_id, name=excluded.name, designation=excluded.designation
        """,
        (committee["committee_id"], candidate_id, committee.get("name"), committee.get("designation")),
    )


def refresh(conn, api_key: str, limit: int | None = None) -> int:
    count = 0
    for bioguide_id, candidate_id in current_member_fec_ids(conn):
        info = fetch_candidate(candidate_id, api_key)
        if not info:
            continue
        upsert_candidate(conn, candidate_id, bioguide_id, info)
        for committee in fetch_principal_committees(candidate_id, api_key):
            upsert_committee(conn, committee, candidate_id)
        conn.commit()
        count += 1
        if limit and count >= limit:
            break
    return count


if __name__ == "__main__":
    import argparse
    import os

    import db
    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="cap candidates pulled, for testing")
    args = parser.parse_args()

    api_key = os.environ["FEC_API_KEY"]
    connection = db.connect()
    db.init_main_db(connection)
    n = refresh(connection, api_key, limit=args.limit)
    print(f"Refreshed {n} candidates/committees")
