-- Data-pairing layer: every cross-table linking the app needs is expressed
-- here as a named view, not as ad hoc joins in Python. app.py only ever
-- does `SELECT ... FROM v_...` against these.

DROP VIEW IF EXISTS v_active_bills_overview;
CREATE VIEW v_active_bills_overview AS
SELECT
  b.bill_id, b.congress, b.bill_type, b.bill_number, b.title, b.introduced_date,
  b.policy_area, b.current_stage, b.latest_action_date, b.latest_action_text, b.source_url,
  b.sponsor_bioguide_id, l.full_name AS sponsor_name, l.party AS sponsor_party, l.state AS sponsor_state
FROM bills b
LEFT JOIN legislators l ON b.sponsor_bioguide_id = l.bioguide_id;

-- Direct link: bill -> sponsor -> FEC candidate -> principal committee -> retention.
DROP VIEW IF EXISTS v_bill_sponsor_retention;
CREATE VIEW v_bill_sponsor_retention AS
SELECT
  b.bill_id, b.title, b.congress, b.current_stage,
  b.sponsor_bioguide_id, l.full_name AS sponsor_name, l.party AS sponsor_party, l.state AS sponsor_state,
  fcand.candidate_id, fc.committee_id, fc.name AS committee_name,
  dr.prior_cycle, dr.current_cycle, dr.prior_cycle_donor_count, dr.retained_donor_count,
  dr.retention_pct, dr.prior_cycle_total_receipts, dr.current_cycle_total_receipts
FROM bills b
JOIN legislators l ON b.sponsor_bioguide_id = l.bioguide_id
JOIN fec_candidates fcand ON fcand.bioguide_id = l.bioguide_id
JOIN fec_committees fc ON fc.candidate_id = fcand.candidate_id AND fc.designation = 'P'
JOIN donor_retention dr ON dr.committee_id = fc.committee_id;

-- Heuristic link: bill's own policy_area matched directly against
-- fec_committee_industry_signal.industry_label (industry_keywords.py's keys
-- are the same Congress.gov policy-area strings, so this is a direct join,
-- not fuzzy text matching -- the fuzziness lives upstream, in how
-- industry_link_agent.py assigned those labels from keyword hits).
DROP VIEW IF EXISTS v_bill_industry_signal;
CREATE VIEW v_bill_industry_signal AS
SELECT
  b.bill_id, b.title, b.policy_area,
  b.sponsor_bioguide_id, l.full_name AS sponsor_name,
  fc.committee_id, fc.name AS committee_name,
  sig.cycle, sig.industry_label, sig.matched_amount, sig.matched_contribution_count
FROM bills b
JOIN legislators l ON b.sponsor_bioguide_id = l.bioguide_id
JOIN fec_candidates fcand ON fcand.bioguide_id = l.bioguide_id
JOIN fec_committees fc ON fc.candidate_id = fcand.candidate_id AND fc.designation = 'P'
JOIN fec_committee_industry_signal sig
  ON sig.committee_id = fc.committee_id AND sig.industry_label = b.policy_area;

-- Reverse link: donating organization/PAC -> committees it gave to -> the
-- sponsor that committee funds -> bills that sponsor introduced. This is
-- "which bills has this donating body supported," expressed as a single
-- join chain rather than looked up piecemeal in Python.
DROP VIEW IF EXISTS v_donor_bills;
CREATE VIEW v_donor_bills AS
SELECT
  od.donor_name, od.cycle, od.total_amount, od.contribution_count,
  fc.committee_id, fc.name AS committee_name,
  l.bioguide_id AS sponsor_bioguide_id, l.full_name AS sponsor_name, l.party AS sponsor_party, l.state AS sponsor_state,
  b.bill_id, b.title, b.current_stage, b.policy_area, b.introduced_date
FROM fec_committee_org_donors od
JOIN fec_committees fc ON fc.committee_id = od.committee_id
JOIN fec_candidates fcand ON fcand.candidate_id = fc.candidate_id
JOIN legislators l ON l.bioguide_id = fcand.bioguide_id
JOIN bills b ON b.sponsor_bioguide_id = l.bioguide_id;
