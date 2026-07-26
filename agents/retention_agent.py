"""Computes 'Itemized Donor Retention' per committee/cycle-pair from the
cached donor_fingerprints table, writing only the aggregate result to
data/tracker.db's donor_retention table -- individual fingerprints never
leave the gitignored cache DB.

The actual donor-pairing across cycles is a plain SQL self-join (the
explicit SQL data-pairing step): match fingerprint_hash between a
committee's prior-cycle rows and its current-cycle rows.
"""

import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import db

RETAINED_DONORS_SQL = """
SELECT COUNT(*) AS retained
FROM donor_fingerprints a
JOIN donor_fingerprints b
  ON a.fingerprint_hash = b.fingerprint_hash AND a.committee_id = b.committee_id
WHERE a.committee_id = ? AND a.cycle = ? AND b.cycle = ?
"""

CYCLE_TOTALS_SQL = """
SELECT COUNT(*) AS donor_count, COALESCE(SUM(total_amount), 0) AS total_receipts
FROM donor_fingerprints
WHERE committee_id = ? AND cycle = ?
"""


def committee_cycle_pairs(main_conn) -> list[tuple[str, int, int]]:
    """(committee_id, prior_cycle, current_cycle) for every pair of consecutive
    complete-ingested cycles."""
    rows = main_conn.execute(
        "SELECT DISTINCT committee_id, cycle FROM fec_ingest_state WHERE complete = 1"
    ).fetchall()
    by_committee: dict[str, set[int]] = {}
    for r in rows:
        by_committee.setdefault(r["committee_id"], set()).add(r["cycle"])

    pairs = []
    for committee_id, cycles in by_committee.items():
        for cycle in cycles:
            if (cycle - 2) in cycles:
                pairs.append((committee_id, cycle - 2, cycle))
    return pairs


def compute_pair(cache_conn, committee_id: str, prior_cycle: int, current_cycle: int) -> dict:
    prior = cache_conn.execute(CYCLE_TOTALS_SQL, (committee_id, prior_cycle)).fetchone()
    current = cache_conn.execute(CYCLE_TOTALS_SQL, (committee_id, current_cycle)).fetchone()
    retained = cache_conn.execute(RETAINED_DONORS_SQL, (committee_id, prior_cycle, current_cycle)).fetchone()

    prior_count = prior["donor_count"]
    retention_pct = (retained["retained"] / prior_count * 100) if prior_count else None
    return {
        "prior_cycle_donor_count": prior_count,
        "retained_donor_count": retained["retained"],
        "retention_pct": retention_pct,
        "prior_cycle_total_receipts": prior["total_receipts"],
        "current_cycle_total_receipts": current["total_receipts"],
    }


def upsert_retention(main_conn, committee_id, prior_cycle, current_cycle, metrics: dict):
    main_conn.execute(
        """
        INSERT INTO donor_retention (committee_id, prior_cycle, current_cycle, prior_cycle_donor_count,
                                      retained_donor_count, retention_pct, prior_cycle_total_receipts,
                                      current_cycle_total_receipts, computed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(committee_id, prior_cycle, current_cycle) DO UPDATE SET
          prior_cycle_donor_count=excluded.prior_cycle_donor_count,
          retained_donor_count=excluded.retained_donor_count,
          retention_pct=excluded.retention_pct,
          prior_cycle_total_receipts=excluded.prior_cycle_total_receipts,
          current_cycle_total_receipts=excluded.current_cycle_total_receipts,
          computed_at=excluded.computed_at
        """,
        (
            committee_id, prior_cycle, current_cycle,
            metrics["prior_cycle_donor_count"], metrics["retained_donor_count"], metrics["retention_pct"],
            metrics["prior_cycle_total_receipts"], metrics["current_cycle_total_receipts"],
            datetime.datetime.now(datetime.UTC).isoformat(),
        ),
    )


def refresh(main_conn, cache_conn) -> int:
    count = 0
    for committee_id, prior_cycle, current_cycle in committee_cycle_pairs(main_conn):
        metrics = compute_pair(cache_conn, committee_id, prior_cycle, current_cycle)
        upsert_retention(main_conn, committee_id, prior_cycle, current_cycle, metrics)
        count += 1
    main_conn.commit()
    return count


if __name__ == "__main__":
    main_connection = db.connect()
    db.init_main_db(main_connection)
    cache_connection = db.connect(config.CACHE_DB_PATH)
    db.init_cache_db(cache_connection)

    n = refresh(main_connection, cache_connection)
    print(f"Computed retention for {n} committee/cycle-pairs")
