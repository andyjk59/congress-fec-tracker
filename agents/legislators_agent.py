"""Pulls the unitedstates/congress-legislators crosswalk (public, no API key)
and upserts bioguide_id <-> FEC candidate_id mappings into `legislators`.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
import yaml

import config


def fetch_legislators_yaml() -> list[dict]:
    resp = requests.get(config.LEGISLATORS_YAML_URL, timeout=30)
    resp.raise_for_status()
    return yaml.safe_load(resp.text)


def _current_term(person: dict) -> dict | None:
    terms = person.get("terms") or []
    return terms[-1] if terms else None


def _full_name(person: dict) -> str:
    name = person.get("name", {})
    if name.get("official_full"):
        return name["official_full"]
    return f"{name.get('first', '')} {name.get('last', '')}".strip()


def upsert_legislators(conn, people: list[dict]) -> int:
    rows = []
    for person in people:
        bioguide_id = person.get("id", {}).get("bioguide")
        if not bioguide_id:
            continue
        term = _current_term(person)
        chamber = {"rep": "house", "sen": "senate"}.get((term or {}).get("type"), None)
        fec_ids = person.get("id", {}).get("fec") or []
        rows.append((
            bioguide_id,
            _full_name(person),
            (term or {}).get("party"),
            (term or {}).get("state"),
            chamber,
            json.dumps(fec_ids),
        ))

    conn.executemany(
        """
        INSERT INTO legislators (bioguide_id, full_name, party, state, chamber, fec_candidate_ids)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(bioguide_id) DO UPDATE SET
          full_name=excluded.full_name, party=excluded.party, state=excluded.state,
          chamber=excluded.chamber, fec_candidate_ids=excluded.fec_candidate_ids
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def refresh(conn) -> int:
    people = fetch_legislators_yaml()
    return upsert_legislators(conn, people)


if __name__ == "__main__":
    import db

    connection = db.connect()
    db.init_main_db(connection)
    count = refresh(connection)
    print(f"Upserted {count} legislators")
