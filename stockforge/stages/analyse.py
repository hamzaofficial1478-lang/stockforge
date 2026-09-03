"""Stage 3 — read a design and write down what it actually is.

One model call per design family. This is where the money goes and where the
quality is decided, so it runs on the strongest model at high effort. A weak
read here costs far more in critique rounds and human review than it ever saves
in tokens.

The doctrine, which lives in the system prompt below and matters more than any
code in this repo: we are not describing pixels so they can be reproduced. We
are recovering the *design decisions* behind the artwork so they can be made
again, properly, with our own type and our own drawn elements.
"""

from __future__ import annotations

import base64
from pathlib import Path

import anthropic
from pydantic import BaseModel, Field

from ..config import settings
from ..pricing import cost_usd, usage_dict
from ..schema import Canvas, DesignDNA, Element


class SpecDraft(BaseModel):
    """What the model returns. Bookkeeping fields are added by us afterwards —
    never ask a model to invent an ID."""

    canvas: Canvas
    dna: DesignDNA
    elements: list[Element] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    notes: str = ""
    warnings: list[str] = Field(default_factory=list)


SYSTEM = """You are a senior print designer reverse-engineering a piece of artwork \
so that it can be rebuilt from scratch — cleanly, in vector, by a program.

You are NOT tracing. You are NOT transcribing pixels. You are recovering the \
design decisions a designer made, so that those same decisions can be made \
again with our own type and our own drawn elements. Think of it as reading a \
finished dish and writing the recipe, not photographing the plate.

Work through the image in this order and commit to an answer at each step:

1. FORMAT. What physical piece is this? A 5x7 invitation, an A4 poster, a 4x6 \
   flat card, a web banner? Give real millimetre dimensions for the printed \
   piece, not the pixel size of the file. Where the aspect matches a standard \
   trim, use that standard.

2. THE GRAMMAR (dna). What is the underlying system? The margins, whether it is \
   centred or asymmetric, how airy the vertical rhythm is. The palette, given as \
   ROLES (background, ink, accent, ...) with hex values sampled from the flat \
   artwork itself. The occasion and category. The style in a handful of tags.

3. TYPE. For every piece of text: its role in the hierarchy, its exact content, \
   its box, and — critically — a DESCRIPTION of the letterforms rather than a \
   guess at the font name. Category, weight, stroke contrast, width, and the \
   mood it carries. We will match this against our own licensed library. Naming \
   a specific commercial font is useless to us; describing it precisely is not. \
   Size is given as cap height over canvas height so it scales to any output.

4. STRUCTURE. Frames, rules, panels, arches, blocks of colour. These are drawn \
   geometry — describe them as primitives with boxes, never as artwork.

5. DECORATION. Botanicals, florals, seasonal motifs, flourishes. Describe each \
   in plain English precisely enough that an illustrator could draw it without \
   seeing the original: what it is, how it sits, which way it faces, how much \
   of the canvas it covers. Do not attempt to describe individual paths.

Rules that are not negotiable:
- Every geometric value is normalised to 0..1 of the canvas.
- Colours are referenced by role everywhere except the palette itself.
- If text is clearly a placeholder for the buyer's own details (names, dates, \
  venue), mark it placeholder:true. If it is fixed design text, do not.
- Be conservative with `confidence`. If the image is low resolution, has a \
  mockup shadow across it, or contains a motif you cannot describe well, say so \
  in `notes` and lower the score. A flagged design goes to a human, which is \
  cheap. A confidently wrong design ships, which is not.
- Put anything a rebuild might get wrong into `warnings` — an unusual fold, a \
  foil or emboss effect, a photographic element, transparency, a die-cut edge.
"""


def _image_block(path: Path) -> dict:
    media = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media,
            "data": base64.standard_b64encode(path.read_bytes()).decode(),
        },
    }


def analyse(
    flat_paths: list[Path],
    client: anthropic.Anthropic | None = None,
) -> tuple[SpecDraft, dict, float]:
    """Read one design. Pass several images of the same design (a flat plus a
    detail shot) and the model gets a better look at the small type."""

    client = client or anthropic.Anthropic()

    content: list[dict] = [_image_block(p) for p in flat_paths[:4]]
    content.append({
        "type": "text",
        "text": (
            "Read this design and return its specification. "
            "Where several images are given they are the same design — use them together."
        ),
    })

    response = client.messages.parse(
        model=settings.model,
        max_tokens=settings.max_tokens,
        thinking={"type": "adaptive"},
        output_config={"effort": settings.effort},
        # The doctrine never changes between calls, so it caches cleanly and we
        # pay ~10% for it on every design after the first.
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": content}],
        output_format=SpecDraft,
    )

    draft = response.parsed_output
    return draft, usage_dict(response.usage), cost_usd(settings.model, response.usage)
