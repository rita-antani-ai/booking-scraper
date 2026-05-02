"""
Data models for Booking.com hotel results.
"""

from pydantic import BaseModel, Field


class Hotel(BaseModel):
    name: str
    location: str = ""
    latitude: str = ""
    longitude: str = ""
    rating: str = ""
    label: str = ""
    reviews: str = ""
    room: str = ""
    price_per_night: str = ""
    total_price: str = ""
    free_cancellation: bool = False
    no_prepayment: bool = False
    link: str = ""


class ScrapeResult(BaseModel):
    url: str
    url_normalized: str
    url_hash: str
    scraped_at: str
    dest_label: str = ""
    checkin: str = ""
    checkout: str = ""
    adults: int = 0
    children: int = 0
    n_hotels: int = 0
    hotels: list[Hotel] = Field(default_factory=list)
    html_file: str = ""
    json_file: str = ""
