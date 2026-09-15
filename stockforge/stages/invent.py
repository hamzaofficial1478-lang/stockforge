"""Designs written from a brief rather than mixed from a catalogue.

The other way to make something. The mixer takes a grid from one of the
owner's designs, a palette from another and decoration from a third, which is
why it needs twenty-four read in before it can start — a bag of ingredients
with two things in it produces the first thing with its hue nudged.

This path needs none of that. A model writes the design, the renderer draws it,
and the same exporter produces the same files. No reading, no pool, no gate.

What it costs, and it is worth saying plainly because it is the whole trade:
the result is not derived from the owner's catalogue, so it does not carry the
shop's style except as far as the brief describes it. And it is one model call
per design, where the mixer does forty-eight in one. Use it to start a niche
with nothing in it, or to go somewhere the catalogue cannot reach; use the
mixer once there is a catalogue worth mixing.

What is NOT given away here: the drawing. The model writes a layout — boxes,
roles, wording, colour — and every mark on the page is still set in the owner's
own fonts and drawn from the owner's own motif library. Nothing third-party is
embedded, which is what keeps the output submittable.
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from pathlib import Path

from ..providers import VisionProvider, vision
from ..schema import DesignDNA, Page, Provenance, DesignSpec, TextElement
from . import motifs as motifs_stage
from .fonts import load_manifest, match, open_face
from .render import FIT_MARGIN, MM_PER_PX, _guess_width

log = logging.getLogger("stockforge.invent")


class Invented(BaseModel):
    """What the model is asked for, and nothing more.

    Deliberately not a whole DesignSpec. Provenance, confidence and the ids are
    facts about how the design came to exist, and a model asked to fill them in
    will happily declare its own output stock-safe. They are set here, from
    what actually happened.

    It also keeps the schema small enough for a model to read. The full spec
    compacts to several thousand characters of JSON Schema, most of it fields
    the model must not touch.
    """

    dna: DesignDNA
    pages: list[Page] = Field(description="one entry per printed surface")
    notes: str = Field(default="", description="anything you were unsure about")


LIKE_THIS = """

YOU ARE ALSO SHOWN A DESIGN. Work in its spirit — the same mood, the same \
weight of colour, the same kind of hierarchy, the same sort of decoration. \
Then make something else.

Not a copy, and not a near-copy with the words swapped. Different wording, a \
different arrangement, a different centrepiece. Someone who owned the design \
you were shown should want this one too, and should not feel they had already \
bought it. If the one you are shown is a pumpkin card in cream and orange, a \
different pumpkin card in cream and orange is a failure; a cauldron card in \
cream and orange that sits beside it on a shelf is the job."""


SYSTEM = """You are a print designer. You are given a brief and you return one \
design as structured data — not a picture, and not a description. Something \
else draws it.

Read these rules as the constraints of the press you are designing for.

GEOMETRY. Every box is fractions of the page, 0..1, with x,y at its top-left. \
Nothing may run off the page: x + w <= 1 and y + h <= 1. Respect the grid \
margins you set — keep every element inside them unless it is deliberately \
bled to the edge. Text boxes need room: a display line wants at least 0.12 of \
the page height, a line of body text at least 0.05.

TYPE. Three voices at most, and two is usually better — a display face for the \
headline, one text face for everything else, and at most one accent. Give each \
text element a role, the actual wording, and a size_ratio relative to the page. \
A headline sits around 0.10 to 0.16; body text around 0.025 to 0.04. Wording \
that the buyer personalises — names, dates, venues, phone numbers — is marked \
placeholder:true so it can be rewritten per design. Fixed design words are not.

MAKE THE WORDS FIT THE BOX. This is the rule most often got wrong, and when it \
is wrong the line is shrunk to fit and the design is ruined. Letters are about \
half as wide as they are tall, so a line of N characters at size_ratio S needs \
roughly N * S * 0.55 of the page width. Before you write a text element, count \
the characters and check:

    size_ratio <= box.w / (characters * 0.55)

"A WICKED NIGHT" is 14 characters. In a box 0.8 wide that allows a size_ratio \
of about 0.10, not 0.16. If you want it bigger, use fewer words — not a bigger \
number. Set box.h to at least 1.4 * size_ratio so the line has room to sit, and \
give a two-line block twice that.

PUNCTUATION. Keep to what every typeface has: letters, digits, full stops, \
commas, ampersands, hyphens, apostrophes. A bullet, an em-dash, a fancy quote \
or an emoji may be missing from the face this gets set in, and a missing glyph \
sends the design to a human. Separate items with a hyphen or start a new line.

DECORATION. You will be given the list of drawings actually available. Use \
those, described in plain words the way you would ask an illustrator — "a \
carved pumpkin, lit from within". Anything you ask for that is not in the list \
comes out as an empty space on the page and sends the design to a human, so \
design with what is there. A design with two motifs that exist beats one with \
six that do not. If the list is empty, use none at all and carry the design on \
type, colour and shape.

COLOUR. Give every swatch a role and a hex. A design is a handful of chosen \
colours — paper, ink, one or two accents — not a gradient of fifteen. The \
background and the ink must be far enough apart to read at arm's length.

NEVER use a raster element. There is no photograph to place: every mark on \
this page is going to be drawn as vector. A raster here produces an empty box.

Make it a design somebody would buy, not a demonstration of the schema. \
Hierarchy first: one thing should be clearly the loudest, one clearly the \
quietest, and the eye should know where to go."""


@dataclass
class Brief:
    """What to make. Everything optional but the niche, because a brief that
    has to be filled in completely is a form, and nobody fills in a form
    forty-eight times."""

    niche: str
    category: str = "invitation"
    occasion: str = ""
    style: str = ""
    trim: str = "5x7in"
    surfaces: int = 1
    wording: str = ""
    avoid: list[str] = field(default_factory=list)

    # What the library can actually draw, filled in by `invent`. Held on the
    # brief rather than passed separately because it belongs to the request:
    # "make me a Halloween card" and "make me one out of THESE drawings" are
    # different asks, and only the second one can be drawn.
    available: list[str] = field(default_factory=list)
    # A design to work in the spirit of, as a picture. The owner's ask, in
    # their words: "i added design like a photo or a link, program sync that
    # and made me new design on the bases of" it. With this set the model is
    # shown the picture and writes something in the same voice; without it, it
    # works from the words alone.
    like: Path | None = None
    # The same thing in words, for when the model cannot be shown a picture.
    #
    # A local web bridge answers text in twelve seconds and does not answer
    # image calls at all — measured, including a 512px picture with a one-line
    # question, which timed out at five minutes. So on the free path there is
    # no way to hand the model an inspiration design directly.
    #
    # There is a way round it that costs one paste. Gemini's own web page takes
    # pictures perfectly well; describe the design there, bring the description
    # back, and it goes in here. One manual step per inspiration, not per
    # design — the twelve designs that come out of it are automatic.
    inspired_by: str = ""

    def as_prompt(self) -> str:
        lines = [f"Niche: {self.niche}",
                 f"What it is: {self.category}",
                 f"Trim: {self.trim}",
                 f"Printed surfaces: {self.surfaces}"]
        if self.occasion:
            lines.append(f"Occasion: {self.occasion}")
        if self.style:
            lines.append(f"Style: {self.style}")
        if self.wording:
            lines.append(f"Wording to work in: {self.wording}")
        if self.inspired_by:
            lines.append("A design to work in the spirit of, described:\n"
                         + self.inspired_by.strip()
                         + "\n\nWork in that spirit and then make something else — "
                           "not that design with the words swapped.")
        if self.avoid:
            # What the shop already has. Naming it is cheaper than discovering
            # a repeat after it is drawn, and far cheaper than shipping one.
            lines.append("Already made, so do something else: "
                         + "; ".join(self.avoid[:12]))
        # The single most useful line in the prompt. Without it the model asks
        # for whatever a designer would want and most of it cannot be drawn:
        # every invented design in the first run went to review with "no
        # library match for a sprig of eucalyptus". The model cannot be
        # expected to guess what is in a folder it has never seen.
        if self.available:
            lines.append("Drawings available to you, and ONLY these: "
                         + "; ".join(self.available[:60]))
        else:
            lines.append("There are no drawings available. Use no motifs at "
                         "all — carry the design on type, colour and shape.")
        return "\n".join(lines)


def available_motifs(motifs_dir: Path, cap: int = 60) -> list[str]:
    """How the library would describe itself to somebody choosing from it.

    The description where there is one, the name otherwise, because that is
    what the matcher compares against — asking for a motif in the words the
    library already uses is the difference between a design that draws and a
    design full of holes.
    """
    out: list[str] = []
    for entry in motifs_stage.load(motifs_dir):
        said = (entry.description or entry.name or entry.library_id).strip()
        if said and said not in out:
            out.append(said)
    return out[:cap]


def fit_type(spec: DesignSpec, fonts_dir: Path) -> list[str]:
    """Shrink any line that will not fit its box, before it is drawn.

    A model asked to size type by arithmetic gets it wrong, and it is not its
    fault: `size_ratio` is a CAP HEIGHT as a fraction of the canvas HEIGHT, the
    em is that divided by the face's own cap ratio, and the box it has to fit
    is a fraction of the WIDTH. Getting from one to the other needs the page
    aspect and the metrics of a font file nobody has opened yet. Real Gemini,
    given the rule in words and following it correctly, still produced a title
    that had to be shrunk to 43% — because the rule as stated left out the
    aspect and the cap ratio, and a prompt cannot carry a font's metrics.

    So it is measured here instead, with the same face and the same `measure`
    the renderer will use, and the size is lowered until it fits. The renderer
    would shrink it anyway — this only moves that from a surprise, reported to
    a human as "type does not fit its box", to a decision made before drawing.
    The design keeps its hierarchy either way; what it loses is the review.

    Returns what was resized, for the record.
    """
    library = load_manifest(fonts_dir)
    changed: list[str] = []
    for page in spec.pages:
        h = page.canvas.height_mm / MM_PER_PX
        w = page.canvas.width_mm / MM_PER_PX
        for el in page.elements:
            if not isinstance(el, TextElement) or not el.content.strip():
                continue
            entry, _ = match(el.font, library)
            face = open_face(entry, fonts_dir) if entry else None
            box_w = el.box.w * w
            if box_w <= 0:
                continue
            # No matched face is not a reason to skip: it is the case where the
            # renderer draws a generic fallback, and skipping left four designs
            # in a row with the headline off both edges of the page.
            size = (el.size_ratio * h) / (face.cap_ratio if face else 0.70)
            widest = max((face.measure(line, size, el.tracking) if face
                          else _guess_width(line, size, el.tracking)
                          for line in el.content.split("\n")), default=0.0)
            if widest <= box_w * FIT_MARGIN or widest <= 0:
                continue
            shrink = (box_w * FIT_MARGIN) / widest
            was = el.size_ratio
            el.size_ratio = max(0.008, was * shrink)
            # The box was sized for the type it was asked to hold, so it comes
            # down with it — a heading in a box twice its height floats.
            el.box.h = max(el.box.h * shrink, el.size_ratio * 1.4)
            changed.append(f"{el.role.value} {el.content.splitlines()[0][:30]!r} "
                           f"{was:.3f} -> {el.size_ratio:.3f}")
    if changed:
        log.info("fitted %d line(s) to their boxes: %s", len(changed), "; ".join(changed[:3]))
    return changed


# How big a reference needs to be. A listing photo is 1588px and half a
# megabyte, and every byte of it is uploaded again on every retry — a single
# design through a web bridge sent the same picture five times. What the model
# is being asked for is the mood, the hierarchy and the weight of colour, none
# of which needs the fine print on the RSVP line to be legible. Reading a
# design to rebuild it is a different job and keeps its own, larger, limit.
REFERENCE_EDGE = 640


def _reference(picture) -> "Path | None":
    """A copy of the reference small enough to send cheaply.

    Written beside the original rather than over it: the owner pointed at a
    file of theirs and it is not this function's business to modify it.
    """
    import cv2

    path = Path(picture)
    img = cv2.imread(str(path))
    if img is None:
        log.warning("could not read the reference %s — writing from the brief alone",
                    path.name)
        return None
    h, w = img.shape[:2]
    if max(h, w) <= REFERENCE_EDGE:
        return path
    scale = REFERENCE_EDGE / max(h, w)
    small = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))),
                       interpolation=cv2.INTER_AREA)
    out = Path(tempfile.gettempdir()) / f"sf-reference-{path.stem[:24]}.jpg"
    cv2.imwrite(str(out), small, [cv2.IMWRITE_JPEG_QUALITY, 82])
    log.info("reference %s shrunk %dx%d -> %dx%d for sending",
             path.name, w, h, small.shape[1], small.shape[0])
    return out


def invent(brief: Brief, provider: VisionProvider | None = None) -> DesignSpec:
    """One design from one brief.

    No images are sent — there is nothing to look at — so this runs on a text
    model as happily as a vision one, and costs a fraction of a read.
    """
    provider = provider or vision()
    look_at = [_reference(brief.like)] if brief.like and Path(brief.like).is_file() else []
    look_at = [p for p in look_at if p is not None]
    made = provider.structured(
        SYSTEM + (LIKE_THIS if look_at else ""),
        brief.as_prompt() + "\n\nReturn the design.",
        look_at,
        Invented,
    )

    spec = DesignSpec(
        source_asset_id=f"invented:{brief.niche}",
        dna=made.dna,
        pages=made.pages,
        confidence=0.6,
        notes=made.notes,
        # Said on the design itself, because six months from now the only way
        # to tell an invented design from a recovered one is if it says so.
        warnings=["written from a brief rather than read from one of your "
                  "designs, so it carries the shop's style only as far as the "
                  "brief described it"],
        provenance=Provenance(
            built_with="stockforge",
            third_party_suspected=False,
            # Safe on the same grounds a mixed design is: the layout is ours,
            # the type is from our own library and every motif resolves to a
            # drawing we hold. Anything that does NOT resolve is reported as a
            # hole by the renderer and the design goes to review — which is the
            # existing behaviour and the reason this can be set here.
            stock_safe=True,
            reason="written from a brief; type and decoration from our own libraries",
        ),
    )
    log.info("invented a %s for %s: %d surface(s), %d element(s)",
             spec.dna.category, brief.niche, len(spec.pages),
             len(spec.elements()))
    return spec
