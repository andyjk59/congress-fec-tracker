-- Schema for the gitignored .cache/fec_raw.db, restored between CI runs via actions/cache.
-- Never committed to git: this is the only place donor-level fingerprints are stored.
CREATE TABLE IF NOT EXISTS donor_fingerprints (
  committee_id TEXT NOT NULL,
  cycle INTEGER NOT NULL,
  fingerprint_hash TEXT NOT NULL,
  employer TEXT,
  occupation TEXT,
  entity_type TEXT,       -- 'IND' individual or 'PAC'/committee-attributed contribution
  total_amount REAL,
  contribution_count INTEGER,
  PRIMARY KEY (committee_id, cycle, fingerprint_hash)
);

CREATE INDEX IF NOT EXISTS idx_fingerprints_committee_cycle
  ON donor_fingerprints(committee_id, cycle);
