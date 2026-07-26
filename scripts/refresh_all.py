"""Local/manual full refresh: same two entry points as the scheduled jobs,
run back to back. Use this for first-time local setup."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import refresh_bills
import refresh_fec

if __name__ == "__main__":
    refresh_bills.main()
    refresh_fec.main()
