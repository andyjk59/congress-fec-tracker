"""Weekly refresh entry point (also used for the one-time chunked backfill via
`--chunk-size`): FEC candidates/committees, Schedule A ingestion, then
recompute retention + industry signals from whatever's newly complete."""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

import config
import db
from agents import fec_candidates_agent, fec_contributions_agent, industry_link_agent, retention_agent

load_dotenv()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--chunk-size", type=int, default=50)
    args = parser.parse_args()

    api_key = os.environ["FEC_API_KEY"]
    main_conn = db.connect()
    db.init_main_db(main_conn)
    cache_conn = db.connect(config.CACHE_DB_PATH)
    db.init_cache_db(cache_conn)

    n_candidates = fec_candidates_agent.refresh(main_conn, api_key)
    print(f"Refreshed {n_candidates} candidates/committees")

    n_ingested = fec_contributions_agent.refresh(main_conn, cache_conn, api_key, chunk_size=args.chunk_size)
    print(f"Ingested {n_ingested} committee/cycle pairs")

    n_retention = retention_agent.refresh(main_conn, cache_conn)
    print(f"Computed retention for {n_retention} committee/cycle-pairs")

    n_industry = industry_link_agent.refresh(main_conn, cache_conn)
    print(f"Computed {n_industry} industry signal rows")


if __name__ == "__main__":
    main()
