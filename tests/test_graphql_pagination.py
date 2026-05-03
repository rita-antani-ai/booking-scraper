"""
Tests for GraphQL FullSearch pagination merge and HTML template extraction.

Uses synthetic payloads (no live Booking.com calls).
"""

import json

from config import GRAPHQL_ENVELOPE_FORMAT
from fetcher import extract_fullsearch_post_body
from parser import parse_hotels, parse_hotels_from_graphql_responses


def _hotel_card(hotel_id: int, name: str) -> dict:
    return {
        "displayName": {"text": name},
        "basicPropertyData": {"id": hotel_id},
        "location": {"displayLocation": "Testville"},
        "reviews": {"score": {"secondaryScore": "9.1"}},
        "priceDisplay": {},
    }


def test_offset_zero_batch_differs_from_offset_twentyfive_batch():
    """Simulate page at offset 0 vs offset 25: disjoint hotel ids/names before merge."""
    page0 = {"data": {"fullSearch": {"searchResults": [_hotel_card(i, f"A-{i}") for i in range(1, 26)]}}}
    page25 = {"data": {"fullSearch": {"searchResults": [_hotel_card(i, f"B-{i}") for i in range(101, 126)]}}}
    hotels0 = parse_hotels_from_graphql_responses([page0])
    hotels25 = parse_hotels_from_graphql_responses([page25])
    names0 = {h.name for h in hotels0}
    names25 = {h.name for h in hotels25}
    assert names0 == {f"A-{i}" for i in range(1, 26)}
    assert names25 == {f"B-{i}" for i in range(101, 126)}
    assert names0.isdisjoint(names25)


def test_pagination_merge_concatenates_pages():
    """Merged envelope yields union of hotels from multiple GraphQL responses."""
    page0 = {"nested": {"searchResults": [_hotel_card(i, f"M-{i}") for i in range(10)]}}
    page25 = {"nested": {"searchResults": [_hotel_card(i + 50, f"M-{i+50}") for i in range(10)]}}
    merged = parse_hotels_from_graphql_responses([page0, page25])
    assert len(merged) == 20
    assert {h.name for h in merged} == {f"M-{i}" for i in list(range(10)) + list(range(50, 60))}


def test_parse_hotels_reads_graphql_envelope_json():
    envelope = {
        "format": GRAPHQL_ENVELOPE_FORMAT,
        "page_html": "<html><title>Hotels in Trentino</title></html>",
        "responses": [
            {"data": {"fullSearch": {"searchResults": [_hotel_card(1, "Alpine Lodge")]}}},
        ],
    }
    hotels = parse_hotels(json.dumps(envelope))
    assert len(hotels) == 1
    assert hotels[0].name == "Alpine Lodge"


def test_extract_fullsearch_template_from_embedded_html():
    snippet = (
        'ignore {"operationName":"FullSearch","variables":{"input":'
        '{"pagination":{"offset":3},"rowsPerPage":25},"foo":true}} tail'
    )
    body = extract_fullsearch_post_body(snippet)
    assert body is not None
    assert body["operationName"] == "FullSearch"
    assert body["variables"]["input"]["pagination"]["offset"] == 3
