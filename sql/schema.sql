CREATE TABLE IF NOT EXISTS bills (
  bill_id TEXT PRIMARY KEY,
  congress INTEGER NOT NULL,
  bill_type TEXT NOT NULL,
  bill_number INTEGER NOT NULL,
  title TEXT,
  introduced_date TEXT,
  policy_area TEXT,
  sponsor_bioguide_id TEXT,
  latest_action_date TEXT,
  latest_action_text TEXT,
  current_stage TEXT,
  source_url TEXT,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS bill_actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bill_id TEXT NOT NULL REFERENCES bills(bill_id),
  action_date TEXT,
  action_text TEXT,
  chamber TEXT,
  derived_stage TEXT
);

CREATE TABLE IF NOT EXISTS bill_subjects (
  bill_id TEXT NOT NULL REFERENCES bills(bill_id),
  subject_term TEXT NOT NULL,
  PRIMARY KEY (bill_id, subject_term)
);

CREATE TABLE IF NOT EXISTS bill_cosponsors (
  bill_id TEXT NOT NULL REFERENCES bills(bill_id),
  bioguide_id TEXT NOT NULL,
  is_original_cosponsor INTEGER,
  PRIMARY KEY (bill_id, bioguide_id)
);

CREATE TABLE IF NOT EXISTS legislators (
  bioguide_id TEXT PRIMARY KEY,
  full_name TEXT,
  party TEXT,
  state TEXT,
  chamber TEXT,
  fec_candidate_ids TEXT  -- JSON array, a legislator can have multiple historical FEC IDs
);

CREATE TABLE IF NOT EXISTS fec_candidates (
  candidate_id TEXT PRIMARY KEY,
  bioguide_id TEXT REFERENCES legislators(bioguide_id),
  name TEXT,
  party TEXT,
  office TEXT,
  state TEXT,
  district TEXT
);

CREATE TABLE IF NOT EXISTS fec_committees (
  committee_id TEXT PRIMARY KEY,
  candidate_id TEXT REFERENCES fec_candidates(candidate_id),
  name TEXT,
  designation TEXT  -- 'P' = principal campaign committee, the only ones in scope
);

CREATE TABLE IF NOT EXISTS bills_ingest_state (
  congress INTEGER PRIMARY KEY,
  last_offset INTEGER DEFAULT 0,
  complete INTEGER DEFAULT 0,
  last_run_at TEXT
);

CREATE TABLE IF NOT EXISTS fec_ingest_state (
  committee_id TEXT NOT NULL,
  cycle INTEGER NOT NULL,
  last_index TEXT,
  last_contribution_receipt_date TEXT,
  complete INTEGER DEFAULT 0,
  rows_pulled INTEGER DEFAULT 0,
  last_run_at TEXT,
  PRIMARY KEY (committee_id, cycle)
);

CREATE TABLE IF NOT EXISTS donor_retention (
  committee_id TEXT NOT NULL REFERENCES fec_committees(committee_id),
  prior_cycle INTEGER NOT NULL,
  current_cycle INTEGER NOT NULL,
  prior_cycle_donor_count INTEGER,
  retained_donor_count INTEGER,
  retention_pct REAL,
  prior_cycle_total_receipts REAL,
  current_cycle_total_receipts REAL,
  computed_at TEXT,
  PRIMARY KEY (committee_id, prior_cycle, current_cycle)
);

CREATE TABLE IF NOT EXISTS fec_committee_industry_signal (
  committee_id TEXT NOT NULL REFERENCES fec_committees(committee_id),
  cycle INTEGER NOT NULL,
  industry_label TEXT NOT NULL,
  matched_amount REAL,
  matched_contribution_count INTEGER,
  PRIMARY KEY (committee_id, cycle, industry_label)
);

CREATE INDEX IF NOT EXISTS idx_bills_congress_stage ON bills(congress, current_stage);
CREATE INDEX IF NOT EXISTS idx_bills_sponsor ON bills(sponsor_bioguide_id);
CREATE INDEX IF NOT EXISTS idx_actions_bill_id ON bill_actions(bill_id);
CREATE INDEX IF NOT EXISTS idx_fec_candidates_bioguide ON fec_candidates(bioguide_id);
CREATE INDEX IF NOT EXISTS idx_fec_committees_candidate ON fec_committees(candidate_id);
