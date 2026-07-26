import datetime
from pathlib import Path

ROOT = Path(__file__).parent
DB_PATH = ROOT / "data" / "tracker.db"
CACHE_DB_PATH = ROOT / ".cache" / "fec_raw.db"
SCHEMA_SQL_PATH = ROOT / "sql" / "schema.sql"
VIEWS_SQL_PATH = ROOT / "sql" / "views.sql"
LEGISLATORS_YAML_URL = (
    "https://raw.githubusercontent.com/unitedstates/congress-legislators/main/legislators-current.yaml"
)

CONGRESS_API_BASE = "https://api.congress.gov/v3"
FEC_API_BASE = "https://api.open.fec.gov/v1"

# OpenFEC free-tier limits: 1000 req/hour, 120 req/min per key.
FEC_RATE_LIMIT_PER_MINUTE = 100  # stay comfortably under 120/min
FEC_PAGE_SIZE = 100
FEC_MAX_ROWS_PER_COMMITTEE_CYCLE = 20_000  # sampled cap, see README limitations

CONGRESS_PAGE_SIZE = 250


def congress_for_year(year: int) -> int:
    """Congress N started 1789 + (N-1)*2; both years of a Congress map to the same N."""
    return (year - 1789) // 2 + 1


def cycle_for_year(year: int) -> int:
    """FEC 2-year cycles are labeled by their ending even year."""
    return year if year % 2 == 0 else year + 1


def current_congress() -> int:
    return congress_for_year(datetime.date.today().year)


def current_cycle() -> int:
    return cycle_for_year(datetime.date.today().year)
