"""
Fetch Booking.com pages.

Supports multiple backends:
- httpx: direct HTTP (fast, but Booking.com blocks it with CAPTCHA)
- firecrawl: uses Firecrawl API (requires FIRECRAWL_API_KEY env var)
"""

import os
from urllib.parse import urlparse

import httpx

from config import USER_AGENT, REQUEST_TIMEOUT, MAX_RETRIES

_BOOKING_HOST = "booking.com"


def _validate_booking_fetch_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise RuntimeError(f"Fetch only supports http(s) URLs, got {url!r}")
    host = (parsed.hostname or "").lower()
    if not host or not (
        host == _BOOKING_HOST or host.endswith(f".{_BOOKING_HOST}")
    ):
        raise RuntimeError(f"Fetch restricted to Booking.com hosts: {url!r}")


async def fetch_httpx(url: str) -> str:
    """Direct HTTP fetch with httpx. Works for sites without anti-bot."""
    _validate_booking_fetch_url(url)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.google.com/",
    }
    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(
                timeout=REQUEST_TIMEOUT,
                follow_redirects=True,
            ) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                return resp.text
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            last_error = e
            if attempt < MAX_RETRIES:
                continue
            raise RuntimeError(
                f"Fetch failed after {MAX_RETRIES + 1} attempts: {last_error}"
            ) from last_error


def fetch_firecrawl(url: str) -> str:
    """
    Fetch via Firecrawl API (synchronous).
    Returns markdown content of the page.
    Requires FIRECRAWL_API_KEY env var.
    """
    _validate_booking_fetch_url(url)
    api_key = os.environ.get("FIRECRAWL_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "FIRECRAWL_API_KEY not set. "
            "Export it or add it to .env"
        )

    api_url = "https://api.firecrawl.dev/v1/scrape"
    payload = {
        "url": url,
        "formats": ["markdown"],
        "waitFor": 5000,
    }

    resp = httpx.post(
        api_url,
        json=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()

    if not data.get("success"):
        raise RuntimeError(f"Firecrawl error: {data}")

    inner = data.get("data") or {}
    markdown = inner.get("markdown") if isinstance(inner, dict) else None
    if not markdown:
        snippet = str(data)[:800]
        raise RuntimeError(
            "Firecrawl response missing data.markdown; payload snippet: "
            f"{snippet!r}"
        )
    return markdown


async def fetch_page(url: str, backend: str = "auto") -> tuple[str, str]:
    """
    Fetch a page using the specified backend.

    Returns (content, backend_used) where backend_used is "httpx" or "firecrawl".

    backends:
        - "auto": try firecrawl if key exists, else httpx
        - "httpx": direct HTTP
        - "firecrawl": Firecrawl API
    """
    if backend == "auto":
        has_key = bool(os.environ.get("FIRECRAWL_API_KEY", "").strip())
        backend = "firecrawl" if has_key else "httpx"

    if backend == "firecrawl":
        return fetch_firecrawl(url), "firecrawl"
    else:
        content = await fetch_httpx(url)
        return content, "httpx"
