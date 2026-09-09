"""Door 2 — a list of listing URLs, pasted in bulk.

Takes a file with one URL per line, or a comma-separated string. Each listing's
images are downloaded into the cache and grouped as one design.

A link straight to an image file works anywhere. A link to a listing *page* is
read by the shop scraper, whose image pattern matches Etsy's CDN and nothing
else — so a listing page on another site comes back with no images and is
skipped rather than half-read. Worth knowing before pasting in a mixed list.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Iterator

from .base import Design, Source
from .http import UA, download  # noqa: F401  (UA re-exported)

log = logging.getLogger("stockforge.sources.links")


def read_links(target: str) -> list[str]:
    path = Path(target).expanduser()
    raw = path.read_text() if path.exists() else target
    links = [ln.strip() for ln in re.split(r"[\n,]", raw)]
    return [ln for ln in links if ln.startswith("http")]


def fetch(url: str, dest: Path, timeout: int = 60) -> Path | None:
    """One image. Retries are handled in `sources.http`."""
    return download(url, dest, timeout=timeout)


class LinksSource(Source):
    """Each link is one listing. Where the link points straight at an image we
    take it as a single-image design; where it points at a listing page we hand
    off to the shop scraper to pull that listing's images."""

    def count(self) -> int:
        return len(read_links(self.target))

    def designs(self) -> Iterator[Design]:
        from .shop import listing_images         # local import, avoids a cycle

        cache = self.cache_dir or Path("./workspace/downloads")
        for i, url in enumerate(read_links(self.target)):
            if self.limit and i >= self.limit:
                return

            if re.search(r"\.(jpe?g|png|webp)(\?|$)", url, re.I):
                dest = cache / f"link-{i:05d}" / Path(url.split("?")[0]).name
                got = fetch(url, dest)
                if got:
                    yield Design(design_id=url, images=[got], listing_url=url, source="links")
                else:
                    self.warnings.append(f"Could not download {url.split('?')[0]}. Check the image link or upload the file.")
                continue

            listing = listing_images(url, cache / f"listing-{i:05d}")
            if listing.images:
                yield listing
            else:
                self.warnings.append(f"{url.split('?')[0]}: {listing.import_error or 'No images found; upload the saved image instead.'}")
            time.sleep(1.0)                      # be a good citizen
