"""
URL normalization and hashing for deduplication.
"""

import hashlib
import re
from html import unescape
from urllib.parse import urlparse, parse_qs, urlencode, unquote_plus

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
    base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    return f"{base}?{sorted_query}" if sorted_query else base


def hash_url(normalized_url: str) -> str:
    """SHA256 of the normalized URL, truncated to 12 hex chars."""
    return hashlib.sha256(normalized_url.encode()).hexdigest()[:12]


def _safe_int(s: str, default: int = 0) -> int:
    try:
        return int(s)
    except (TypeError, ValueError):
        return default


def extract_search_params(url: str) -> dict:
    """Extract human-readable search parameters from a Booking URL."""
    params = parse_qs(urlparse(url).query)
    return {
        "checkin": params.get("checkin", [""])[0],
        "checkout": params.get("checkout", [""])[0],
        "dest_id": params.get("dest_id", [""])[0],
        "dest_type": params.get("dest_type", [""])[0],
        "adults": _safe_int(params.get("group_adults", ["0"])[0]),
        "children": _safe_int(params.get("group_children", ["0"])[0]),
    }


def extract_dest_label(url: str, page_text: str = "") -> str:
    """
    Human-readable destination (city/region name), not the opaque dest_id.
    Prefer URL hints (ss=, path), then page title / headings.
    """
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)

    for key in ("ss", "highlighted_hotels"):
        vals = qs.get(key, [])
        if vals and vals[0].strip():
            return unquote_plus(vals[0].strip()).replace("+", " ")

    # Path segment: .../city/name-it.html
    path_parts = [p for p in parsed.path.split("/") if p]
    if path_parts:
        seg = path_parts[-1]
        if seg.endswith(".html"):
            seg = seg[:-5]
        if seg and "searchresults" in seg.lower():
            seg = ""
        if seg and seg not in ("searchresults", "hotel"):
            slug = re.sub(r"\.[a-z]{2}-[a-z]{2}$", "", seg, flags=re.IGNORECASE)
            if slug and not slug.isdigit():
                return slug.replace("-", " ").replace("_", " ").title()

    text = page_text if page_text else ""
    if text:
        tmatch = re.search(
            r"<title[^>]*>([^<]{1,200})</title>",
            text,
            re.IGNORECASE | re.DOTALL,
        )
        if tmatch:
            raw = unescape(re.sub(r"\s+", " ", tmatch.group(1)).strip())
            m = re.search(
                r"(?:Hotels in|Hotel a|Hotels à)\s*(.+?)(?:\s*[\.|\-|–|:]|$)",
                raw,
                re.IGNORECASE,
            )
            if m:
                return m.group(1).strip()
            m2 = re.search(
                r"^(.+?):\s*\d[\d,\s]*\s*(?:properties|propriet)",
                raw,
                re.IGNORECASE,
            )
            if m2:
                return m2.group(1).strip()

        for pat in (
            r"^#\s+(.+?)(?:\s*\|\s*)?$",
            r"^##\s+(.+?)(?:\s*\|\s*)?$",
        ):
            mline = re.search(pat, text, re.MULTILINE)
            if mline:
                line = mline.group(1).strip()
                line = re.sub(r"\s*:\s*\d+.*$", "", line)
                if line and "booking.com" not in line.lower():
                    return line

    return ""
