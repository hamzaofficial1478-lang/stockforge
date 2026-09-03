"""Where designs come in from. Three doors, one shape coming out the other side.

    1  shop      an Etsy shop ID or URL — walks the whole catalogue
    2  links     a list of listing URLs, pasted in bulk
    3  folder    images already on disk

Whichever you use, a source yields `Design` objects: a set of images that belong
to ONE product, plus whatever the listing already knows about itself. That
grouping matters — a listing carries four to six images of the same product, and
treating them as six separate designs would be both wrong and six times the cost.
"""

from __future__ import annotations

from .base import Design, Source
from .folder import FolderSource
from .links import LinksSource
from .shop import EtsyShopSource

__all__ = ["Design", "Source", "FolderSource", "LinksSource", "EtsyShopSource"]


def open_source(kind: str, target: str, **kw) -> Source:
    kinds = {"shop": EtsyShopSource, "links": LinksSource, "folder": FolderSource}
    if kind not in kinds:
        raise ValueError(f"unknown source {kind!r} — pick one of {', '.join(kinds)}")
    return kinds[kind](target, **kw)
