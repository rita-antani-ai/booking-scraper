"""
Parse Booking.com search results from raw HTML/markdown content.

Works with the markdown output from web_extract or raw HTML.
Extracts hotel listings from the search results page.
"""

import re
from models import Hotel


def parse_hotels(content: str) -> list[Hotel]:
    """
    Extract hotel listings from Booking.com page content.
    Works with both raw HTML and markdown-rendered content.
    """
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

        # --- Rating: Scored X.X ---
        rating_match = re.search(r"Scored (\d+\.\d+)", s)
        if rating_match and current:
            current["rating"] = rating_match.group(1)
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
                price_match = re.match(r"^\$(\d[\d,]*)$", lines[j].strip())
                if price_match:
                    current.setdefault("price_per_night", price_match.group(1).replace(",", ""))
                    break
            continue

        # --- Total price: line with "Price $N" or "Original price ... Current price $N" ---
        if current and "total_price" not in current:
            # Original price line (discounted)
            discount = re.search(r"Current price \$(\d[\d,]*)", s)
            if discount:
                current["total_price"] = discount.group(1).replace(",", "")
                continue
            # Plain price line
            plain = re.match(r"^Price \$(\d[\d,]*)$", s)
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
