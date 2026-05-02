"""
Parse Booking.com search results from raw HTML/markdown content.

Works with the markdown output from web_extract or raw HTML.
Extracts hotel listings from the search results page.
"""

import re
from html.parser import HTMLParser

from models import Hotel


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
    """
    if _looks_like_html(content):
        content = _html_to_text(content)

    lines = content.split("\n")
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
                _finalize_and_append(current, hotels)
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
        _finalize_and_append(current, hotels)

    return hotels


def _finalize_and_append(data: dict, hotels: list[Hotel]):
    """Fill defaults and append a Hotel."""
    hotels.append(Hotel(
        name=data.get("name", ""),
        location=data.get("location", ""),
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
