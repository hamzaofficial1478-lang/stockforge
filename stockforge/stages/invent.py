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
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from pathlib import Path

from ..providers import VisionProvider, vision
from ..schema import DesignDNA, Page, Provenance, DesignSpec
from . import motifs as motifs_stage

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


def invent(brief: Brief, provider: VisionProvider | None = None) -> DesignSpec:
    """One design from one brief.

    No images are sent — there is nothing to look at — so this runs on a text
    model as happily as a vision one, and costs a fraction of a read.
    """
    provider = provider or vision()
    made = provider.structured(
        SYSTEM,
        brief.as_prompt() + "\n\nReturn the design.",
        [],
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
