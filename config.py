"""
Configuration for the Booking.com scraper.
"""

from pathlib import Path

from dotenv import load_dotenv

# Paths
PROJECT_ROOT = Path(__file__).parent
OUTPUT_DIR = PROJECT_ROOT / "output"
RESULTS_DIR = OUTPUT_DIR / "results"
INDEX_FILE = OUTPUT_DIR / "index.json"

# HTTP
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 30
MAX_RETRIES = 2

# URL normalization: only keep these params for deduplication
# (tracking params like label, sid, aid are stripped)
KEEP_PARAMS = {
    "checkin", "checkout", "dest_id", "dest_type",
    "group_adults", "req_adults", "no_rooms",
    "group_children", "req_children", "age", "req_age",
    "flex_window", "nflt", "broad_search_not_eligible",
}


def init_storage() -> None:
    """Load environment and ensure output directories exist. Call once from main()."""
    load_dotenv()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
