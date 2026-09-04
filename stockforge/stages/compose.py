"""Mixing designs, rather than deriving from one.

Your 50/50 idea, built properly — and it is a better mechanism than the
single-source derivation it sits beside, for a simple reason: a piece whose
grid came from one design, palette from another, type from a third and
decoration from a fourth is not a variant of anything. There is no single
original for it to resemble.

The recipe is recorded on every output. Not paperwork — it is how you answer
"where did this come from" in one look, six months from now, when you no longer
remember. And it is what lets you re-run a mix you liked with one ingredient
swapped.

Donors are only ever your own analysed designs. The pool is your catalogue.
"""

from __future__ import annotations

import logging
import random
from dataclasses import asdict, dataclass, field

from ..schema import DesignSpec, MotifElement

log = logging.getLogger("stockforge.compose")


@dataclass
class Recipe:
    """Which design gave which ingredient."""

    base: str                                    # whose layout and text structure
    palette_from: str | None = None
    type_from: str | None = None
    motifs_from: str | None = None
    background_from: str | None = None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        bits = [f"layout {self.base[:8]}"]
        for label, src in (("palette", self.palette_from), ("type", self.type_from),
                           ("motifs", self.motifs_from), ("background", self.background_from)):
            if src and src != self.base:
                bits.append(f"{label} {src[:8]}")
        return " + ".join(bits)


def _identity(spec: DesignSpec) -> str:
    """What makes a design itself.

    Not its asset id. A shop puts the same size chart and the same "instant
    download" banner on every listing it has, and those are usually wide — so
    if one of them is the largest image of a listing, every design in the
    catalogue reports the same asset id. Keyed on that, no design was eligible
    to lend an ingredient to any other and mixing quietly stopped happening,
    which is the mechanism the whole project leans on hardest.
    """
    return spec.design_id or spec.source_asset_id


def _same_family(a: DesignSpec, b: DesignSpec) -> bool:
    """Only mix ingredients that belong together. A Halloween palette on a
    wedding invitation is not a new design, it is a mistake.

    Occasion is what admits a donor. Shared style tags are NOT enough on their
    own — half a catalogue ends up tagged "minimal" or "playful", and a wedding
    card that happens to share a tag with a Halloween card is still the wrong
    place to borrow a palette from. Tags rank donors instead, below.
    """
    return a.dna.occasion.strip().lower() == b.dna.occasion.strip().lower()


def _affinity(a: DesignSpec, b: DesignSpec) -> int:
    """How well two designs sit together, for ordering donors best-first."""
    tags_a = {t.lower() for t in a.dna.style_tags}
    tags_b = {t.lower() for t in b.dna.style_tags}
    score = len(tags_a & tags_b)
    if a.dna.category.strip().lower() == b.dna.category.strip().lower():
        score += 1
    return score


def eligible_donors(target: DesignSpec, pool: list[DesignSpec],
                    strict: bool = True) -> list[DesignSpec]:
    """Your other designs that can lend this one an ingredient, best first."""
    donors = [d for d in pool if _identity(d) != _identity(target)]
    if strict:
        matched = [d for d in donors if _same_family(target, d)]
        if matched:
            return sorted(matched, key=lambda d: -_affinity(target, d))
        log.debug("no same-occasion donors for %r, widening to the whole pool",
                  target.dna.occasion)
    return sorted(donors, key=lambda d: -_affinity(target, d))


def compose(
    base: DesignSpec,
    pool: list[DesignSpec],
    mix: float = 0.5,
    seed: int | None = None,
    strict_family: bool = True,
) -> tuple[DesignSpec, Recipe]:
    """Build a new design from several of your own.

    `mix` is how much comes from elsewhere. At 0 you get the base back. At 0.5
    roughly half the ingredients are borrowed from other designs. At 1.0 every
    ingredient that can be borrowed is.
    """
    rng = random.Random(seed)

    out = base.model_copy(deep=True)
    recipe = Recipe(base=_identity(base))

    donors = eligible_donors(base, pool, strict_family)
    if not donors:
        recipe.notes.append("no donors available — this is the base design unchanged")
        return out, recipe

    def borrow() -> bool:
        return rng.random() < mix

    # --- palette -----------------------------------------------------
    if borrow():
        donor = rng.choice(donors)
        if donor.dna.palette.swatches:
            out.dna.palette = donor.dna.palette.model_copy(deep=True)
            recipe.palette_from = _identity(donor)

    # --- type --------------------------------------------------------
    if borrow():
        donor = rng.choice(donors)
        pairing = donor.dna.type_pairing or [t.font for t in donor.texts()[:2]]
        if pairing:
            out.dna.type_pairing = [f.model_copy(deep=True) for f in pairing]
            # reassign by role so the hierarchy survives the swap
            display, body = pairing[0], pairing[-1]
            for el in out.texts():
                el.font = (display if el.size_ratio > 0.05 else body).model_copy(deep=True)
            recipe.type_from = _identity(donor)

    # --- background --------------------------------------------------
    if borrow():
        donor = rng.choice(donors)
        out.dna.background = donor.dna.background.model_copy(deep=True)
        recipe.background_from = _identity(donor)

    # --- decoration --------------------------------------------------
    if borrow():
        donor = rng.choice(donors)
        donor_motifs = donor.motifs()
        if donor_motifs:
            _swap_motifs(out, donor_motifs)
            out.dna.motif_vocabulary = sorted(
                set(out.dna.motif_vocabulary) | set(donor.dna.motif_vocabulary)
            )
            recipe.motifs_from = _identity(donor)

    # --- grid --------------------------------------------------------
    if borrow():
        donor = rng.choice(donors)
        out.dna.grid = donor.dna.grid.model_copy(deep=True)
        recipe.notes.append(f"grid from {_identity(donor)[:8]}")

    # A mix inherits the most cautious provenance of everything in it. If any
    # ingredient came from a design that was held back, the result is too.
    contributors = [base] + [
        d for d in donors
        if _identity(d) in {recipe.palette_from, recipe.type_from,
                                 recipe.motifs_from, recipe.background_from}
    ]
    if any(c.provenance.stock_safe is not True for c in contributors):
        out.provenance.stock_safe = False
        out.provenance.reason = "an ingredient came from a design that was held back"

    return out, recipe


def _swap_motifs(spec: DesignSpec, donor_motifs: list[MotifElement]) -> None:
    """Keep the base's motif POSITIONS, take the donor's motif CONTENT.

    This is the borrow that changes a design most while breaking it least. The
    composition still works because the boxes are the ones the base design was
    built around; what sits in them is entirely different.
    """
    for page in spec.pages:
        slots = [el for el in page.elements if isinstance(el, MotifElement)]
        for i, slot in enumerate(slots):
            donor = donor_motifs[i % len(donor_motifs)]
            slot.motif = donor.motif
            slot.description = donor.description
            slot.library_id = donor.library_id
            slot.match_score = donor.match_score
            slot.flip_x = donor.flip_x
