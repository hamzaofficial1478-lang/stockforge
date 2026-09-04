"""Is this one too close to something we already made?

The distinctness check in `derive` asks one question: does this rebuild still
read as a copy of the design it learned from? That is the right question and it
is not the whole of it. Nothing has ever compared design four hundred against
design twelve.

It should, because the failure mode is real and it is expensive. Mixing draws
its ingredients from one pool and derivation applies one family of moves, so
two unrelated sources can land in the same place — same grid, same rotated
palette, same three motifs. The agencies run similarity matching on submission,
and a batch flagged against something already in the library is rejected as a
batch. The penalty lands on the contributor account rather than on the file,
which is exactly the thing this project is careful about everywhere else.

The comparison is on the rendered preview rather than the spec, because a
picture is what an agency's matcher actually sees. It is a perceptual hash and
a Hamming distance — cheap enough to run every design against every previous
one without noticing, and blunt enough that it only ever says "these two look
alike", which is all it is asked.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("stockforge.duplicates")


# A 64-bit hash — the default used for grouping source images — is far too
# coarse for this. Measured over real finished pages: four genuinely different
# designs came out 6 to 17 bits apart while a true duplicate was 0, so the two
# bands overlap and no threshold separates them. At 256 bits the same four sit
# 40 to 74 apart, a re-encoded copy at 0 and a recolour at 4. The extra
# resolution is what makes the question answerable at all.
HASH_SIZE = 64
HASH_KEEP = 16
HASH_BITS = HASH_KEEP * HASH_KEEP


def fingerprint(image: Path) -> tuple[str, float] | None:
    """A perceptual hash of a rendered page, and its aspect. None if unreadable."""
    import cv2

    from .ingest import phash

    img = cv2.imread(str(image), cv2.IMREAD_COLOR)
    if img is None:
        log.warning("could not fingerprint %s", image)
        return None
    h, w = img.shape[:2]
    return phash(img, size=HASH_SIZE, keep=HASH_KEEP), round(w / h, 4)


def distance(a: str, b: str) -> int:
    """How many bits differ. Same length assumed; they always are."""
    return sum(x != y for x, y in zip(a, b)) + abs(len(a) - len(b))


@dataclass
class Twin:
    """Something we already made that this one looks like."""

    design_id: str
    page_name: str
    distance: int

    def line(self) -> str:
        return (f"too close to {self.design_id[:12]} ({self.page_name}) — "
                f"{self.distance} of {HASH_BITS} bits apart")


def nearest(phash: str, aspect: float, others: list, max_distance: int,
            aspect_tol: float = 0.04) -> Twin | None:
    """The closest previous page within the limit, or None.

    Aspect is a hard gate, the way it was for grouping: a 5x7 invitation and a
    square social post built from the same artwork are different products, and
    an agency treats them as such. Two pieces have to be the same shape before
    looking alike means anything.
    """
    best: Twin | None = None
    for row in others:
        other_aspect = row["aspect"]
        if other_aspect and abs(other_aspect - aspect) > aspect_tol:
            continue
        gap = distance(phash, row["phash"])
        if gap <= max_distance and (best is None or gap < best.distance):
            best = Twin(design_id=row["design_id"], page_name=row["page_name"],
                        distance=gap)
    return best
