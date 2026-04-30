# booking-scraper

Scrape Booking.com search results and save them locally for offline analysis.

## Features

- **Deduplication**: normalizes URLs and hashes them — same search won't be scraped twice
- **Raw HTML storage**: saves the full page for future re-parsing without re-fetching
- **Re-parse mode**: extract new fields from saved HTML without hitting Booking.com again
- **Index**: tracks all scraped searches with metadata in `output/index.json`
- **Multiple backends**: direct HTTP (fast) or Firecrawl (bypasses anti-bot)

## Install

```bash
pip install -r requirements.txt
```

### Firecrawl (recommended for Booking.com)

Booking.com blocks direct HTTP requests with a JavaScript challenge.
For reliable scraping, use Firecrawl as backend:

1. Get an API key at https://www.firecrawl.dev
2. Export it:
   ```bash
   export FIRECRAWL_API_KEY=fc-xxxxxxxxxxxx
   ```

Without Firecrawl, the scraper uses direct httpx — fast but Booking.com
will return a CAPTCHA page instead of results.

## Usage

```bash
# Scrape (auto-detects backend: uses firecrawl if key is set)
python scraper.py "https://www.booking.com/searchresults.it.html?checkin=2026-06-17&checkout=2026-06-20&dest_id=911&dest_type=region&group_adults=2&group_children=1&nflt=..."

# Force a specific backend
python scraper.py "<url>" --backend firecrawl
python scraper.py "<url>" --backend httpx

# List all saved searches
python scraper.py --list

# Force re-scrape (overwrite existing)
python scraper.py "<url>" --force

# Re-parse from saved HTML (no HTTP request)
python scraper.py "<url>" --reparse
```

## Output structure

```
output/
├── index.json                  # Index of all scraped searches
└── results/
    └── <hash>/
        ├── page.html           # Raw page content (for re-parsing)
        └── hotels.json         # Structured hotel data
```

## URL normalization

Tracking parameters (`label`, `sid`, `aid`) are stripped before hashing.
Only search-defining parameters are kept:

`checkin`, `checkout`, `dest_id`, `dest_type`, `group_adults`, `group_children`, `age`, `nflt`, etc.

This means the same search with different tracking codes won't be duplicated.

## Data extracted per hotel

| Field | Example |
|-------|---------|
| name | Hotel Montana |
| location | Vason |
| rating | 9.1 |
| label | Wonderful |
| reviews | 506 |
| room | Deluxe Double or Twin Room |
| price_per_night | 249 |
| total_price | 748 |
| free_cancellation | true |
| no_prepayment | false |
| link | https://www.booking.com/hotel/it/montana.html... |
