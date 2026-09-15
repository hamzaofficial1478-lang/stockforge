"""Is a drawing struck through the type?

The renderer places type and decoration from the same spec and neither knows
the other is there. Nothing downstream noticed: four invited-to-a-party cards
came out with the headline sliced by an arch and a spider's web drawn across
the opening line, and all four were filed `ready` — files exported, PDF
written, nothing queued. The critic that would have caught it needs a model
that can look at a picture, and in a bridge-only setup there isn't one.

So measure it instead. The SVG comes out in layers — background, artwork,
structure, decoration, type — so the drawings and the words can be rasterised
apart and their ink intersected. That is not an estimate of what the page
looks like; it is what the page looks like, at the resolution asked for.

WHAT COUNTS AS A FAULT. Not any overlap. Type set on a filled banner or a
colour block is a normal thing to do, and there the drawing is under every
letter — whether it reads is a question about colour, which is not this. Type
crossed by a line is the fault, and there the drawing takes a slice: a stroke
through two letters of twenty. So the band between is what gets flagged, and
ink covered nearly end to end is left alone as a ground.

A PLACED PICTURE IS A GROUND, whatever share of a line it covers, and it is
not judged here at all. A photographic vignette and a painted object adopted
into the library are both areas rather than strokes: setting type across the
edge of one is a thing designers do on purpose, and it is what the analyser
reads back off a listing the owner already sells. Judged, this check sent a
design with a small photo in it and a design carrying a big painted ghost
straight to review — both of them ordinary work, and the tests that said so
were written long before this existed and were right.

Measured on the four cards above, as a fraction of each line's own ink:

    0.0%  0.0%  0.0%      clear of everything
    0.6%  0.8%             a serif kissing an arch leg
    2.4%                   a web strand struck through the B of BY ORDER
    7.5%  8.3%  8.5% 10.3% a leg through the last letter, a ghost behind it
    16.6% 30.5% 39.8%      the line is wrecked

A twenty-character line is about five percent of its ink per letter, so two
percent is half a letter struck through. Below that is a serif touching
something and the antialiasing either side of it.
"""
from __future__ import annotations

import logging
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# Half a letter of a twenty-character line, struck through — see above.
STRUCK = 0.02
# Covered nearly end to end is a ground to set type on, not a strike.
GROUND = 0.90
# Wide enough to resolve a hairline rule at card size, small enough that two
# of these per page is not felt. A 1pt rule on a 5x7 card is about one pixel.
WIDTH = 500
# An alpha this low is the feathered edge of a stroke rather than the stroke.
OPAQUE = 40

DRAWN = ("artwork", "structure", "decoration")
# A placed picture is an area under the words, not a stroke through them — see
# the note above. Dropped before the drawn layer is rasterised rather than by
# leaving its layer out, because `artwork` and `decoration` each carry both
# kinds: a motif in this library may be a drawing or a picture, and only one of
# those can be a drawing through the type.
# Non-greedy on purpose: two pictures on a page and a greedy match eats every
# drawn mark between them. DOTALL is insurance rather than a requirement —
# the renderer puts each <image> on one line today, and if that ever stops
# being true the match would fall short and leave the page malformed.
_PICTURE = re.compile(r"<image\b.*?(?:/>|</image>)", re.S | re.I)
# The renderer's own five layers by name, not any group with a lowercase id: a
# motif file is inlined into the page whole, and a drawing that came with its
# own <g id="art"> would otherwise be switched off along with the layers —
# which loses marks from the very picture being measured.
LAYERS = ("background", "artwork", "structure", "decoration", "type")
_GROUP = re.compile(r'<g id="(%s)">' % "|".join(LAYERS))
_TAG = re.compile(r"<(?!/)")


@dataclass
class Struck:
    """One line of type with a drawing through it."""
    label: str
    share: float

    def line(self) -> str:
        return f"{self.label} has a drawing through {self.share:.0%} of it"


def _drew_anything(svg: str, groups: tuple[str, ...]) -> bool:
    """Is there a mark in any of these layers?

    The renderer opens all five groups on every page whether or not it has
    anything to put in them, so the tag being present says nothing. Asked that
    way, a card carrying type and no decoration at all still paid for two
    rasterisations — which over a catalogue is the whole cost of this check
    spent on pages that cannot have the fault.
    """
    for group in groups:
        start = svg.find(f'<g id="{group}">')
        if start < 0:
            continue
        # An empty group is a layer whose own </g> comes before anything else
        # opens. Walking it to find the matching close would be a character
        # loop over the whole page, three times, for every design.
        after = svg.find(">", start) + 1
        opens = _TAG.search(svg, after)
        closes = svg.find("</g>", after)
        if opens and (closes < 0 or opens.start() < closes):
            return True
    return False


def _only(svg: str, keep: tuple[str, ...]) -> str:
    """The same SVG with every other layer switched off."""
    return _GROUP.sub(
        lambda m: m.group(0) if m.group(1) in keep
        else f'<g id="{m.group(1)}" style="display:none">', svg)


def _ink(svg: str, into: Path, name: str) -> "object | None":
    """Where this layer put ink, as a mask.

    Alpha, not darkness: a pale motif on pale paper is still a mark on the
    page, and thresholding on brightness would call it blank. The background
    layer is off in every call here, so anything opaque was drawn.
    """
    import cv2
    from .export import svg_to_png

    source = into / f"{name}.svg"
    source.write_text(svg, encoding="utf-8")
    png = svg_to_png(source, into / f"{name}.png", width=WIDTH)
    img = cv2.imread(str(png), cv2.IMREAD_UNCHANGED)
    if img is None:
        return None
    if img.ndim == 3 and img.shape[2] == 4:
        return img[:, :, 3] > OPAQUE
    # A converter that flattened the alpha away. Paper is white because
    # nothing was drawn on it, so anything that is not white is a mark.
    grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    return grey < 250


def struck_type(svg: str, text_ink, viewbox: tuple[float, float]) -> list[Struck]:
    """Which lines have a drawing through them, worst first.

    `text_ink` is what the renderer said about where each line landed, and
    `viewbox` the page in the same units, so a rectangle can be turned into
    pixels. A line the renderer would not place — one that is rotated — is not
    in the list and is not judged here.

    Returns nothing at all when the check cannot run. A page nobody could
    rasterise is a question for the exporter, which already fails the design
    when no file comes out; inventing a layout fault out of a missing
    converter would be a second, wrong answer to it.
    """
    if not text_ink:
        return []
    if not _drew_anything(svg, DRAWN):
        return []

    page_w, page_h = viewbox
    if page_w <= 0 or page_h <= 0:
        return []

    try:
        with tempfile.TemporaryDirectory() as tmp:
            into = Path(tmp)
            drawn = _ink(_PICTURE.sub("", _only(svg, DRAWN)), into, "drawn")
            typed = _ink(_only(svg, ("type",)), into, "type")
    except Exception as exc:                       # pragma: no cover - env
        log.info("could not check for drawings through the type: %s", str(exc)[:200])
        return []
    if drawn is None or typed is None:
        return []

    if drawn.shape != typed.shape:                 # pragma: no cover - env
        log.info("the two layers rasterised to different sizes, %s and %s",
                 drawn.shape, typed.shape)
        return []

    hit = drawn & typed
    if not hit.any():
        return []

    h, w = typed.shape[:2]
    sx, sy = w / page_w, h / page_h
    out: list[Struck] = []
    for label, (x, y, bw, bh) in text_ink:
        x0, y0 = max(0, int(x * sx)), max(0, int(y * sy))
        x1, y1 = min(w, int((x + bw) * sx) + 1), min(h, int((y + bh) * sy) + 1)
        if x1 <= x0 or y1 <= y0:
            continue
        mine = int(typed[y0:y1, x0:x1].sum())
        if mine <= 0:
            continue
        share = int(hit[y0:y1, x0:x1].sum()) / mine
        if STRUCK <= share < GROUND:
            out.append(Struck(label, share))
    return sorted(out, key=lambda s: -s.share)
