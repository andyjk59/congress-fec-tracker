"""Heuristic industry-signal computation: keyword-matches cached contributor
employer/occupation/PAC-name text against industry_keywords.py's policy-area
table, using the SAME cached rows already pulled for retention -- no extra
API calls. Writes aggregate-only rows to fec_committee_industry_signal.

This is directional, not an authoritative CRP/OpenSecrets industry
classification -- see README known limitations.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import db
from industry_keywords import POLICY_AREA_KEYWORDS

MATCH_SQL_TEMPLATE = """
SELECT COUNT(*) AS cnt, COALESCE(SUM(total_amount), 0) AS amt
FROM donor_fingerprints
WHERE committee_id = ? AND cycle = ?
  AND (UPPER(COALESCE(employer, '') || ' ' || COALESCE(occupation, '')) LIKE ? {extra})
"""


def _committee_cycles(main_conn) -> list[tuple[str, int]]:
    rows = main_conn.execute(
        "SELECT DISTINCT committee_id, cycle FROM fec_ingest_state WHERE complete = 1"
    ).fetchall()
    return [(r["committee_id"], r["cycle"]) for r in rows]


def compute_signals(cache_conn, committee_id: str, cycle: int) -> dict[str, tuple[float, int]]:
    signals = {}
    for label, keywords in POLICY_AREA_KEYWORDS.items():
        if not keywords:
            continue
        extra = " OR " + " OR ".join(
            ["UPPER(COALESCE(employer, '') || ' ' || COALESCE(occupation, '')) LIKE ?"] * (len(keywords) - 1)
        ) if len(keywords) > 1 else ""
        sql = MATCH_SQL_TEMPLATE.format(extra=extra)
        params = [committee_id, cycle] + [f"%{kw}%" for kw in keywords]
        row = cache_conn.execute(sql, params).fetchone()
        if row["cnt"] > 0:
            signals[label] = (row["amt"], row["cnt"])
    return signals


def upsert_signal(main_conn, committee_id, cycle, label, amount, count):
    main_conn.execute(
        """
        INSERT INTO fec_committee_industry_signal (committee_id, cycle, industry_label,
                                                     matched_amount, matched_contribution_count)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(committee_id, cycle, industry_label) DO UPDATE SET
          matched_amount=excluded.matched_amount, matched_contribution_count=excluded.matched_contribution_count
        """,
        (committee_id, cycle, label, amount, count),
    )


def refresh(main_conn, cache_conn) -> int:
    count = 0
    for committee_id, cycle in _committee_cycles(main_conn):
        for label, (amount, matched_count) in compute_signals(cache_conn, committee_id, cycle).items():
            upsert_signal(main_conn, committee_id, cycle, label, amount, matched_count)
            count += 1
    main_conn.commit()
    return count


if __name__ == "__main__":
    main_connection = db.connect()
    db.init_main_db(main_connection)
    cache_connection = db.connect(config.CACHE_DB_PATH)
    db.init_cache_db(cache_connection)

    n = refresh(main_connection, cache_connection)
    print(f"Computed {n} industry signal rows")
