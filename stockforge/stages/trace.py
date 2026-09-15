"""Turn a picture of a motif into a drawing the library can use.

The last link in a chain that was otherwise complete. A design asks for a
carved pumpkin; the library has not got one; `motifs.draw` asks an image model
for a picture of one and writes it to `_drawn/` with a note saying

    "Reference only. Trace it to SVG before using it in a design."

and there it stopped, because the library only ever globs `*.svg`. A generated
pumpkin could never become a usable pumpkin. Same for the cutouts `harvest`
takes out of the owner's own artwork.

So this traces. Not with potrace — that is a binary dependency for a project
whose whole shape is "standard library, OpenCV, and nothing to install" — but
with OpenCV, which is already here and is well suited to the job because the
job is deliberately easy. The prompt that generates these asks for

    "Flat vector style, solid colours, clean even outlines, one subject only
     ... no drop shadow, no gradient, no photographic texture"

which is a picture made of a few flat regions. Quantise, find the regions,
follow their edges, write the paths. A photograph would come out as mush; flat
art comes out as flat art.

What this is NOT is a promise of fidelity. It is a redrawing: fewer colours,
simplified edges, small specks dropped. That is the right trade for something
that will be printed at 40mm beside a line of type, and it is the honest
description of what a trace is.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("stockforge.trace")

# The same seed the critique stage uses, and for the same reason: k-means picks
# its starting centres at random, so without this the same picture traces to a
# different drawing every run and nothing about a motif is reproducible.
KMEANS_SEED = 20240921

# Below this share of the picture a region is a speck — a stray pixel, a JPEG
# artefact, the dot of an anti-aliased edge — and carrying it into the drawing
# adds a path nobody will ever see at print size.
SPECK = 0.0004


@dataclass
class Traced:
    """One drawing, and what it cost to make it."""

    path: Path
    source: Path
    paths: int = 0
    colours: list[str] = field(default_factory=list)
    note: str = ""


def _foreground(img: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The picture, and a mask of what is actually drawn on it.

    Alpha where there is alpha — the harvested cutouts carry it, and it is
    exact. Otherwise the paper is whatever the corners agree on, which is how
    you find the background of a generated image told to sit on plain white
    without assuming it obeyed.
    """
    if img.shape[2] == 4:
        bgr = img[:, :, :3]
        return bgr, (img[:, :, 3] > 128).astype(np.uint8)

    bgr = img
    h, w = bgr.shape[:2]
    patch = max(2, min(h, w) // 40)
    corners = np.concatenate([
        bgr[:patch, :patch].reshape(-1, 3), bgr[:patch, -patch:].reshape(-1, 3),
        bgr[-patch:, :patch].reshape(-1, 3), bgr[-patch:, -patch:].reshape(-1, 3),
    ])
    paper = np.median(corners, axis=0)
    # Generous, because a "white" background out of an image model is never one
    # value — it is a wash of near-whites with the compression noise on top.
    away = np.linalg.norm(bgr.astype(np.float32) - paper, axis=2)
    mask = (away > 26).astype(np.uint8)
    # Close the pinholes an anti-aliased edge leaves, then drop the dust.
    k = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
    return bgr, mask


def _quantise(bgr: np.ndarray, mask: np.ndarray, colours: int) -> tuple[np.ndarray, list[tuple]]:
    """Reduce the drawn part to a few flat colours. Returns labels and centres."""
    pixels = bgr[mask > 0].astype(np.float32)
    if len(pixels) < colours:
        return np.zeros(mask.shape, np.int32), []
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 40, 0.5)
    cv2.setRNGSeed(KMEANS_SEED)
    _, labels, centres = cv2.kmeans(pixels, colours, None, crit, 6, cv2.KMEANS_PP_CENTERS)
    full = np.full(mask.shape, -1, np.int32)
    full[mask > 0] = labels.ravel()
    return full, [tuple(int(v) for v in c) for c in centres]


def _curve(points: np.ndarray) -> str:
    """A closed path through these points, as cubic curves rather than corners.

    approxPolyDP alone gives straight segments, and a pumpkin traced into forty
    straight segments reads as a pumpkin drawn by a committee — the first trace
    of one came out visibly faceted all round its body. Catmull-Rom through the
    same points, converted to the cubics SVG actually speaks, follows the same
    outline with the same handful of points and arrives smooth.

    The tangent at each point is the direction from its neighbour before to its
    neighbour after, which is what makes the curve continuous where the
    segments meet.
    """
    n = len(points)
    bits = [f"M{points[0][0]:.1f} {points[0][1]:.1f}"]
    for i in range(n):
        p0 = points[(i - 1) % n]
        p1, p2 = points[i], points[(i + 1) % n]
        p3 = points[(i + 2) % n]
        c1 = p1 + (p2 - p0) / 6.0
        c2 = p2 - (p3 - p1) / 6.0
        bits.append(f"C{c1[0]:.1f} {c1[1]:.1f} {c2[0]:.1f} {c2[1]:.1f} "
                    f"{p2[0]:.1f} {p2[1]:.1f}")
    bits.append("Z")
    return "".join(bits)


def _paths_for(region: np.ndarray, smooth: float) -> list[str]:
    """SVG path data for one flat-coloured region, holes included.

    RETR_CCOMP gives outers and their holes in one pass; both are emitted into
    a single path and filled even-odd, which is what makes the middle of an O
    the colour of the paper rather than the colour of the O.

    The region is grown by a pixel first. Each colour is traced separately, so
    the anti-aliased pixels along a boundary belong to neither side and the
    first trace of a pumpkin had a white seam around its eyes and mouth.
    Overlapping by a pixel closes them, and costs nothing because the regions
    are painted largest first — a neighbour's overspill ends up underneath.
    """
    region = cv2.dilate(region, np.ones((3, 3), np.uint8), iterations=1)
    found, _hierarchy = cv2.findContours(region, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if not found:
        return []
    total = float(region.shape[0] * region.shape[1])
    out: list[str] = []
    for contour in found:
        if cv2.contourArea(contour) < total * SPECK:
            continue
        # The same test as for a whole colour, asked of one piece of it. A
        # colour can be a real shape AND have stray rims: the green of a
        # pumpkin's stalk also picked up the soft pixels around its eyes, and
        # because the stalk itself is solid the colour passed — then painted
        # green rims around both eyes and the mouth. The stalk has an inside.
        # The rims do not.
        piece = np.zeros(region.shape, np.uint8)
        cv2.drawContours(piece, [contour], -1, 255, -1)
        if _is_only_an_edge(piece):
            continue
        eps = smooth * cv2.arcLength(contour, True)
        points = cv2.approxPolyDP(contour, eps, True).reshape(-1, 2).astype(np.float64)
        if len(points) < 3:
            continue
        out.append(_curve(points))
    return out


def _is_only_an_edge(region: np.ndarray, keep: float = 0.16) -> bool:
    """Is this colour a shape, or the soft rim between two other shapes?

    A shape has an inside: erode it and most of it is still there. A rim is a
    few pixels wide everywhere, so erosion all but erases it. Measuring the
    inside is what separates them, and it needs no threshold on colour — it
    works the same on a light halo and a dark one.
    """
    inside = cv2.erode(region, np.ones((5, 5), np.uint8), iterations=1)
    before = float((region > 0).sum())
    if before <= 0:
        return True
    return float((inside > 0).sum()) / before < keep


def trace(source: Path, out: Path, *, colours: int = 5, smooth: float = 0.0025,
          kind: str = "", name: str = "", description: str = "",
          tags: list[str] | None = None, stretch: bool = False,
          provenance: dict | None = None) -> Traced:
    """Trace one picture into a library motif.

    `colours` is how many flat colours to allow. Five suits the flat artwork
    this is for; a busier picture wants more and will trace to more paths.
    `smooth` is how far a traced edge may stray from the pixels, as a fraction
    of the outline's own length — bigger is simpler and blockier.
    """
    img = cv2.imread(str(source), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError(f"unreadable image: {source}")
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

    bgr, mask = _foreground(img)
    h, w = mask.shape
    if not mask.any():
        raise ValueError(f"nothing but background in {source.name} — "
                         f"the whole picture is one colour")

    labels, centres = _quantise(bgr, mask, colours)
    if not centres:
        raise ValueError(f"too little drawn in {source.name} to trace")

    # Back to front: the biggest region is the ground the rest sits on, and an
    # SVG is painted in document order. Emitted the other way round, a pumpkin's
    # body would be drawn over its own face.
    order = sorted(range(len(centres)),
                   key=lambda i: -int((labels == i).sum()))

    body: list[str] = []
    used: list[str] = []
    count = 0
    for i in order:
        region = ((labels == i) * 255).astype(np.uint8)
        share = float(region.any(axis=None) and (region > 0).mean())
        if share < SPECK:
            continue
        if _is_only_an_edge(region):
            # A colour k-means spent on the soft pixels along a boundary rather
            # than on anything anybody drew. Traced, it comes back as a halo
            # around the shape it borders — the first pumpkin had a pale ring
            # right round its body, and it is the single ugliest thing a
            # tracer can produce. It has no inside, so it is not a shape.
            log.debug("dropping colour %d — it is an edge, not a shape", i)
            continue
        data = _paths_for(region, smooth)
        if not data:
            continue
        b, g, r = centres[i]
        hexc = f"#{r:02x}{g:02x}{b:02x}"
        used.append(hexc)
        count += len(data)
        body.append(f'  <path fill="{hexc}" fill-rule="evenodd" d="{"".join(data)}"/>')

    if not body:
        raise ValueError(f"nothing traceable in {source.name}")

    head = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}"']
    if kind:
        head.append(f' data-kind="{_clean(kind)}"')
    if tags:
        head.append(f' data-tags="{_clean(", ".join(tags))}"')
    if stretch:
        head.append(' data-stretch="true"')
    head.append(">")
    parts = ["".join(head)]
    if name:
        parts.append(f"  <title>{_clean(name)}</title>")
    if description:
        parts.append(f"  <desc>{_clean(description)}</desc>")
    if provenance:
        # Beside the drawing, not in a file next to it, because a file next to
        # it gets separated from it the first time somebody tidies up. Six
        # months on, "did I draw this or did a model?" is a question about
        # whether this may be sold.
        parts.append("  <!-- " + _clean(json.dumps(provenance, sort_keys=True)) + " -->")
    parts.extend(body)
    parts.append("</svg>")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(parts) + "\n", encoding="utf-8")
    note = "" if count <= 400 else "heavy"
    log.info("traced %s -> %s (%d path(s), %d colour(s))",
             source.name, out.name, count, len(used))
    return Traced(path=out, source=source, paths=count, colours=used, note=note)


def _clean(text: str) -> str:
    """Anything going into the SVG. These files are read back by a regex and
    handed to a renderer, so a description with a bracket in it must not be
    able to close a tag or open a comment."""
    text = re.sub(r"[<>&]", " ", str(text))
    return re.sub(r"--+", "-", text).strip()
