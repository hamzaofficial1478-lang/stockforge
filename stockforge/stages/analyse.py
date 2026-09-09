"""Read a design and write down what it actually is.

Rewritten for local models. A hosted frontier model will fill a hundred-field
nested schema in one shot; a 7B-90B model running on your own card will not,
and pretending otherwise just produces confident rubbish.

So the read is split into five small passes, each with a schema a local model
can actually hit, each building on the last:

  1 survey       what is this, how many printed surfaces, which image is which
  2 palette      k-means does the colours; the model only assigns roles
  3 typography   OCR supplies the strings; the model judges the letterforms
  4 structure    frames, rules, panels, and the decorative elements
  5 provenance   is this design safe to publish, or editable-master only

Five cheap calls beat one impossible one, and when a pass fails you know
exactly which part of the read went wrong.
"""

from __future__ import annotations

import logging
from pathlib import Path
from collections.abc import Callable

import cv2
import numpy as np
from pydantic import BaseModel, Field

from ..providers import VisionProvider, vision
from ..schema import (
    Background, Canvas, ColourRole, DesignDNA, DesignSpec, Element, FontClass,
    Grid, MotifElement, Page, Palette, Provenance, RasterElement, ShapeElement,
    Swatch, TextElement,
)
from . import ocr

log = logging.getLogger("stockforge.analyse")


# --------------------------------------------------------------------------
# pass 1 — survey
# --------------------------------------------------------------------------

class Surface(BaseModel):
    name: str = Field(description="cover, inside, invitation, rsvp, details, back")
    image_index: int = Field(ge=0, description="which of the supplied images shows this surface flat")
    width_mm: float = Field(gt=0)
    height_mm: float = Field(gt=0)


class Survey(BaseModel):
    category: str = Field(description="invitation, greeting card, banner, poster, menu, planner")
    occasion: str = Field(description="wedding, halloween, christmas, birthday, corporate, ...")
    style_tags: list[str] = Field(
        default_factory=list,
        description="the visual language in a few words — botanical, art-deco, "
                    "hand-drawn, minimal, vintage, whimsical")
    surfaces: list[Surface] = Field(description="one entry per printed surface in this design")
    mockup_indices: list[int] = Field(
        default_factory=list,
        description="images that are staged photographs or marketing frames, not flat artwork",
    )
    confidence: float = Field(ge=0, le=1)
    notes: str = ""


SURVEY_SYSTEM = """You are a print production manager sorting through the images \
from one product listing.

A listing usually carries four to six images of the SAME product: one or two \
flat artwork files, and the rest staged photographs, size charts, or marketing \
frames with badges and logos on them. Your job is to sort them out.

Some products have more than one printed surface. A greeting card has a front \
and an inside. A wedding suite might have an invitation, an RSVP card and a \
details card, all sharing one visual language. Report every surface you can \
see, and say which image shows each one flat.

Give real millimetre dimensions for the printed piece, not pixel sizes. Where \
the proportions match a standard trim — 5x7in, A5, A6, 4x6in — use that."""


def survey(images: list[Path], provider: VisionProvider) -> Survey:
    return provider.structured(
        SURVEY_SYSTEM,
        "These images are all from one listing, numbered from 0 in the order given. "
        "Sort them and describe the product.",
        images,
        Survey,
    )


# --------------------------------------------------------------------------
# pass 2 — palette (mostly arithmetic)
# --------------------------------------------------------------------------

class RoleAssignment(BaseModel):
    hex: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    role: ColourRole


class PaletteRead(BaseModel):
    assignments: list[RoleAssignment]
    temperature: str = Field(default="neutral", description="warm, cool or neutral")
    contrast: str = Field(default="medium", description="low, medium or high")


def dominant_colours(path: Path, k: int = 6) -> list[tuple[str, float]]:
    """k-means in Lab, returned as hex with coverage. No model involved —
    a model asked to eyeball a hex value guesses, and this does not."""
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        return []
    # Area averaging blends thin type with its background, inventing lighter
    # ink colors. Sample original pixels instead.
    img = cv2.resize(img, (256, 256), interpolation=cv2.INTER_NEAREST)
    pixels = img.reshape(-1, 3)
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32)
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.5)
    _, labels, centres = cv2.kmeans(lab, k, None, crit, 5, cv2.KMEANS_PP_CENTERS)

    out: list[tuple[str, float]] = []
    total = len(labels)
    for i, centre in enumerate(centres):
        members = pixels[labels.ravel() == i]
        colors, counts = np.unique(members, axis=0, return_counts=True)
        if not len(colors):
            continue
        b, g, r = colors[counts.argmax()]
        coverage = float((labels == i).sum()) / total
        out.append((f"#{r:02x}{g:02x}{b:02x}", coverage))
    return sorted(out, key=lambda c: -c[1])


PALETTE_SYSTEM = """You are assigning roles to colours already sampled from the \
artwork. The hex values are measured and correct — do not change them, only say \
what each one is doing in the design.

background is the ground the piece sits on. ink is the main text colour. \
accent is the colour doing the emotional work — the orange in a Halloween \
piece, the sage in a botanical one. line is for rules and frames. Use \
ink_muted for secondary text and metallic for gold or foil effects.

Not every role needs filling, and a colour may be left unassigned if it is \
just a blend between two others."""


def _rgb(hex_: str) -> tuple[int, int, int]:
    return tuple(int(hex_[i:i + 2], 16) for i in (1, 3, 5))


def _snap(hex_: str, sampled: list[tuple[str, float]]) -> tuple[str, float]:
    """Pull a returned colour back to one that was genuinely measured.

    The prompt tells the model the hex values are measured and must not be
    changed. A local model changes them anyway — a digit, the case, or
    something invented outright — and the coverage was then looked up by exact
    string match, so a drifted hex landed silently with a coverage of zero and
    a colour that is not in the artwork. Snapping keeps both honest.
    """
    if not sampled:
        return hex_.lower(), 0.0
    want = _rgb(hex_.lower())
    nearest, coverage = min(
        sampled, key=lambda s: sum((a - b) ** 2 for a, b in zip(want, _rgb(s[0]))))
    if nearest != hex_.lower():
        log.debug("palette: %s was not measured, snapped to %s", hex_, nearest)
    return nearest, coverage


def palette(flat: Path, provider: VisionProvider) -> Palette:
    sampled = dominant_colours(flat)
    listing = "\n".join(f"  {hex_} covering {cov:.1%} of the canvas" for hex_, cov in sampled)
    read = provider.structured(
        PALETTE_SYSTEM,
        f"Colours measured from this artwork:\n{listing}\n\nAssign each a role.",
        [flat],
        PaletteRead,
    )
    swatches = []
    for a in read.assignments:
        hex_, coverage = _snap(a.hex, sampled)
        swatches.append(Swatch(role=a.role, hex=hex_, coverage=coverage))
    return Palette(
        swatches=swatches or [Swatch(role=ColourRole.BACKGROUND, hex="#ffffff", coverage=1.0)],
        temperature=read.temperature if read.temperature in ("warm", "cool", "neutral") else "neutral",
        contrast=read.contrast if read.contrast in ("low", "medium", "high") else "medium",
    )


# --------------------------------------------------------------------------
# pass 3 — typography
# --------------------------------------------------------------------------

class TypeRead(BaseModel):
    elements: list[TextElement] = Field(
        description="one entry per line of type on this surface")
    grid: Grid
    type_pairing: list[FontClass] = Field(
        default_factory=list,
        description="the two or three letterform descriptions this design pairs, "
                    "display first")


TYPE_SYSTEM = """You are describing the type on one printed surface so it can be \
set again from scratch.

The strings below were read by OCR and are the ground truth for CONTENT and \
rough POSITION. Trust them over your own reading of the image. Never invent \
lines OCR did not see and never drop lines it did.

Each line comes with the confidence OCR had in it. A line read at 95% or better \
is almost certainly exact and you should not second-guess it. Where the \
confidence is lower, look hard at the image and correct it — that is where OCR \
turns a 5 into a 3 or drops a letter out of a place name, and a nearly-right \
address is wrong on something somebody prints.

Your job is everything OCR cannot tell us: the role each line plays in the \
hierarchy, the size relative to the canvas, the tracking, the alignment, and — \
most importantly — a DESCRIPTION of the letterforms. Category, weight, stroke \
contrast, width, mood.

Never name a font. Naming one is useless to us; we match your description \
against our own licensed library. "Heavy dripping horror display, very high \
contrast, playful-macabre" is worth more than any font name you could guess.

Text a buyer would personalise — names, dates, venues, phone numbers — is \
marked placeholder:true. Fixed design text is not."""


def typography(flat: Path, provider: VisionProvider) -> TypeRead:
    lines = ocr.read(flat)
    return provider.structured(
        TYPE_SYSTEM,
        f"OCR read these lines from the artwork:\n{ocr.as_prompt(lines)}\n\n"
        "Describe every one of them as a text element, plus the grid they sit on.",
        [flat],
        TypeRead,
    )


# --------------------------------------------------------------------------
# pass 4 — structure and decoration
# --------------------------------------------------------------------------

class StructureRead(BaseModel):
    background: Background
    shapes: list[ShapeElement] = Field(default_factory=list)
    motifs: list[MotifElement] = Field(default_factory=list)
    rasters: list[RasterElement] = Field(default_factory=list)
    motif_vocabulary: list[str] = Field(
        default_factory=list,
        description="the decorative language this design draws on, as short "
                    "phrases — 'eucalyptus sprig', 'carved pumpkin'. Used to "
                    "build new designs in the same visual family, so name the "
                    "kind of thing rather than the individual drawing")


STRUCTURE_SYSTEM = """You are describing everything on this surface that is not type.

Two different things, and the difference matters:

SHAPES are geometry — frames, rules, panels, arches, blocks of colour, borders. \
These get drawn as clean primitives, so describe them as primitives with boxes.

MOTIFS are pictorial — a pumpkin, a ghost, a sprig of eucalyptus, a bat, a \
cauldron, a flourish. Describe each one precisely enough that an illustrator \
could draw it from your words alone, without ever seeing the original: what it \
is, how it sits, which way it faces, its mood and its style. Say "grinning \
carved jack-o-lantern, three-quarter view, warm light from within, painterly" \
— not "orange shape at lower left".

Ignore anything belonging to the listing rather than the design: shop logos, \
watermarks, "Canva" badges, price stickers, marketing borders.

RASTERS are the parts that are photographic or so richly painted that no \
illustrator could redraw them from words — a photograph, a watercolour wash \
with real brush texture, a generated scene. Do not try to break one into \
shapes and do not describe it as a motif. Report it as a raster with two \
boxes: `box` is where it sits on the finished piece, and `source` is the part \
of the image you are looking at that it occupies. Both are fractions from 0 to \
1. They are usually the same, and differ when the artwork does not fill the \
image. Say what it shows in `description`.

Being honest here costs nothing and hiding it costs everything: a raster is \
kept exactly as it is, so the rebuild stays faithful, and the design is marked \
as one to keep rather than one to sell."""


def structure(flat: Path, provider: VisionProvider) -> StructureRead:
    return provider.structured(
        STRUCTURE_SYSTEM,
        "Describe the background treatment, the drawn geometry, and every "
        "decorative element on this surface.",
        [flat],
        StructureRead,
    )


# --------------------------------------------------------------------------
# pass 5 — provenance
# --------------------------------------------------------------------------

PROVENANCE_SYSTEM = """You are checking whether a design can be submitted to a \
stock agency, or whether it is for the owner's own use only.

Stock agencies require the contributor to hold redistribution rights to every \
element in the file. Redrawing does not launder an element whose composition \
came from someone else's library.

Look for and report honestly:
- a template-tool badge or watermark anywhere in the listing images (Canva, \
  Creative Fabrica, and so on) — a strong signal the design was assembled from \
  a library
- photographic or AI-generated scenes used as the artwork itself
- detailed painted or illustrated characters and clipart, as opposed to simple \
  drawn shapes
- typefaces used as artwork rather than as text

Set third_party_suspected true when the design leans on assembled library \
content. List every raster element that could not be honestly rebuilt as clean \
vector. Be conservative — a false "safe" costs the owner their contributor \
account, a false "unsafe" costs them nothing but a second look."""


def provenance(images: list[Path], provider: VisionProvider) -> Provenance:
    prov = provider.structured(
        PROVENANCE_SYSTEM,
        "Assess these listing images. Can this design be submitted to a stock "
        "agency, or is it for the owner's own use only?",
        images,
        Provenance,
    )
    # The model advises; the rule decides. Never let a model talk the pipeline
    # into publishing something with third-party content in it.
    prov.stock_safe = not (prov.third_party_suspected or bool(prov.raster_elements))
    if not prov.stock_safe and not prov.reason:
        prov.reason = "assembled from third-party or raster elements — editable master only"
    return prov


# --------------------------------------------------------------------------
# put it together
# --------------------------------------------------------------------------

def _hold_back_rasters(pages: list[Page], prov: Provenance,
                       warnings: list[str]) -> None:
    """A design with a photograph placed in it is never stock-safe.

    The structure pass and the provenance pass look for rasters separately and
    either can see one the other missed, so the element being on the page
    settles it whatever the provenance pass concluded. The pixels are the
    owner's own artwork coming back to them, and nobody else's to sell.
    """
    placed = [r.description for page in pages for r in page.elements
              if isinstance(r, RasterElement)]
    if not placed:
        return
    prov.stock_safe = False
    for one in placed:
        if one not in prov.raster_elements:
            prov.raster_elements.append(one)
    if not prov.reason:
        prov.reason = "a photographic area is placed as-is — editable master only"
    warnings.extend(f"raster element: {r}" for r in placed
                    if f"raster element: {r}" not in warnings)


def analyse(
    images: list[Path],
    asset_id: str,
    provider: VisionProvider | None = None,
    design_id: str | None = None,
    listing_url: str | None = None,
    mockups: set[int] | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> DesignSpec:
    """Full read of one design from its listing images.

    `mockups` names the images that ingest recovered from a staged photograph
    rather than a flat export, by index into `images`. They are perfectly
    usable — the perspective is corrected and the colour balanced — but a true
    flat is better evidence, and until now nothing anywhere acted on the
    difference. Both ingest and the survey pass worked out which images were
    staged and both threw the answer away.
    """
    report = on_progress or (lambda step: None)
    provider = provider or vision()
    mockups = set(mockups or ())

    report("Identifying pages — waiting for the vision model")
    sv = survey(images, provider)
    # The model has its own opinion; take both, since either noticing is worth
    # more than neither.
    staged = mockups | {i for i in sv.mockup_indices if 0 <= i < len(images)}
    surfaces = [s for s in sv.surfaces if 0 <= s.image_index < len(images)]
    # The palette is measured off one image, so it should be the best one we
    # have. A colour sampled through tungsten light and a linen tablecloth is
    # the wrong colour however carefully the roles are then assigned.
    chosen = [s.image_index for s in surfaces]
    primary_index = next((i for i in chosen if i not in staged),
                         next((i for i in range(len(images)) if i not in staged),
                              chosen[0] if chosen else 0))
    primary = images[primary_index]

    report("Measuring colors and identifying their roles — waiting for the vision model")
    pal = palette(primary, provider)
    report("Checking artwork provenance — waiting for the vision model")
    prov = provenance(images, provider)

    pages: list[Page] = []
    grid, pairing, background, vocabulary = Grid(), [], Background(), []

    warnings = [f"raster element: {r}" for r in prov.raster_elements]

    for index, surface in enumerate(surfaces, 1):
        flat = images[surface.image_index]
        if surface.image_index in staged:
            warnings.append(
                f"'{surface.name}' was read from a staged photograph, not a flat "
                f"export — the colours and the text are less reliable")

        report(f"Reading text with OCR and matching typography — page {index}/{len(surfaces)}")
        type_read = typography(flat, provider)
        report(f"Reconstructing shapes and artwork — page {index}/{len(surfaces)}; waiting for the vision model")
        struct = structure(flat, provider)

        elements: list[Element] = [*struct.rasters, *struct.shapes,
                                   *struct.motifs, *type_read.elements]
        pages.append(Page(
            name=surface.name,
            canvas=Canvas(width_mm=surface.width_mm, height_mm=surface.height_mm),
            elements=elements,
            source_image=str(flat),
        ))

        if not pages[:-1]:                      # the first surface sets the DNA
            grid, background = type_read.grid, struct.background
            pairing = type_read.type_pairing
        vocabulary.extend(struct.motif_vocabulary)

    dna = DesignDNA(
        category=sv.category,
        occasion=sv.occasion,
        style_tags=sv.style_tags,
        grid=grid,
        background=background,
        palette=pal,
        type_pairing=pairing,
        motif_vocabulary=sorted(set(vocabulary)),
    )

    _hold_back_rasters(pages, prov, warnings)

    return DesignSpec(
        source_asset_id=asset_id,
        design_id=design_id,
        listing_url=listing_url,
        dna=dna,
        pages=pages,
        provenance=prov,
        confidence=sv.confidence,
        notes=sv.notes,
        warnings=warnings,
    )
