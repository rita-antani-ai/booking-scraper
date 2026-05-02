"""
Parse Booking.com search results from raw HTML/markdown content.

Works with the markdown output from web_extract or raw HTML.
Extracts hotel listings from the search results page.
"""

import json
import re
from html.parser import HTMLParser

from models import Hotel

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


def parse_hotels(content: str) -> list[Hotel]:
    """
    Extract hotel listings from Booking.com page content.
    Works with markdown (Firecrawl), raw HTML (httpx), and HTML converted to text.
    GPS pairs are taken from the raw page (HTML scripts / URLs / markdown) then
    matched to hotels in list order.
    """
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
