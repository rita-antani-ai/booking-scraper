"""
Parse Booking.com search results from raw HTML/markdown content.

Works with the markdown output from web_extract or raw HTML.
Extracts hotel listings from the search results page.
"""

import json
import re
from html.parser import HTMLParser

from models import Hotel
from config import GRAPHQL_ENVELOPE_FORMAT

# Decimal degrees (optional leading minus, at least one digit before optional fraction)
_COORD_FLOAT = r"-?\d{1,3}(?:\.\d+)?"


def _parse_coord_number(s: str) -> float | None:
    try:
        return float(s.replace(",", "."))
    except ValueError:
        return None


def _is_valid_lat(v: float) -> bool:
    return -90.0 <= v <= 90.0


def _is_valid_lng(v: float) -> bool:
    return -180.0 <= v <= 180.0


def _is_valid_pair(lat: float, lng: float) -> bool:
    if not _is_valid_lat(lat) or not _is_valid_lng(lng):
        return False
    # Reject (0, 0) — almost never a real property location on Booking
    if abs(lat) < 1e-6 and abs(lng) < 1e-6:
        return False
    return True


def _format_coord_str(v: float) -> str:
    s = f"{v:.8f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _dedupe_key(lat: float, lng: float) -> tuple[float, float]:
    return (round(lat, 5), round(lng, 5))


def _append_pair_unique(
    out: list[tuple[str, str]],
    seen: set[tuple[float, float]],
    lat: float,
    lng: float,
) -> None:
    if not _is_valid_pair(lat, lng):
        return
    key = _dedupe_key(lat, lng)
    if key in seen:
        return
    seen.add(key)
    out.append((_format_coord_str(lat), _format_coord_str(lng)))


def _walk_json_for_geo(obj, out: list[tuple[float, float]]) -> None:
    """Collect (lat, lng) from JSON-LD / nested dicts (geo, latitude/longitude)."""
    if isinstance(obj, dict):
        geo = obj.get("geo")
        if isinstance(geo, dict):
            la = geo.get("latitude") or geo.get("lat")
            lo = geo.get("longitude") or geo.get("lng") or geo.get("lon")
            if la is not None and lo is not None:
                try:
                    flat = float(la) if not isinstance(la, (dict, list)) else None
                    flng = float(lo) if not isinstance(lo, (dict, list)) else None
                    if flat is not None and flng is not None and _is_valid_pair(flat, flng):
                        out.append((flat, flng))
                except (TypeError, ValueError):
                    pass
        la = obj.get("latitude") or obj.get("lat")
        lo = obj.get("longitude") or obj.get("lng") or obj.get("lon")
        if la is not None and lo is not None and geo is None:
            try:
                flat = float(la) if not isinstance(la, (dict, list)) else None
                flng = float(lo) if not isinstance(lo, (dict, list)) else None
                if flat is not None and flng is not None and _is_valid_pair(flat, flng):
                    out.append((flat, flng))
            except (TypeError, ValueError):
                pass
        for v in obj.values():
            _walk_json_for_geo(v, out)
    elif isinstance(obj, list):
        for item in obj:
            _walk_json_for_geo(item, out)


def _try_parse_json_blob(blob: str) -> list:
    """Parse one or more JSON values from a string (LD+JSON sometimes concatenated)."""
    blob = blob.strip()
    if not blob:
        return []
    out: list = []
    dec = json.JSONDecoder()
    idx = 0
    n = len(blob)
    while idx < n:
        while idx < n and blob[idx] in " \t\r\n":
            idx += 1
        if idx >= n:
            break
        try:
            val, end = dec.raw_decode(blob, idx)
        except json.JSONDecodeError:
            break
        out.append(val)
        idx = end
    if out:
        return out
    if blob.startswith(("{", "[")):
        try:
            return [json.loads(blob)]
        except json.JSONDecodeError:
            pass
    return []


def _coord_pairs_from_json_ld(content: str) -> list[tuple[float, float]]:
    pairs: list[tuple[float, float]] = []
    for m in re.finditer(
        r'<script[^>]+type\s*=\s*["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        content,
        re.IGNORECASE | re.DOTALL,
    ):
        for root in _try_parse_json_blob(m.group(1)):
            _walk_json_for_geo(root, pairs)
    return pairs


def _coord_pairs_from_loose_json(content: str) -> list[tuple[float, float]]:
    """
    Booking often embeds hotelLatitude/hotelLongitude or similar in inline JSON
    (not always inside type=application/ld+json). Scan for key-value pairs.
    """
    pairs: list[tuple[float, float]] = []
    # Named Booking / generic keys (case-sensitive keys in JSON are usually lower camel)
    la_names = (
        r'(?:"hotelLatitude"|"latitude"|"lat"|hotelLatitude|latitude|lat)\s*:\s*'
        r'(["\']?)(' + _COORD_FLOAT + r')\1'
    )
    lo_names = (
        r'(?:"hotelLongitude"|"longitude"|"lng"|"lon"|hotelLongitude|longitude|lng|lon)\s*:\s*'
        r'(["\']?)(' + _COORD_FLOAT + r')\1'
    )
    for m in re.finditer(la_names, content):
        flat = _parse_coord_number(m.group(2))
        if flat is None:
            continue
        start = m.end()
        window = content[start : start + 800]
        lon_m = re.search(lo_names, window)
        if not lon_m:
            continue
        flng = _parse_coord_number(lon_m.group(2))
        if flng is None:
            continue
        if _is_valid_pair(flat, flng):
            pairs.append((flat, flng))
    return pairs


def _coord_pairs_from_meta(content: str) -> list[tuple[float, float]]:
    pairs: list[tuple[float, float]] = []
    meta_geo = (
        r'<meta\s+[^>]*name\s*=\s*["\']geo\.position["\'][^>]*content\s*=\s*["\']([^"\']+)["\']',
        r'<meta\s+[^>]*content\s*=\s*["\']([^"\']+)["\'][^>]*name\s*=\s*["\']geo\.position["\']',
    )
    for pat in meta_geo:
        for m in re.finditer(pat, content, re.IGNORECASE):
            parts = re.split(r"[;,]\s*", m.group(1).strip())
            if len(parts) >= 2:
                flat = _parse_coord_number(parts[0])
                flng = _parse_coord_number(parts[1])
                if flat is not None and flng is not None and _is_valid_pair(flat, flng):
                    pairs.append((flat, flng))
    for m in re.finditer(
        r'<meta\s+[^>]*property\s*=\s*["\']place:location:latitude["\'][^>]*content\s*=\s*["\']([^"\']+)["\']',
        content,
        re.IGNORECASE,
    ):
        flat = _parse_coord_number(m.group(1))
        if flat is None:
            continue
        after = content[m.end() : m.end() + 400]
        lat_m = re.search(
            r'place:location:longitude["\'][^>]*content\s*=\s*["\']([^"\']+)["\']',
            after,
            re.IGNORECASE,
        )
        if not lat_m:
            continue
        flng = _parse_coord_number(lat_m.group(1))
        if flng is not None and _is_valid_pair(flat, flng):
            pairs.append((flat, flng))
    return pairs


def _coord_pairs_from_data_attrs(content: str) -> list[tuple[float, float]]:
    pairs: list[tuple[float, float]] = []
    for m in re.finditer(
        r'data-(?:lat|latitude)\s*=\s*["\'](?P<la>' + _COORD_FLOAT + r')["\']'
        r'[^>]{0,300}'
        r'data-(?:lng|lon|longitude)\s*=\s*["\'](?P<lo>' + _COORD_FLOAT + r')["\']',
        content,
        re.IGNORECASE,
    ):
        flat = _parse_coord_number(m.group("la"))
        flng = _parse_coord_number(m.group("lo"))
        if flat is not None and flng is not None and _is_valid_pair(flat, flng):
            pairs.append((flat, flng))
    # Allow reverse order (lng then lat) — rare
    for m in re.finditer(
        r'data-(?:lng|lon|longitude)\s*=\s*["\'](?P<lo>' + _COORD_FLOAT + r')["\']'
        r'[^>]{0,300}'
        r'data-(?:lat|latitude)\s*=\s*["\'](?P<la>' + _COORD_FLOAT + r')["\']',
        content,
        re.IGNORECASE,
    ):
        flat = _parse_coord_number(m.group("la"))
        flng = _parse_coord_number(m.group("lo"))
        if flat is not None and flng is not None and _is_valid_pair(flat, flng):
            pairs.append((flat, flng))
    return pairs


def _coord_pairs_from_itemprop(content: str) -> list[tuple[float, float]]:
    """Microdata itemprop latitude / longitude."""
    pairs: list[tuple[float, float]] = []
    lat_re = re.compile(
        r'itemprop\s*=\s*["\']latitude["\'][^>]*\scontent\s*=\s*["\'](?P<v>' + _COORD_FLOAT + r')["\']',
        re.IGNORECASE,
    )
    lat_re_alt = re.compile(
        r'content\s*=\s*["\'](?P<v>' + _COORD_FLOAT + r')["\'][^>]*itemprop\s*=\s*["\']latitude["\']',
        re.IGNORECASE,
    )
    lon_re = re.compile(
        r'itemprop\s*=\s*["\']longitude["\'][^>]*\scontent\s*=\s*["\'](?P<v>' + _COORD_FLOAT + r')["\']',
        re.IGNORECASE,
    )
    lon_re_alt = re.compile(
        r'content\s*=\s*["\'](?P<v>' + _COORD_FLOAT + r')["\'][^>]*itemprop\s*=\s*["\']longitude["\']',
        re.IGNORECASE,
    )

    def _consume_lat_block(start: int, flat: float) -> None:
        window = content[start : start + 600]
        for lon_pattern in (lon_re, lon_re_alt):
            ln = lon_pattern.search(window)
            if ln:
                flng = _parse_coord_number(ln.group("v"))
                if flng is not None and _is_valid_pair(flat, flng):
                    pairs.append((flat, flng))
                return

    for m in lat_re.finditer(content):
        flat = _parse_coord_number(m.group("v"))
        if flat is not None:
            _consume_lat_block(m.end(), flat)
    for m in lat_re_alt.finditer(content):
        flat = _parse_coord_number(m.group("v"))
        if flat is not None:
            _consume_lat_block(m.end(), flat)
    return pairs


def _coord_pairs_from_map_urls(content: str) -> list[tuple[float, float]]:
    pairs: list[tuple[float, float]] = []
    # Google Maps: @lat,lng or @lat,lng,zoom
    for m in re.finditer(
        r"@\s*(" + _COORD_FLOAT + r")\s*,\s*(" + _COORD_FLOAT + r")(?:\s*,\s*\d+(?:\.\d+)?z\b|\s*,|\s*$|\s*/|\s*\?)",
        content,
    ):
        flat = _parse_coord_number(m.group(1))
        flng = _parse_coord_number(m.group(2))
        if flat is not None and flng is not None and _is_valid_pair(flat, flng):
            pairs.append((flat, flng))
    # q=lat,lng in maps query
    for m in re.finditer(
        r"(?:[?&]q=|query=)(-?\d{1,2}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\b",
        content,
        re.IGNORECASE,
    ):
        flat = _parse_coord_number(m.group(1))
        flng = _parse_coord_number(m.group(2))
        if flat is not None and flng is not None and _is_valid_pair(flat, flng):
            pairs.append((flat, flng))
    # booking.com map / static map URLs
    for m in re.finditer(
        r"(?:latitude|lat)=(" + _COORD_FLOAT + r")[^&\s\"'<>]{0,80}(?:longitude|lng|lon)=("
        + _COORD_FLOAT
        + r")",
        content,
        re.IGNORECASE,
    ):
        flat = _parse_coord_number(m.group(1))
        flng = _parse_coord_number(m.group(2))
        if flat is not None and flng is not None and _is_valid_pair(flat, flng):
            pairs.append((flat, flng))
    for m in re.finditer(
        r"(?:longitude|lng|lon)=(" + _COORD_FLOAT + r")[^&\s\"'<>]{0,80}(?:latitude|lat)=("
        + _COORD_FLOAT
        + r")",
        content,
        re.IGNORECASE,
    ):
        flng = _parse_coord_number(m.group(1))
        flat = _parse_coord_number(m.group(2))
        if flat is not None and flng is not None and _is_valid_pair(flat, flng):
            pairs.append((flat, flng))
    return pairs


def _coord_pairs_from_kv_proximity(content: str) -> list[tuple[float, float]]:
    """
    Generic lat/lng labels near each other (markdown or minified HTML without script).
    """
    pairs: list[tuple[float, float]] = []
    lat_label = re.compile(
        r"(?:\b|_)(?:lat|latitude)(?:itude)?\b\s*[:=]\s*(" + _COORD_FLOAT + r")",
        re.IGNORECASE,
    )
    lng_label = re.compile(
        r"(?:\b|_)(?:lng|lon|longitude)\b\s*[:=]\s*(" + _COORD_FLOAT + r")",
        re.IGNORECASE,
    )
    for m in lat_label.finditer(content):
        flat = _parse_coord_number(m.group(1))
        if flat is None or not _is_valid_lat(flat):
            continue
        window = content[m.end() : m.end() + 250]
        ln = lng_label.search(window)
        if not ln:
            continue
        flng = _parse_coord_number(ln.group(1))
        if flng is not None and _is_valid_pair(flat, flng):
            pairs.append((flat, flng))
    return pairs


def extract_coordinate_pairs(content: str) -> list[tuple[str, str]]:
    """
    Extract GPS pairs from raw page content (HTML or markdown).
    Runs on the original string so JSON-LD in <script> is still visible to regex/JSON.
    Ordered by priority; duplicates (5dp) are dropped.
    """
    if not content or not content.strip():
        return []

    out: list[tuple[str, str]] = []
    seen: set[tuple[float, float]] = set()

    # 1) Structured data & Booking JSON keys (highest signal)
    for flat, flng in _coord_pairs_from_json_ld(content):
        _append_pair_unique(out, seen, flat, flng)
    for flat, flng in _coord_pairs_from_loose_json(content):
        _append_pair_unique(out, seen, flat, flng)
    for flat, flng in _coord_pairs_from_meta(content):
        _append_pair_unique(out, seen, flat, flng)
    for flat, flng in _coord_pairs_from_data_attrs(content):
        _append_pair_unique(out, seen, flat, flng)
    for flat, flng in _coord_pairs_from_itemprop(content):
        _append_pair_unique(out, seen, flat, flng)
    for flat, flng in _coord_pairs_from_map_urls(content):
        _append_pair_unique(out, seen, flat, flng)
    for flat, flng in _coord_pairs_from_kv_proximity(content):
        _append_pair_unique(out, seen, flat, flng)

    return out


class _HTMLToText(HTMLParser):
    """Strip tags to plain text with newlines for block elements."""

    _BLOCK = frozenset(
        ("p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "tr", "section", "article")
    )

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip = True
        elif tag in self._BLOCK:
            self._parts.append("\n")
        elif tag == "br":
            self._parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        if tag == "br":
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self._skip = False
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_data(self, data):
        if not self._skip and data:
            self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts)


def _looks_like_html(content: str) -> bool:
    head = content.lstrip()[:800].lower()
    if head.startswith("<!doctype") or head.startswith("<html"):
        return True
    pos = head.find("<html")
    return 0 <= pos < 400


def _html_to_text(html: str) -> str:
    parser = _HTMLToText()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return html
    return parser.get_text()


def _looks_like_graphql_hotel_card(d: dict) -> bool:
    """Heuristic: Booking FullSearch cards usually expose basicPropertyData or displayName + commerce fields."""
    if not isinstance(d, dict):
        return False
    bpd = d.get("basicPropertyData")
    if isinstance(bpd, dict) and (bpd.get("id") is not None or bpd.get("name")):
        return True
    if d.get("displayName") is not None and (
        "priceDisplay" in d or "reviews" in d or "location" in d
    ):
        return True
    return False


def iter_graphql_search_result_cards(obj) -> object:
    """Yield hotel-shaped dicts nested under any searchResults list in a GraphQL JSON tree."""
    if isinstance(obj, dict):
        sr = obj.get("searchResults")
        if isinstance(sr, list):
            for item in sr:
                if isinstance(item, dict) and _looks_like_graphql_hotel_card(item):
                    yield item
        for v in obj.values():
            yield from iter_graphql_search_result_cards(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from iter_graphql_search_result_cards(item)


def count_hotels_in_graphql_response(resp: dict) -> int:
    """Count FullSearch hotel cards in one GraphQL JSON response dict."""
    if not isinstance(resp, dict):
        return 0
    return sum(1 for _ in iter_graphql_search_result_cards(resp))


def _graphql_display_name(card: dict) -> str:
    dn = card.get("displayName")
    if isinstance(dn, dict):
        for key in ("text", "plainText", "title"):
            v = dn.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    elif isinstance(dn, str) and dn.strip():
        return dn.strip()
    bpd = card.get("basicPropertyData") if isinstance(card.get("basicPropertyData"), dict) else {}
    for key in ("name", "propertyName", "plainName"):
        v = bpd.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _graphql_booking_link(card: dict) -> str:
    for key in ("shareUrl", "propertyUrl", "landingURL", "landingUrl"):
        u = card.get(key)
        if isinstance(u, str) and "booking.com" in u:
            return u
    urls = card.get("urls")
    if isinstance(urls, dict):
        for u in urls.values():
            if isinstance(u, str) and "booking.com" in u:
                return u
    bpd = card.get("basicPropertyData") if isinstance(card.get("basicPropertyData"), dict) else {}
    for key in ("url", "propertyUrl"):
        u = bpd.get(key)
        if isinstance(u, str) and "booking.com" in u:
            return u
    page_name = bpd.get("pageName")
    if isinstance(page_name, str) and page_name.strip():
        return f"https://www.booking.com/hotel/{page_name.strip()}.html"
    return ""


def _graphql_stable_hotel_id(card: dict) -> str:
    bpd = card.get("basicPropertyData") if isinstance(card.get("basicPropertyData"), dict) else {}
    hid = bpd.get("id")
    if hid is not None:
        return f"id:{hid}"
    ufi = bpd.get("ufi")
    slug = (bpd.get("pageName") or bpd.get("slug") or "").strip()
    if ufi is not None and slug:
        return f"ufi:{ufi}:{slug}"
    link = _graphql_booking_link(card)
    if link:
        return f"url:{link}"
    name = _graphql_display_name(card)
    loc_obj = card.get("location") if isinstance(card.get("location"), dict) else {}
    loc = loc_obj.get("displayLocation") if isinstance(loc_obj.get("displayLocation"), str) else ""
    return f"fallback:{name}|{loc}"


def _graphql_coord_pair(card: dict) -> tuple[str, str]:
    bpd = card.get("basicPropertyData") if isinstance(card.get("basicPropertyData"), dict) else {}
    lat_keys = ("latitude", "lat")
    lng_keys = ("longitude", "lng", "lon")
    flat = lng = None
    for lk in lat_keys:
        v = bpd.get(lk)
        if isinstance(v, (int, float)):
            flat = float(v)
            break
        if isinstance(v, str):
            flat = _parse_coord_number(v)
            break
    for lk in lng_keys:
        v = bpd.get(lk)
        if isinstance(v, (int, float)):
            lng = float(v)
            break
        if isinstance(v, str):
            lng = _parse_coord_number(v)
            break
    loc = card.get("location") if isinstance(card.get("location"), dict) else {}
    if flat is None or lng is None:
        geo = loc.get("geo") if isinstance(loc.get("geo"), dict) else {}
        if flat is None:
            for lk in lat_keys:
                v = geo.get(lk) or loc.get(lk)
                if isinstance(v, (int, float)):
                    flat = float(v)
                    break
                if isinstance(v, str):
                    flat = _parse_coord_number(v)
                    break
        if lng is None:
            for lk in lng_keys:
                v = geo.get(lk) or loc.get(lk)
                if isinstance(v, (int, float)):
                    lng = float(v)
                    break
                if isinstance(v, str):
                    lng = _parse_coord_number(v)
                    break
    la_s = lo_s = ""
    if flat is not None and lng is not None and _is_valid_pair(flat, lng):
        la_s = _format_coord_str(flat)
        lo_s = _format_coord_str(lng)
    return la_s, lo_s


def _graphql_rating_and_reviews(card: dict) -> tuple[str, str]:
    reviews = card.get("reviews") if isinstance(card.get("reviews"), dict) else {}
    score = reviews.get("score")
    rating_s = ""
    if isinstance(score, dict):
        for key in ("secondaryScore", "primaryScore", "value", "formatted"):
            v = score.get(key)
            if isinstance(v, str) and v.strip():
                rating_s = v.strip().replace(",", ".")
                break
            if isinstance(v, (int, float)):
                rating_s = str(v)
                break
    elif isinstance(score, (int, float)):
        rating_s = str(score)
    reviews_count = ""
    cnt = reviews.get("count") if isinstance(reviews.get("count"), dict) else {}
    if isinstance(cnt, dict):
        v = cnt.get("formatted") or cnt.get("text")
        if isinstance(v, str):
            reviews_count = v.replace(",", "").strip()
    rc = reviews.get("reviewsCount") if isinstance(reviews.get("reviewsCount"), dict) else {}
    if isinstance(rc, dict):
        v = rc.get("formatted") or rc.get("text")
        if isinstance(v, str):
            reviews_count = v.replace(",", "").strip()
    return rating_s, reviews_count


def _graphql_prices(card: dict) -> tuple[str, str]:
    """Return (price_per_night-ish, total/strike bundle best-effort)."""
    pd = card.get("priceDisplay") if isinstance(card.get("priceDisplay"), dict) else {}

    def _fmt_money_blob(blob: dict | None) -> str:
        if not isinstance(blob, dict):
            return ""
        amt = blob.get("chargeAmount") if isinstance(blob.get("chargeAmount"), dict) else {}
        if isinstance(amt, dict):
            raw = amt.get("formatted") or amt.get("plainText")
            if isinstance(raw, str):
                return raw
        disp = blob.get("displayAmount") if isinstance(blob.get("displayAmount"), dict) else {}
        if isinstance(disp, dict):
            raw = disp.get("formatted") or disp.get("plainText")
            if isinstance(raw, str):
                return raw
        return ""

    per_night = _fmt_money_blob(pd.get("priceBeforeDiscount") or pd.get("displayPrice"))
    if not per_night:
        per_night = _fmt_money_blob(pd.get("leadingCaption"))
    total = _fmt_money_blob(pd.get("totalPrice") or pd.get("displayPrice"))

    def _digits(s: str) -> str:
        m = re.search(r"(\d[\d\s,]*)", s)
        if not m:
            return ""
        return re.sub(r"\D", "", m.group(1))

    return _digits(per_night), _digits(total)


def _graphql_room_hint(card: dict) -> str:
    blocks = card.get("blocks") if isinstance(card.get("blocks"), list) else []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        if b.get("__typename") == "SearchResultsRoomSkeleton":
            rm = b.get("roomInformation") if isinstance(b.get("roomInformation"), dict) else {}
            name = rm.get("roomName") or rm.get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
    return ""


def _graphql_flags(card: dict) -> tuple[bool, bool]:
    text_blob = json.dumps(card, ensure_ascii=False)
    free_cancel = "free cancellation" in text_blob.lower()
    no_prepay = "no prepayment" in text_blob.lower() or "pay at the property" in text_blob.lower()
    pol = card.get("cancellationPolicies") if isinstance(card.get("cancellationPolicies"), dict) else {}
    if pol.get("freeCancellation") is True:
        free_cancel = True
    return free_cancel, no_prepay


def hotel_from_graphql_card(card: dict) -> Hotel:
    """Map one FullSearch hotel-shaped GraphQL dict into Hotel."""
    name = _graphql_display_name(card)
    loc_obj = card.get("location") if isinstance(card.get("location"), dict) else {}
    location = ""
    if isinstance(loc_obj.get("displayLocation"), str):
        location = loc_obj["displayLocation"].strip()
    la, lo = _graphql_coord_pair(card)
    rating, reviews = _graphql_rating_and_reviews(card)
    ppn, total = _graphql_prices(card)
    room = _graphql_room_hint(card)
    free_c, no_prepay = _graphql_flags(card)
    link = _graphql_booking_link(card)
    label = ""
    secondary = card.get("secondaryScore") if isinstance(card.get("secondaryScore"), dict) else {}
    if isinstance(secondary.get("description"), str):
        label = secondary["description"].strip()

    return Hotel(
        name=name,
        location=location,
        latitude=la,
        longitude=lo,
        rating=rating,
        label=label,
        reviews=reviews,
        room=room,
        price_per_night=ppn,
        total_price=total,
        free_cancellation=free_c,
        no_prepayment=no_prepay,
        link=link,
    )


def parse_hotels_from_graphql_responses(responses: list) -> list[Hotel]:
    """Merge paginated FullSearch GraphQL responses into deduplicated Hotel rows."""
    seen: set[str] = set()
    out: list[Hotel] = []
    if not isinstance(responses, list):
        return out
    for resp in responses:
        if not isinstance(resp, dict):
            continue
        for card in iter_graphql_search_result_cards(resp):
            hid = _graphql_stable_hotel_id(card)
            if hid in seen:
                continue
            seen.add(hid)
            out.append(hotel_from_graphql_card(card))
    return out


def parse_hotels(content: str) -> list[Hotel]:
    """
    Extract hotel listings from Booking.com page content.
    Works with markdown (Firecrawl), raw HTML (httpx), and HTML converted to text.
    GPS pairs are taken from the raw page (HTML scripts / URLs / markdown) then
    matched to hotels in list order.

    GraphQL backend persists a JSON envelope (see config.GRAPHQL_ENVELOPE_FORMAT): parsed via
    parse_hotels_from_graphql_responses.
    """
    stripped = content.lstrip()
    if stripped.startswith("{"):
        try:
            envelope = json.loads(content)
        except json.JSONDecodeError:
            envelope = None
        else:
            if isinstance(envelope, dict) and envelope.get("format") == GRAPHQL_ENVELOPE_FORMAT:
                return parse_hotels_from_graphql_responses(envelope.get("responses") or [])

    raw = content
    coord_pairs = extract_coordinate_pairs(raw)
    text_lines = _html_to_text(raw) if _looks_like_html(raw) else raw

    lines = text_lines.split("\n")
    hotels: list[Hotel] = []
    current: dict = {}

    for i, line in enumerate(lines):
        s = line.strip()
        if not s:
            continue

        # --- Hotel name: ### [Name \ Opens in new window] ---
        name_match = re.search(r"### \[(.+?)\\", s)
        if name_match:
            if current.get("name"):
                _finalize_and_append(current, hotels, coord_pairs)
            current = {"name": name_match.group(1).strip()}
            continue

        # --- Location: [CityShow on map] ---
        loc_match = re.search(
            r"^\[([A-Za-zÀ-ú\s\-']+(?:\s[A-Za-zÀ-ú\s\-']+)*)"
            r"(?:Show on map|Mostra su mappa)",
            s,
        )
        if loc_match and current:
            current["location"] = loc_match.group(1).strip()
            continue

        # --- Rating: several Booking / locale patterns ---
        rating_match = None
        for pat in (
            r"Scored (\d+[.,]\d+)",
            r"Review score (\d+[.,]\d+)",
            r"(\d+[.,]\d+)\s*/\s*10",
            r"(?:rating|bewertung|note|valutazione)\s*[:\s]+(\d+[.,]\d+)",
            r"(\d+[.,]\d+)\s+out of\s+10",
            r"(?:★|⭐)\s*(\d+[.,]\d+)",
        ):
            rating_match = re.search(pat, s, re.IGNORECASE)
            if rating_match:
                break
        if rating_match and current:
            current["rating"] = rating_match.group(1).replace(",", ".")
            continue

        # --- Reviews: N reviews / N recensioni ---
        review_match = re.search(r"([\d,]+)\s*(?:reviews|recensioni)", s, re.IGNORECASE)
        if review_match and current:
            current["reviews"] = review_match.group(1)
            continue

        # --- Rating label ---
        for label in ["Exceptional", "Wonderful", "Excellent", "Very Good", "Good", "Pleasant"]:
            if s.rstrip("\\") == label:
                current.setdefault("label", label)
                break

        # --- Room type: #### Room Type ---
        room_match = re.search(r"#### (.+)", s)
        if room_match and current:
            current.setdefault("room", room_match.group(1).strip())
            continue

        # --- Price per night: line after "Per night" ---
        if s == "Per night" and current:
            for j in range(i + 1, min(i + 4, len(lines))):
                price_match = re.match(
                    r"^[\$€£¥]?\s*(\d[\d,]*)\s*[\$€£¥]?$",
                    lines[j].strip(),
                )
                if price_match:
                    current.setdefault("price_per_night", price_match.group(1).replace(",", ""))
                    break
            continue

        # --- Total price: line with "Price $N" or "Original price ... Current price $N" ---
        if current and "total_price" not in current:
            # Original price line (discounted)
            discount = re.search(
                r"Current price\s+[\$€£¥]?\s*(\d[\d,]*)",
                s,
                re.IGNORECASE,
            )
            if discount:
                current["total_price"] = discount.group(1).replace(",", "")
                continue
            # Plain price line
            plain = re.match(r"^Price\s+[\$€£¥]?\s*(\d[\d,]*)$", s, re.IGNORECASE)
            if plain:
                current["total_price"] = plain.group(1).replace(",", "")
                continue

        # --- Amenities ---
        if "Free cancellation" in s and current:
            current["free_cancellation"] = True
        if "No prepayment needed" in s and current:
            current["no_prepayment"] = True

        # --- Link: [See availability](url) ---
        link_match = re.search(r"\[See availability\]\((https://www\.booking\.com/[^\)]+)\)", s)
        if link_match and current:
            current.setdefault("link", link_match.group(1))

    # Don't forget the last hotel
    if current.get("name"):
        _finalize_and_append(current, hotels, coord_pairs)

    return hotels


def _finalize_and_append(
    data: dict,
    hotels: list[Hotel],
    coord_pairs: list[tuple[str, str]],
):
    """Fill defaults and append a Hotel."""
    idx = len(hotels)
    la, lo = ("", "")
    if idx < len(coord_pairs):
        la, lo = coord_pairs[idx]
    hotels.append(Hotel(
        name=data.get("name", ""),
        location=data.get("location", ""),
        latitude=la,
        longitude=lo,
        rating=data.get("rating", ""),
        label=data.get("label", ""),
        reviews=data.get("reviews", ""),
        room=data.get("room", ""),
        price_per_night=data.get("price_per_night", ""),
        total_price=data.get("total_price", ""),
        free_cancellation=data.get("free_cancellation", False),
        no_prepayment=data.get("no_prepayment", False),
        link=data.get("link", ""),
    ))
