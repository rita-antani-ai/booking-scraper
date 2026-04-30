"""
Storage: index.json management and result file I/O.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from config import OUTPUT_DIR, RESULTS_DIR, INDEX_FILE
from models import ScrapeResult, Hotel


def load_index() -> dict:
    """Load the index file, return empty dict if missing."""
    if INDEX_FILE.exists():
        return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    return {}


def save_index(index: dict):
    """Write the index file."""
    INDEX_FILE.write_text(
        json.dumps(index, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def is_visited(url_hash: str) -> dict | None:
    """
    Check if a URL hash exists in the index.
    Returns the index entry if found, None otherwise.
    """
    index = load_index()
    return index.get(url_hash)


def save_result(result: ScrapeResult, html_content: str) -> str:
    """
    Save HTML and JSON for a scrape result.
    Returns the path to the JSON file.
    """
    result_dir = RESULTS_DIR / result.url_hash
    result_dir.mkdir(parents=True, exist_ok=True)

    # Save raw HTML
    html_path = result_dir / "page.html"
    html_path.write_text(html_content, encoding="utf-8")
    result.html_file = str(html_path)

    # Save structured JSON
    json_path = result_dir / "hotels.json"
    json_path.write_text(
        result.model_dump_json(indent=2),
        encoding="utf-8",
    )
    result.json_file = str(json_path)

    # Update index
    index = load_index()
    index[result.url_hash] = {
        "url": result.url,
        "url_normalized": result.url_normalized,
        "scraped_at": result.scraped_at,
        "dest_label": result.dest_label,
        "checkin": result.checkin,
        "checkout": result.checkout,
        "adults": result.adults,
        "children": result.children,
        "n_hotels": result.n_hotels,
        "html_file": str(html_path),
        "json_file": str(json_path),
    }
    save_index(index)

    return str(json_path)


def load_result_from_html(url_hash: str) -> str | None:
    """Load raw HTML for re-parsing. Returns None if not found."""
    html_path = RESULTS_DIR / url_hash / "page.html"
    if html_path.exists():
        return html_path.read_text(encoding="utf-8")
    return None
