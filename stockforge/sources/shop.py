"""Door 1 — point it at your own shop and let it walk the catalogue.

Two routes, and the first is much better than the second:

  Etsy Open API v3 (preferred)
      Register an app at etsy.com/developers, set SF_ETSY_API_KEY, and you get
      listing counts, titles, tags and image URLs straight from Etsy — reliably,
      paginated, and without pretending to be a browser. This is the route that
      answers "how many uploads do I actually have", which is the question you
      wanted answering in the first place.

  Public page parsing (fallback)
      Only used when no API key is set. Slow on purpose, one request at a time,
      and it will break whenever Etsy changes their markup — that is the nature
      of scraping, not a bug to fix. Etsy also blocks aggressive clients hard,
      so the rate limiting here is protecting your access, not being polite for
      its own sake.

Either way this reads YOUR OWN shop. Pointing it at someone else's catalogue to
harvest their designs is not what this is for and not something the licence
check downstream will let you publish anyway.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Iterator

from .base import Design, Source
from .http import UA, Unavailable, download as fetch, get as http_get  # noqa: F401

log = logging.getLogger("stockforge.sources.shop")

API = "https://openapi.etsy.com/v3/application"


def _get(url: str, headers: dict | None = None, timeout: int = 45) -> bytes:
    """A request that survives a rate limit. See `sources.http`."""
    return http_get(url, headers, timeout)


# --------------------------------------------------------------------------
# the good route
# --------------------------------------------------------------------------

def _report(seen: int, dropped: int) -> None:
    """Say once, at the end, how much of the shop actually made it.

    A listing we could not get a single image for never reaches the pipeline,
    so without this it simply is not there and nothing says why. Across five
    thousand listings a handful will do this and you want to know the number.
    """
    if dropped:
        log.warning("walked %d listings; %d had no usable image and are not in "
                    "the pull", seen, dropped)
    else:
        log.info("walked %d listings, every one with images", seen)


def _api_headers() -> dict | None:
    key = os.environ.get("SF_ETSY_API_KEY")
    return {"x-api-key": key} if key else None


def resolve_shop_id(shop: str, headers: dict) -> tuple[int, str, int]:
    """Accepts a shop name, a shop URL, or a numeric ID.
    Returns (shop_id, shop_name, active_listing_count)."""
    name = shop
    if shop.startswith("http"):
        m = re.search(r"/shop/([^/?#]+)", shop)
        if m:
            name = m.group(1)

    if name.isdigit():
        data = json.loads(_get(f"{API}/shops/{name}", headers))
    else:
        found = json.loads(_get(f"{API}/shops?shop_name={urllib.parse.quote(name)}", headers))
        results = found.get("results") or []
        if not results:
            raise LookupError(f"no shop called {name!r}")
        data = results[0]

    return int(data["shop_id"]), data.get("shop_name", name), int(data.get("listing_active_count") or 0)


def api_listings(shop: str, headers: dict, limit: int | None = None) -> Iterator[dict]:
    shop_id, shop_name, total = resolve_shop_id(shop, headers)
    log.info("shop %s (%d) has %d active listings", shop_name, shop_id, total)

    offset, page = 0, 100
    while True:
        url = (f"{API}/shops/{shop_id}/listings/active"
               f"?limit={page}&offset={offset}&includes=Images")
        batch = json.loads(_get(url, headers)).get("results") or []
        if not batch:
            return
        yield from batch
        offset += len(batch)
        if limit and offset >= limit:
            return
        time.sleep(0.4)


# --------------------------------------------------------------------------
# the fallback
# --------------------------------------------------------------------------

_IMG = re.compile(r'https://i\.etsystatic\.com/[^"\'\s\\]+?\.(?:jpg|png|webp)', re.I)
_TITLE = re.compile(r"<title>(.*?)</title>", re.S | re.I)


def listing_images(url: str, dest: Path, max_images: int = 8) -> Design:
    """Pull the images off one public listing page. Used by the links door too."""
    try:
        html = _get(url).decode("utf-8", errors="replace")
    except Exception as exc:
        log.warning("could not read %s: %s", url, exc)
        return Design(design_id=url, listing_url=url, source="links")

    # keep the largest variant of each distinct image
    seen: dict[str, str] = {}
    for match in _IMG.findall(html):
        base = re.sub(r"il_\d+x\d+", "il", match)
        if base not in seen or len(match) > len(seen[base]):
            seen[base] = match

    title = None
    m = _TITLE.search(html)
    if m:
        title = re.sub(r"\s*[-|]\s*Etsy.*$", "", m.group(1)).strip()

    images: list[Path] = []
    for i, src in enumerate(list(seen.values())[:max_images]):
        got = fetch(src, dest / f"{i:02d}.jpg")
        if got:
            images.append(got)
        time.sleep(0.3)

    return Design(design_id=url, images=images, title=title, listing_url=url, source="shop")


def scrape_listing_urls(shop: str, max_pages: int = 40) -> list[str]:
    name = re.search(r"/shop/([^/?#]+)", shop).group(1) if shop.startswith("http") else shop
    urls: list[str] = []
    for page in range(1, max_pages + 1):
        try:
            html = _get(f"https://www.etsy.com/shop/{name}?page={page}").decode("utf-8", "replace")
        except Exception as exc:
            log.warning("shop page %d failed: %s", page, exc)
            break
        found = re.findall(r"https://www\.etsy\.com/listing/\d+/[^\"'?\s]+", html)
        fresh = [u for u in dict.fromkeys(found) if u not in urls]
        if not fresh:
            break
        urls.extend(fresh)
        log.info("page %d: %d listings (%d total)", page, len(fresh), len(urls))
        time.sleep(2.0)
    return urls


# --------------------------------------------------------------------------

class EtsyShopSource(Source):
    def count(self) -> int | None:
        headers = _api_headers()
        if not headers:
            return None
        try:
            return resolve_shop_id(self.target, headers)[2]
        except Exception as exc:
            log.warning("could not read shop size: %s", exc)
            return None

    def designs(self) -> Iterator[Design]:
        cache = self.cache_dir or Path("./workspace/downloads")
        headers = _api_headers()

        if headers:
            seen = dropped = 0
            for i, listing in enumerate(api_listings(self.target, headers, self.limit)):
                if self.limit and i >= self.limit:
                    break
                seen += 1
                lid = listing["listing_id"]
                dest = cache / f"etsy-{lid}"
                images = []
                for j, img in enumerate((listing.get("images") or [])[:8]):
                    src = img.get("url_fullxfull") or img.get("url_570xN")
                    if src and (got := fetch(src, dest / f"{j:02d}.jpg")):
                        images.append(got)
                if images:
                    yield Design(
                        design_id=str(lid), images=images,
                        title=listing.get("title"), tags=listing.get("tags") or [],
                        listing_url=listing.get("url"), source="etsy-api",
                    )
                else:
                    dropped += 1
                    log.warning("listing %s: not one image could be fetched", lid)
                time.sleep(0.3)
            _report(seen, dropped)
            return

        log.warning("no SF_ETSY_API_KEY set — falling back to page parsing, which is "
                    "slower and fragile. An API key is worth the ten minutes.")
        seen = dropped = 0
        for i, url in enumerate(scrape_listing_urls(self.target)):
            if self.limit and i >= self.limit:
                break
            seen += 1
            design = listing_images(url, cache / f"listing-{i:05d}")
            if design.images:
                yield design
            else:
                dropped += 1
                log.warning("%s: not one image could be fetched", url)
            time.sleep(1.5)
        _report(seen, dropped)
