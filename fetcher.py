"""
Fetch Booking.com pages.

Supports multiple backends:
- httpx: direct HTTP (fast, but Booking.com blocks it with CAPTCHA)
- firecrawl: uses Firecrawl API (requires FIRECRAWL_API_KEY env var)
- graphql: GET search URL (cookies), extract FullSearch POST template from HTML,
  paginate via /dml/graphql (curl_cffi TLS impersonation)
"""

from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx

from config import (
    USER_AGENT,
    REQUEST_TIMEOUT,
    MAX_RETRIES,
    BOOKING_GRAPHQL_ENDPOINT,
    BOOKING_GRAPHQL_IMPERSONATE,
    BOOKING_GRAPHQL_PAYLOAD_PATH_ENV,
    GRAPHQL_ENVELOPE_FORMAT,
)

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


def _json_object_slice(s: str, open_brace_idx: int) -> str | None:
    """Return substring for balanced `{...}` starting at open_brace_idx, respecting JSON strings."""
    depth = 0
    i = open_brace_idx
    n = len(s)
    in_str = False
    escape = False
    while i < n:
        ch = s[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[open_brace_idx : i + 1]
        i += 1
    return None


def extract_fullsearch_post_body(html: str) -> dict | None:
    """
    Extract the browser-style GraphQL POST JSON object for FullSearch from HTML.
    Returns a dict suitable as curl_cffi/httpx JSON body (operationName, variables, optional query/extensions).
    """
    if not html:
        return None
    for m in re.finditer(
        r'\{\s*"operationName"\s*:\s*"FullSearch"',
        html,
        flags=re.IGNORECASE,
    ):
        sliced = _json_object_slice(html, m.start())
        if not sliced:
            continue
        try:
            body = json.loads(sliced)
        except json.JSONDecodeError:
            continue
        op = body.get("operationName")
        if isinstance(op, str) and op.lower() == "fullsearch":
            return body
    return None


def load_fullsearch_body_from_env_path() -> dict | None:
    """Optional BOOKING_GRAPHQL_PAYLOAD_PATH → JSON file with FullSearch POST body."""
    raw = os.environ.get(BOOKING_GRAPHQL_PAYLOAD_PATH_ENV, "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_file():
        raise RuntimeError(
            f"{BOOKING_GRAPHQL_PAYLOAD_PATH_ENV} points to missing file: {path}"
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid JSON in {path}: {e}") from e
    if not isinstance(data, dict):
        raise RuntimeError(f"{path} must contain a JSON object")
    op = data.get("operationName")
    if not isinstance(op, str) or op.lower() != "fullsearch":
        raise RuntimeError(
            f"{path}: expected operationName FullSearch, got {op!r}"
        )
    return data


def _ensure_input_pagination(body: dict) -> tuple[dict, dict, dict]:
    """Return (variables, input dict, pagination dict) with pagination writable."""
    variables = body.setdefault("variables", {})
    if not isinstance(variables, dict):
        raise RuntimeError("FullSearch body.variables must be an object")
    inp = variables.setdefault("input", {})
    if not isinstance(inp, dict):
        raise RuntimeError("FullSearch body.variables.input must be an object")
    pag = inp.setdefault("pagination", {})
    if not isinstance(pag, dict):
        raise RuntimeError("FullSearch body.variables.input.pagination must be an object")
    return variables, inp, pag


def _graphql_headers_for_booking(referer_url: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": referer_url.split("#")[0],
        "Origin": "https://www.booking.com",
    }


def fetch_graphql_pages(search_url: str) -> tuple[str, list[dict]]:
    """
    GET search_url with curl_cffi (captures cookies), extract FullSearch template,
    POST paginated FullSearch requests until a page returns fewer than rowsPerPage hotels.

    Returns (envelope_json_string, raw_response_dicts).
    """
    _validate_booking_fetch_url(search_url)
    try:
        from curl_cffi import requests as curl_requests
    except ImportError as e:
        raise RuntimeError(
            "graphql backend requires curl-cffi. Install: pip install curl-cffi"
        ) from e

    impersonate = BOOKING_GRAPHQL_IMPERSONATE
    session = curl_requests.Session()

    get_resp = session.get(
        search_url,
        impersonate=impersonate,
        timeout=REQUEST_TIMEOUT,
    )
    get_resp.raise_for_status()
    html = get_resp.text

    base_body = extract_fullsearch_post_body(html)
    if base_body is None:
        base_body = load_fullsearch_body_from_env_path()
    if base_body is None:
        raise RuntimeError(
            "Could not extract FullSearch GraphQL POST body from HTML. "
            f"Save a captured POST JSON and set {BOOKING_GRAPHQL_PAYLOAD_PATH_ENV} "
            "to its path, or verify the search page still embeds FullSearch."
        )

    _, inp0, pag0 = _ensure_input_pagination(base_body)
    rows = inp0.get("rowsPerPage") or pag0.get("rowsPerPage") or 25
    try:
        rows_per_page = int(rows)
    except (TypeError, ValueError):
        rows_per_page = 25
    if rows_per_page < 1:
        rows_per_page = 25

    from parser import count_hotels_in_graphql_response

    responses: list[dict] = []
    offset = 0
    while True:
        body = copy.deepcopy(base_body)
        _, inp, pag = _ensure_input_pagination(body)
        inp["rowsPerPage"] = rows_per_page
        pag["offset"] = offset

        post_resp = session.post(
            BOOKING_GRAPHQL_ENDPOINT,
            json=body,
            headers=_graphql_headers_for_booking(search_url),
            impersonate=impersonate,
            timeout=REQUEST_TIMEOUT,
        )
        post_resp.raise_for_status()
        try:
            payload = post_resp.json()
        except json.JSONDecodeError as e:
            raise RuntimeError(
                "GraphQL response was not JSON. "
                f"First 400 chars: {post_resp.text[:400]!r}"
            ) from e

        responses.append(payload)

        n_hotels = count_hotels_in_graphql_response(payload)
        if n_hotels < rows_per_page:
            break
        offset += rows_per_page
        if offset >= 10_000:
            break

    envelope = {
        "format": GRAPHQL_ENVELOPE_FORMAT,
        "page_html": html,
        "responses": responses,
    }
    return json.dumps(envelope, ensure_ascii=False), responses


async def fetch_page(url: str, backend: str = "auto") -> tuple[str, str]:
    """
    Fetch a page using the specified backend.

    Returns (content, backend_used) where backend_used is
    "httpx", "firecrawl", or "graphql".

    backends:
        - "auto": try firecrawl if key exists, else httpx (never graphql)
        - "httpx": direct HTTP
        - "firecrawl": Firecrawl API
        - "graphql": Booking FullSearch GraphQL (+ bootstrap HTML GET)
    """
    import asyncio

    if backend == "auto":
        has_key = bool(os.environ.get("FIRECRAWL_API_KEY", "").strip())
        backend = "firecrawl" if has_key else "httpx"

    if backend == "firecrawl":
        return fetch_firecrawl(url), "firecrawl"
    if backend == "graphql":
        content, _ = await asyncio.to_thread(fetch_graphql_pages, url)
        return content, "graphql"

    content = await fetch_httpx(url)
    return content, "httpx"
