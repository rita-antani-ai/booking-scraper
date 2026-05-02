"""
Storage: index.json management and result file I/O.
"""

import json
import os
import tempfile

from config import RESULTS_DIR, INDEX_FILE
from models import ScrapeResult


def load_index() -> dict:
    """Load the index file, return empty dict if missing."""
    if INDEX_FILE.exists():
        return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    return {}


def save_index(index: dict):
    """Write the index file atomically (temp + replace)."""
    INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(index, indent=2, ensure_ascii=False)
    fd, tmp_path = tempfile.mkstemp(
        dir=INDEX_FILE.parent,
        prefix=".index.",
        suffix=".json.tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp_path, INDEX_FILE)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def is_visited(url_hash: str) -> dict | None:
    """
    Check if a URL hash exists in the index.
    Returns the index entry if found, None otherwise.
    """
    index = load_index()
    return index.get(url_hash)


def save_result(result: ScrapeResult, raw_content: str, page_suffix: str = "html") -> str:
    """
    Save raw page (HTML or markdown) and JSON for a scrape result.
    page_suffix: "html" for httpx HTML, "md" for Firecrawl markdown.
    Returns the path to the JSON file.
    """
    result_dir = RESULTS_DIR / result.url_hash
    result_dir.mkdir(parents=True, exist_ok=True)

    if page_suffix == "md":
        raw_path = result_dir / "page.md"
    else:
        raw_path = result_dir / "page.html"

    raw_path.write_text(raw_content, encoding="utf-8")
    result.html_file = str(raw_path)

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
        "html_file": str(raw_path),
        "json_file": str(json_path),
    }
    save_index(index)

    return str(json_path)


def load_stored_page(url_hash: str) -> tuple[str, str] | None:
    """
    Load saved raw page for re-parsing.
    Returns (content, page_suffix) with page_suffix "html" or "md", or None.
    """
    result_dir = RESULTS_DIR / url_hash
    for name, suffix in (("page.html", "html"), ("page.md", "md")):
        path = result_dir / name
        if path.exists():
            return path.read_text(encoding="utf-8"), suffix
    return None
