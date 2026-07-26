"""Daily refresh entry point: legislators crosswalk + current-Congress bill delta."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

import config
import db
from agents import bills_agent, legislators_agent

load_dotenv()


def main():
    api_key = os.environ["CONGRESS_API_KEY"]
    conn = db.connect()
    db.init_main_db(conn)

    n_legislators = legislators_agent.refresh(conn)
    print(f"Upserted {n_legislators} legislators")

    n_bills = bills_agent.refresh(conn, config.current_congress(), api_key)
    print(f"Refreshed {n_bills} bills for Congress {config.current_congress()}")


if __name__ == "__main__":
    main()
