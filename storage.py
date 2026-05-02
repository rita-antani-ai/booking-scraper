"""
Storage: index.json management and result file I/O.
"""

import json
import os
import tempfile
from pathlib import Path

from config import RESULTS_DIR, INDEX_FILE, PROJECT_ROOT
from models import ScrapeResult


def load_index() -> dict:
    """Load the index file, return empty dict if missing or invalid."""
    if not INDEX_FILE.exists():
        return {}
    try:
        data = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


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


def resolve_stored_path(stored: str) -> Path:
    """Resolve a path from the index (relative to PROJECT_ROOT or legacy absolute)."""
    p = Path(stored)
    if p.is_absolute():
        return p
    return PROJECT_ROOT / p


def get_index_entry(url_hash: str) -> dict | None:
    """
    Return the index entry for a URL hash if present, else None.
    """
    index = load_index()
    entry = index.get(url_hash)
    return entry if isinstance(entry, dict) else None


def is_visited(url_hash: str) -> bool:
    """True if this URL hash exists in the index."""
    return get_index_entry(url_hash) is not None


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
        "html_file": str(raw_path.relative_to(PROJECT_ROOT)),
        "json_file": str(json_path.relative_to(PROJECT_ROOT)),
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
