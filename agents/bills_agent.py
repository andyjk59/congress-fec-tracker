"""Congress.gov ingestion: bills, actions (stage history), subjects, cosponsors.

Refreshes are incremental by default -- only bills updated since the last
refresh are re-pulled, keeping the daily job cheap. Full first-time ingestion
of a Congress happens the first time `refresh()` runs for it (no prior rows).
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

import config

STAGE_RULES = [
    # (keyword substring in action_text, stage label) -- checked in order, first match wins
    ("BECAME PUBLIC LAW", "Signed"),
    ("SIGNED BY PRESIDENT", "Signed"),
    ("VETOED BY PRESIDENT", "Vetoed"),
    ("PRESENTED TO PRESIDENT", "To President"),
    ("PASSED SENATE", "Passed Senate"),
    ("PASSED/AGREED TO IN SENATE", "Passed Senate"),
    ("PASSED HOUSE", "Passed House"),
    ("PASSED/AGREED TO IN HOUSE", "Passed House"),
    ("REPORTED BY", "In Committee"),
    ("REFERRED TO", "In Committee"),
    ("INTRODUCED IN", "Introduced"),
]

STAGE_ORDER = [
    "Introduced", "In Committee", "Passed House", "Passed Senate",
    "To President", "Signed", "Vetoed",
]


def derive_stage(action_text: str) -> str | None:
    if not action_text:
        return None
    upper = action_text.upper()
    for keyword, stage in STAGE_RULES:
        if keyword in upper:
            return stage
    return None


def furthest_stage(stages: list[str]) -> str | None:
    present = [s for s in stages if s in STAGE_ORDER]
    if not present:
        return None
    return max(present, key=STAGE_ORDER.index)


def _get(session: requests.Session, path: str, api_key: str, params: dict | None = None) -> dict:
    params = dict(params or {})
    params["api_key"] = api_key
    params["format"] = "json"
    for attempt in range(8):
        try:
            resp = session.get(f"{config.CONGRESS_API_BASE}{path}", params=params, timeout=30)
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


def list_bills(session, congress: int, api_key: str, from_date: str | None = None):
    """Yields bill summary dicts for a Congress, newest-updated first, paginated."""
    offset = 0
    while True:
        params = {"limit": config.CONGRESS_PAGE_SIZE, "offset": offset, "sort": "updateDate+desc"}
        if from_date:
            params["fromDateTime"] = f"{from_date}T00:00:00Z"
        data = _get(session, f"/bill/{congress}", api_key, params)
        bills = data.get("bills", [])
        if not bills:
            return
        for b in bills:
            yield b
        offset += config.CONGRESS_PAGE_SIZE
        if offset >= data.get("pagination", {}).get("count", 0):
            return


def fetch_bill_detail(session, congress: int, bill_type: str, bill_number: int, api_key: str) -> dict:
    data = _get(session, f"/bill/{congress}/{bill_type}/{bill_number}", api_key)
    return data.get("bill", {})


def fetch_bill_actions(session, congress: int, bill_type: str, bill_number: int, api_key: str) -> list[dict]:
    actions, offset = [], 0
    while True:
        data = _get(
            session, f"/bill/{congress}/{bill_type}/{bill_number}/actions", api_key,
            {"limit": 250, "offset": offset},
        )
        batch = data.get("actions", [])
        actions.extend(batch)
        offset += 250
        if not batch or offset >= data.get("pagination", {}).get("count", 0):
            return actions


def fetch_bill_subjects(session, congress: int, bill_type: str, bill_number: int, api_key: str) -> list[str]:
    data = _get(session, f"/bill/{congress}/{bill_type}/{bill_number}/subjects", api_key)
    subjects = data.get("subjects", {}).get("legislativeSubjects", [])
    return [s["name"] for s in subjects if s.get("name")]


def fetch_bill_cosponsors(session, congress: int, bill_type: str, bill_number: int, api_key: str) -> list[dict]:
    data = _get(session, f"/bill/{congress}/{bill_type}/{bill_number}/cosponsors", api_key)
    return data.get("cosponsors", [])


def upsert_bill(conn, bill_id, congress, bill_type, bill_number, detail, current_stage, now_iso):
    sponsors = detail.get("sponsors") or []
    sponsor_bioguide_id = sponsors[0]["bioguideId"] if sponsors else None
    latest_action = detail.get("latestAction", {})
    conn.execute(
        """
        INSERT INTO bills (bill_id, congress, bill_type, bill_number, title, introduced_date,
                            policy_area, sponsor_bioguide_id, latest_action_date, latest_action_text,
                            current_stage, source_url, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(bill_id) DO UPDATE SET
          title=excluded.title, introduced_date=excluded.introduced_date,
          policy_area=excluded.policy_area, sponsor_bioguide_id=excluded.sponsor_bioguide_id,
          latest_action_date=excluded.latest_action_date, latest_action_text=excluded.latest_action_text,
          current_stage=excluded.current_stage, source_url=excluded.source_url, updated_at=excluded.updated_at
        """,
        (
            bill_id, congress, bill_type, bill_number,
            detail.get("title"), detail.get("introducedDate"),
            (detail.get("policyArea") or {}).get("name"), sponsor_bioguide_id,
            latest_action.get("actionDate"), latest_action.get("text"),
            current_stage, detail.get("url"), now_iso,
        ),
    )


def upsert_actions(conn, bill_id, actions):
    conn.execute("DELETE FROM bill_actions WHERE bill_id = ?", (bill_id,))
    conn.executemany(
        "INSERT INTO bill_actions (bill_id, action_date, action_text, chamber, derived_stage) VALUES (?, ?, ?, ?, ?)",
        [
            (bill_id, a.get("actionDate"), a.get("text"), a.get("chamber"), derive_stage(a.get("text", "")))
            for a in actions
        ],
    )


def upsert_subjects(conn, bill_id, subjects):
    conn.execute("DELETE FROM bill_subjects WHERE bill_id = ?", (bill_id,))
    conn.executemany(
        "INSERT OR IGNORE INTO bill_subjects (bill_id, subject_term) VALUES (?, ?)",
        [(bill_id, s) for s in subjects],
    )


def upsert_cosponsors(conn, bill_id, cosponsors):
    conn.execute("DELETE FROM bill_cosponsors WHERE bill_id = ?", (bill_id,))
    conn.executemany(
        "INSERT OR IGNORE INTO bill_cosponsors (bill_id, bioguide_id, is_original_cosponsor) VALUES (?, ?, ?)",
        [(bill_id, c["bioguideId"], int(bool(c.get("isOriginalCosponsor")))) for c in cosponsors if c.get("bioguideId")],
    )


def last_refreshed_date(conn, congress: int) -> str | None:
    row = conn.execute(
        "SELECT MAX(updated_at) AS m FROM bills WHERE congress = ?", (congress,)
    ).fetchone()
    return row["m"][:10] if row and row["m"] else None


def _ingest_bill(conn, session, congress, api_key, summary, now_iso):
    bill_type = summary["type"].lower()
    bill_number = summary["number"]
    bill_id = f"{congress}-{bill_type}-{bill_number}"

    detail = fetch_bill_detail(session, congress, bill_type, bill_number, api_key)
    actions = fetch_bill_actions(session, congress, bill_type, bill_number, api_key)
    subjects = fetch_bill_subjects(session, congress, bill_type, bill_number, api_key)
    cosponsors = fetch_bill_cosponsors(session, congress, bill_type, bill_number, api_key)

    stages = [derive_stage(a.get("text", "")) for a in actions]
    current_stage = furthest_stage([s for s in stages if s]) or "Introduced"

    upsert_bill(conn, bill_id, congress, bill_type, bill_number, detail, current_stage, now_iso)
    upsert_actions(conn, bill_id, actions)
    upsert_subjects(conn, bill_id, subjects)
    upsert_cosponsors(conn, bill_id, cosponsors)


def refresh(conn, congress: int, api_key: str, limit: int | None = None) -> int:
    """Incremental delta refresh: only bills Congress.gov has updated since
    the last refresh. Cheap, meant for the daily scheduled job. NOT suitable
    for first-time ingestion of a Congress -- use refresh_full() for that."""
    import datetime

    session = requests.Session()
    since = last_refreshed_date(conn, congress)
    now_iso = datetime.datetime.now(datetime.UTC).isoformat()
    count = 0
    for summary in list_bills(session, congress, api_key, from_date=since):
        _ingest_bill(conn, session, congress, api_key, summary, now_iso)
        conn.commit()
        count += 1
        if limit and count >= limit:
            break
    return count


def refresh_full(conn, congress: int, api_key: str) -> int:
    """Resumable full pull of every bill in a Congress, checkpointed by list
    offset in bills_ingest_state -- unlike refresh(), safe to interrupt and
    re-run. Use this once per Congress; after it completes, the cheap
    incremental refresh() keeps it current."""
    import datetime

    session = requests.Session()
    row = conn.execute(
        "SELECT last_offset, complete FROM bills_ingest_state WHERE congress = ?", (congress,)
    ).fetchone()
    if row and row["complete"]:
        return 0
    offset = row["last_offset"] if row else 0

    count = 0
    while True:
        params = {"limit": config.CONGRESS_PAGE_SIZE, "offset": offset, "sort": "updateDate+desc"}
        data = _get(session, f"/bill/{congress}", api_key, params)
        bills = data.get("bills", [])
        total = data.get("pagination", {}).get("count", 0)

        now_iso = datetime.datetime.now(datetime.UTC).isoformat()
        for summary in bills:
            _ingest_bill(conn, session, congress, api_key, summary, now_iso)
            count += 1

        offset += config.CONGRESS_PAGE_SIZE
        done = not bills or offset >= total
        conn.execute(
            """
            INSERT INTO bills_ingest_state (congress, last_offset, complete, last_run_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(congress) DO UPDATE SET
              last_offset=excluded.last_offset, complete=excluded.complete, last_run_at=excluded.last_run_at
            """,
            (congress, offset, int(done), now_iso),
        )
        conn.commit()
        if done:
            return count


if __name__ == "__main__":
    import argparse
    import os

    import db
    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--congress", type=int, default=config.current_congress())
    parser.add_argument("--limit", type=int, default=None, help="cap bills pulled, for testing")
    parser.add_argument("--full", action="store_true", help="resumable full backfill, not just the incremental delta")
    args = parser.parse_args()

    api_key = os.environ["CONGRESS_API_KEY"]
    connection = db.connect()
    db.init_main_db(connection)
    if args.full:
        n = refresh_full(connection, args.congress, api_key)
        print(f"Full backfill: ingested {n} bills for Congress {args.congress}")
    else:
        n = refresh(connection, args.congress, api_key, limit=args.limit)
        print(f"Refreshed {n} bills for Congress {args.congress}")
