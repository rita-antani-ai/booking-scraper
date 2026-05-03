#!/usr/bin/env python3
"""
booking-scraper: Scrape Booking.com search results and save them locally.

Usage:
    python scraper.py <booking_url>           # Scrape and save
    python scraper.py <booking_url> --backend graphql  # FullSearch GraphQL + pagination
    python scraper.py <booking_url> --force   # Re-scrape even if visited
    python scraper.py <booking_url> --reparse # Re-parse from saved HTML (no fetch)
    python scraper.py --list                  # Show all visited searches
"""

import asyncio
import argparse
import json
import sys
from datetime import datetime, timezone

from url_utils import normalize_url, hash_url, extract_search_params, extract_dest_label
from fetcher import fetch_page
from parser import parse_hotels
from config import init_storage
from storage import get_index_entry, save_result, load_stored_page, load_index, resolve_stored_path
from models import ScrapeResult


def print_summary(result: ScrapeResult):
    """Print a nice terminal summary."""
    print()
    print(f"  Destinazione: {result.dest_label or 'N/A'}")
    print(f"  Date:         {result.checkin} → {result.checkout}")
    print(f"  Ospiti:       {result.adults} adulti, {result.children} bambini")
    print(f"  Hotel trovati:{result.n_hotels}")
    print()
    print(
        f"  {'Hotel':<36} {'Località':<18} {'Lat':>9} {'Lng':>10} "
        f"{'Voto':>5} {'$/notte':>8} {'3 notti':>8}"
    )
    print(f"  {'─'*36} {'─'*18} {'─'*9} {'─'*10} {'─'*5} {'─'*8} {'─'*8}")
    for h in result.hotels:
        name = h.name[:34]
        loc = h.location[:16]
        lat_s = (h.latitude or "-")[:8] if h.latitude else "-"
        lng_s = (h.longitude or "-")[:9] if h.longitude else "-"
        print(
            f"  {name:<36} {loc:<18} {lat_s:>9} {lng_s:>10} "
            f"{h.rating:>5} {h.price_per_night:>8} {h.total_price:>8}"
        )
    print()
    print(f"  HTML:  {result.html_file}")
    print(f"  JSON:  {result.json_file}")
    print()


def _prompt_cache_overwrite(scraped_date: str) -> bool:
    """Return True if the user confirms overwriting cached results."""
    prompt = (
        f"Questa ricerca è già stata effettuata il {scraped_date}. "
        "Vuoi sovrascrivere con risultati più recenti? (s/n) "
    )
    while True:
        ans = input(prompt).strip().lower()
        if ans in ("s", "si"):
            return True
        if ans in ("n", "no"):
            return False
        print("(Rispondi 's', 'si', 'n' o 'no'.)")


def print_index():
    """List all visited searches."""
    index = load_index()
    if not index:
        print("Nessuna ricerca salvata.")
        return
    print(f"\n  {'Hash':<14} {'Destinazione':<25} {'Date':<25} {'Hotel':>5}  {'Data scrape'}")
    print(f"  {'─'*14} {'─'*25} {'─'*25} {'─'*5}  {'─'*20}")
    for h, entry in sorted(index.items(), key=lambda x: x[1].get("scraped_at", ""), reverse=True):
        dest = entry.get("dest_label", "")[:23]
        dates = f"{entry.get('checkin', '')} → {entry.get('checkout', '')}"
        n = entry.get("n_hotels", "?")
        ts = entry.get("scraped_at", "")[:19]
        print(f"  {h:<14} {dest:<25} {dates:<25} {n:>5}  {ts}")
    print()


async def scrape(url: str, force: bool = False, reparse: bool = False, backend: str = "auto"):
    """Main scrape logic."""
    normalized = normalize_url(url)
    url_hash = hash_url(normalized)
    params = extract_search_params(url)
    now = datetime.now(timezone.utc).isoformat()
    page_suffix = "html"

    # --- Check if already visited ---
    if reparse:
        loaded = load_stored_page(url_hash)
        if not loaded:
            print(f"❌ Nessuna pagina salvata per hash {url_hash}. Scrape prima senza --reparse.")
            return
        html, page_suffix = loaded
        print(f"♻️  Re-parse da file salvato (hash: {url_hash})")
    elif not force:
        existing = get_index_entry(url_hash)
        if existing:
            json_path = resolve_stored_path(existing["json_file"])
            if json_path.is_file():
                result = ScrapeResult.model_validate_json(
                    json_path.read_text(encoding="utf-8")
                )
                print_summary(result)
            else:
                print(f"   ⚠️  File JSON non trovato: {json_path}")
                print()
                print(f"  Data scrape:    {existing['scraped_at'][:19]}")
                print(f"  Destinazione:   {existing.get('dest_label') or 'N/A'}")
                print(f"  Hotel (indice): {existing.get('n_hotels', '?')}")
                print()
            scraped_slice = existing["scraped_at"][:19]
            if not _prompt_cache_overwrite(scraped_slice):
                return

    if not reparse:
        html, used_backend = await fetch_page(url, backend=backend)
        if used_backend == "firecrawl":
            page_suffix = "md"
        elif used_backend == "graphql":
            page_suffix = "json"
        else:
            page_suffix = "html"
        if force:
            print(f"📥 Pagina scaricata — forzato re-scrape ({len(html):,} caratteri)")
        else:
            print(f"📥 Pagina scaricata ({len(html):,} caratteri)")

    # --- Parse ---
    hotels = parse_hotels(html)
    print(f"🔍 Trovati {len(hotels)} hotel")

    if not hotels:
        print("⚠️  Nessun hotel trovato. La pagina potrebbe essere un CAPTCHA o una pagina vuota.")
        print("   Controlla l'HTML salvato per debugare.")

    label_src = html
    if page_suffix == "json":
        try:
            env = json.loads(html)
            if isinstance(env, dict):
                label_src = env.get("page_html", html)
        except json.JSONDecodeError:
            pass

    # --- Build result ---
    result = ScrapeResult(
        url=url,
        url_normalized=normalized,
        url_hash=url_hash,
        scraped_at=now,
        dest_label=extract_dest_label(url, label_src),
        checkin=params["checkin"],
        checkout=params["checkout"],
        adults=params["adults"],
        children=params["children"],
        n_hotels=len(hotels),
        hotels=hotels,
    )

    # --- Save ---
    json_path = save_result(result, html, page_suffix=page_suffix)
    print(f"💾 Salvato in {json_path}")
    print_summary(result)


def main():
    init_storage()
    parser = argparse.ArgumentParser(
        description="Scrape Booking.com search results"
    )
    parser.add_argument("url", nargs="?", help="Booking.com search URL")
    parser.add_argument("--force", action="store_true", help="Re-scrape even if already visited")
    parser.add_argument("--reparse", action="store_true", help="Re-parse from saved HTML (no fetch)")
    parser.add_argument("--backend", choices=["auto", "httpx", "firecrawl", "graphql"], default="auto",
                        help="Fetch backend (default: auto — firecrawl if key exists; never graphql)")
    parser.add_argument("--list", action="store_true", help="List all visited searches")

    args = parser.parse_args()

    if args.list:
        print_index()
        return

    if not args.url:
        parser.print_help()
        sys.exit(1)

    asyncio.run(scrape(args.url, force=args.force, reparse=args.reparse, backend=args.backend))


if __name__ == "__main__":
    main()
