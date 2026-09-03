"""Make it a new design, not a copy of an old one.

This is the step that decides whether the output is worth having.

The honest version of what we are doing: the source design tells us a *style*
that sold — this palette temperature, this type hierarchy, this density of
decoration, this kind of motif. We keep the style and build a new piece in it.
That is what a design series is, and it is exactly what stock buyers look for:
eight coordinated Halloween invitations, not one.

The dishonest version — nudge a hue, swap one font, call it new — does not
work. Reviewers see hundreds of these a day, agencies run similarity matching
on submission, and a near-duplicate flagged against something already in the
library gets the whole batch rejected. So we do not aim for "different enough
to get away with". We aim for genuinely a different design that a buyer would
recognise as belonging to the same family.

Four levers, applied in order of how much they change the read of a piece:

  content   new names, dates, venues — cosmetic on its own, necessary anyway
  colour    a real palette move, not a nudge
  type      a different pairing with the same voice
  layout    the one that actually changes the design: rhythm, scale, balance

The distinctiveness check at the end is not a rubber stamp. It scores the
result against the source and says plainly whether more work is needed.
"""

from __future__ import annotations

import colorsys
import logging
import random
from pathlib import Path

from pydantic import BaseModel, Field

from ..providers import VisionProvider, reason
from ..schema import ColourRole, DesignSpec, FontClass, Swatch, TextElement

log = logging.getLogger("stockforge.derive")


# --------------------------------------------------------------------------
# colour
# --------------------------------------------------------------------------

def _hex_to_hls(hex_: str) -> tuple[float, float, float]:
    r, g, b = (int(hex_[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return colorsys.rgb_to_hls(r, g, b)


def _hls_to_hex(h: float, l: float, s: float) -> str:
    r, g, b = colorsys.hls_to_rgb(h % 1.0, min(max(l, 0), 1), min(max(s, 0), 1))
    return "#{:02x}{:02x}{:02x}".format(int(r * 255), int(g * 255), int(b * 255))


def shift_palette(spec: DesignSpec, hue_shift: float, sat_scale: float = 1.0,
                  light_shift: float = 0.0) -> None:
    """Rotate the whole palette together so the relationships survive.

    Shifting each colour independently is what makes a recolour look wrong —
    the accent stops being an accent. Rotating in step keeps the design's
    internal logic and still lands somewhere genuinely different.
    """
    for sw in spec.dna.palette.swatches:
        h, l, s = _hex_to_hls(sw.hex)
        if s < 0.08:                      # near-neutrals only take the lightness move
            sw.hex = _hls_to_hex(h, l + light_shift * 0.4, s)
        else:
            sw.hex = _hls_to_hex(h + hue_shift, l + light_shift, s * sat_scale)


# --------------------------------------------------------------------------
# type
# --------------------------------------------------------------------------

def shift_type(spec: DesignSpec, keep_category: bool = True) -> None:
    """Move the type to a different voice with the same job.

    keep_category holds the broad class — a horror display stays a display, a
    wedding script stays a script — while weight, contrast and width move. Turn
    it off and you get a bigger change, at the risk of losing the piece's
    character entirely.
    """
    swaps = {
        "serif": "slab", "slab": "serif", "sans": "display",
        "display": "sans", "script": "script", "mono": "sans",
        "blackletter": "display",
    }
    for el in spec.texts():
        f = el.font
        el.font = FontClass(
            category=f.category if keep_category else swaps.get(f.category, f.category),
            weight=max(100, min(900, f.weight + random.choice([-200, -100, 100, 200]))),
            contrast={"low": "medium", "medium": "high", "high": "medium"}[f.contrast],
            width=f.width,
            mood=f.mood,
        )


# --------------------------------------------------------------------------
# content
# --------------------------------------------------------------------------

class NewCopy(BaseModel):
    replacements: list[str] = Field(
        description="new text for each placeholder line, in the order given"
    )


COPY_SYSTEM = """You are writing fresh sample copy for a design template.

You will be given the placeholder lines from an existing design. Replace each \
with new sample text of a similar length and the same kind — a name for a name, \
a date for a date, an address for an address. Keep the tone of the piece.

Use obviously generic sample details. Never reuse a real name, address or phone \
number from the original. Keep line lengths close so the layout still balances."""


def rewrite_placeholders(spec: DesignSpec, provider: VisionProvider | None = None) -> None:
    provider = provider or reason()
    slots = [el for el in spec.texts() if el.placeholder]
    if not slots:
        return

    listing = "\n".join(f"{i}. [{el.role.value}] {el.content}" for i, el in enumerate(slots))
    try:
        new = provider.structured(
            COPY_SYSTEM,
            f"Placeholder lines from the design:\n{listing}\n\n"
            f"Return exactly {len(slots)} replacements, in the same order.",
            [],
            NewCopy,
        )
    except Exception as exc:
        log.warning("copy rewrite failed, keeping originals: %s", exc)
        return

    for el, text in zip(slots, new.replacements):
        el.content = text


# --------------------------------------------------------------------------
# layout
# --------------------------------------------------------------------------

def shift_layout(spec: DesignSpec, strength: float = 0.5) -> None:
    """The lever that actually changes how a design reads.

    Margins open or close, the type scale spreads or compresses, decoration
    grows or shrinks against the type. Small numbers, large effect — this is
    what separates a recolour from a new piece.
    """
    g = spec.dna.grid
    g.margin_x = max(0.03, min(0.30, g.margin_x * (1 + 0.5 * strength)))
    g.margin_y = max(0.03, min(0.30, g.margin_y * (1 + 0.5 * strength)))
    g.vertical_rhythm = {"tight": "even", "even": "airy", "airy": "tight"}[g.vertical_rhythm]

    for el in spec.elements():
        if isinstance(el, TextElement):
            # push the hierarchy apart: big type bigger, small type smaller
            factor = 1 + strength * (0.12 if el.size_ratio > 0.06 else -0.10)
            el.size_ratio = max(0.008, min(0.6, el.size_ratio * factor))
            el.tracking = max(-0.1, min(1.0, el.tracking + strength * 0.02))
        else:
            el.box.w = min(1.4, el.box.w * (1 - 0.08 * strength))
            el.box.h = min(1.4, el.box.h * (1 - 0.08 * strength))


# --------------------------------------------------------------------------
# the check
# --------------------------------------------------------------------------

class Distinctiveness(BaseModel):
    distinct: float = Field(ge=0, le=1, description="0 = a copy, 1 = clearly its own design")
    same_family: float = Field(ge=0, le=1, description="does it still read as the same style?")
    verdict: str = Field(description="ship, derive_further, or too_far")
    what_still_reads_as_copied: list[str] = Field(default_factory=list)
    suggestion: str = ""


DISTINCT_SYSTEM = """You are judging whether a new design stands on its own.

The FIRST image is an existing design. The SECOND is a new piece built in the \
same style. The second is meant to belong to the same family — same mood, same \
kind of piece — while being genuinely its own design, the way two invitations \
in one collection differ.

Score two things, and they pull against each other:

  distinct    Would anyone looking at both call the second a copy of the first? \
              Copied composition, identical proportions, the same decorative \
              elements in the same places all push this down. A different \
              palette alone does NOT make something distinct.

  same_family Does it still read as the same style and quality? A change so \
              large that it has lost the character that made the original work \
              is a failure too, not a success.

Verdicts: `ship` when it stands alone and still belongs. `derive_further` when \
it is still too close — say exactly what is still reading as copied. `too_far` \
when the character is gone and the derivation should be dialled back."""


def check(source: Path, derived: Path, provider: VisionProvider | None = None) -> Distinctiveness:
    provider = provider or reason()
    return provider.structured(
        DISTINCT_SYSTEM,
        "First image: the existing design. Second: the new piece. "
        "Does the second stand on its own?",
        [source, derived],
        Distinctiveness,
    )


# --------------------------------------------------------------------------

def derive(spec: DesignSpec, strength: float = 0.5, seed: int | None = None,
           provider: VisionProvider | None = None) -> DesignSpec:
    """Apply all four levers at a given strength. Returns a new spec."""
    if seed is not None:
        random.seed(seed)

    out = spec.model_copy(deep=True)
    rewrite_placeholders(out, provider)
    shift_palette(
        out,
        hue_shift=random.uniform(0.06, 0.18) * (1 if random.random() > 0.5 else -1) * strength * 2,
        sat_scale=1 + random.uniform(-0.15, 0.15) * strength,
        light_shift=random.uniform(-0.06, 0.06) * strength,
    )
    shift_type(out)
    shift_layout(out, strength)
    return out
