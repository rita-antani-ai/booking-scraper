"""
URL normalization and hashing for deduplication.
"""

import hashlib
from urllib.parse import urlparse, parse_qs, urlencode

from config import KEEP_PARAMS


def normalize_url(url: str) -> str:
    """
    Strip tracking params from a Booking.com URL, keep only
    search-defining params, sort them, return a canonical URL.
    """
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)

    # Keep only search-relevant params
    filtered = {
        k: v for k, v in params.items()
        if k.lower() in KEEP_PARAMS
    }

    # Sort for determinism
    sorted_query = urlencode(filtered, doseq=True)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{sorted_query}"


def hash_url(normalized_url: str) -> str:
    """SHA256 of the normalized URL, truncated to 12 hex chars."""
    return hashlib.sha256(normalized_url.encode()).hexdigest()[:12]


def extract_search_params(url: str) -> dict:
    """Extract human-readable search parameters from a Booking URL."""
    params = parse_qs(urlparse(url).query)
    return {
        "checkin": params.get("checkin", [""])[0],
        "checkout": params.get("checkout", [""])[0],
        "dest_id": params.get("dest_id", [""])[0],
        "dest_type": params.get("dest_type", [""])[0],
        "adults": int(params.get("group_adults", ["0"])[0]),
        "children": int(params.get("group_children", ["0"])[0]),
    }
