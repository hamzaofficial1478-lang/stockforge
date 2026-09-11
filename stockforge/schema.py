"""The design spec — the single most important file in this project.

Everything upstream produces one of these; everything downstream consumes one.
Get the shape right and the rest is plumbing.

Two layers, deliberately separated:

  DesignDNA  — the reusable grammar of a design. Grid, palette relationships,
               type scale, motif vocabulary, mood. This is what we rebuild from.
  Element[]  — one concrete arrangement of that grammar on a canvas.

We never store "the original pixels" or a traced path. We store an
understanding. A rebuild is a fresh interpretation of the DNA, not a copy of
the source, which is the whole point of the project.

All geometry is normalised to 0..1 of the canvas, so a spec renders at any size
without touching the numbers.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


# --------------------------------------------------------------------------
# primitives
# --------------------------------------------------------------------------

class Box(BaseModel):
    """Normalised bounding box. (0,0) is top-left of the canvas."""

    x: float = Field(ge=-0.2, le=1.2)
    y: float = Field(ge=-0.2, le=1.2)
    w: float = Field(gt=0, le=1.4)
    h: float = Field(gt=0, le=1.4)

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


class ColourRole(str, Enum):
    """Colours are referenced by role, never by literal hex, so a whole design
    can be recoloured by swapping one palette."""

    BACKGROUND = "background"
    SURFACE = "surface"
    INK = "ink"
    INK_MUTED = "ink_muted"
    ACCENT = "accent"
    ACCENT_ALT = "accent_alt"
    METALLIC = "metallic"
    LINE = "line"


class Swatch(BaseModel):
    role: ColourRole
    hex: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    coverage: float = Field(ge=0, le=1, description="fraction of canvas area")

    @field_validator("hex")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.lower()


class Palette(BaseModel):
    swatches: list[Swatch]
    temperature: Literal["warm", "cool", "neutral"] = "neutral"
    contrast: Literal["low", "medium", "high"] = "medium"

    def get(self, role: ColourRole, fallback: str = "#000000") -> str:
        for s in self.swatches:
            if s.role == role:
                return s.hex
        return fallback


# --------------------------------------------------------------------------
# type
# --------------------------------------------------------------------------

class TypeRole(str, Enum):
    EYEBROW = "eyebrow"       # small line above the title — "together with their families"
    TITLE = "title"           # the names, the big statement
    SUBTITLE = "subtitle"
    BODY = "body"             # date, venue, the meat
    DETAIL = "detail"         # rsvp, dress code, small print
    SIGNOFF = "signoff"       # a closing flourish line
    ORNAMENTAL = "ornamental"  # type used as decoration (a monogram, a big numeral)


class FontClass(BaseModel):
    """We describe the *shape* of the type, never the original font's name.

    The matcher scores our own licensed library against this description. That
    is a deliberate constraint, not a limitation: it keeps every output
    redistributable and it is what stops the tool becoming a font laundering
    machine.
    """

    category: Literal["serif", "sans", "script", "display", "slab", "mono", "blackletter"]
    weight: int = Field(ge=100, le=900, description="nearest CSS weight")
    contrast: Literal["low", "medium", "high"] = "medium"
    width: Literal["condensed", "normal", "extended"] = "normal"
    italic: bool = False
    mood: list[str] = Field(default_factory=list, description="e.g. elegant, playful, rustic, modern")


class TextElement(BaseModel):
    kind: Literal["text"] = "text"
    role: TypeRole
    content: str
    box: Box
    font: FontClass
    size_ratio: float = Field(gt=0, le=0.6, description="cap height as a fraction of canvas height")
    tracking: float = Field(default=0.0, ge=-0.1, le=1.0, description="letter-spacing in em")
    line_height: float = Field(default=1.25, ge=0.6, le=3.0)
    align: Literal["left", "center", "right", "justify"] = "center"
    case: Literal["as-is", "upper", "lower", "title"] = "as-is"
    colour: ColourRole = ColourRole.INK
    rotation: float = Field(default=0.0, ge=-180, le=180)
    # editable text is the whole point of the deliverable, so we keep it live
    # right up until export, where stock sites want it outlined
    placeholder: bool = Field(default=False, description="generic sample text, safe to swap")


# --------------------------------------------------------------------------
# non-type elements
# --------------------------------------------------------------------------

class MotifKind(str, Enum):
    BOTANICAL = "botanical"
    FLORAL = "floral"
    FRAME = "frame"
    BORDER = "border"
    RULE = "rule"
    FLOURISH = "flourish"
    GEOMETRIC = "geometric"
    ICON = "icon"
    SEASONAL = "seasonal"     # pumpkin, holly, bunny, heart
    TEXTURE = "texture"


class MotifElement(BaseModel):
    """A decorative element. `description` is what the analyser saw;
    `library_id` is what the matcher picked from our own motif library.

    We never trace the source artwork. If nothing in the library is a decent
    match the element is flagged and it goes to the human queue — that is how
    the library grows."""

    kind: Literal["motif"] = "motif"
    motif: MotifKind
    description: str = Field(description="plain-English description of the shape and its role")
    box: Box
    rotation: float = Field(default=0.0, ge=-180, le=180)
    flip_x: bool = False
    colour: ColourRole = ColourRole.ACCENT
    library_id: str | None = Field(default=None, description="resolved by the motif matcher")
    match_score: float | None = Field(default=None, ge=0, le=1)


class ShapeElement(BaseModel):
    """Honest primitive geometry — the frames, rules and blocks that make up
    most of a card. Drawn, not traced, so the paths are clean."""

    kind: Literal["shape"] = "shape"
    primitive: Literal["rect", "ellipse", "line", "arch", "polygon"]
    box: Box
    fill: ColourRole | None = None
    stroke: ColourRole | None = ColourRole.LINE
    stroke_ratio: float = Field(default=0.002, ge=0, le=0.1, description="stroke width / canvas height")
    corner_radius: float = Field(default=0.0, ge=0, le=0.5)
    rotation: float = Field(default=0.0, ge=-180, le=180)
    sides: int | None = Field(default=None, ge=3, le=24, description="for polygon")


class RasterElement(BaseModel):
    """A photographic or richly painted area that cannot honestly be rebuilt.

    Not traced and not invented: the pixels are cut straight out of the listing
    image this design was read from, so the master is a faithful rebuild you
    can edit around rather than a design with a hole where the photograph was.

    It also makes the design permanently stock-unsafe. That is the point of
    keeping it as its own element type rather than pretending it is a motif —
    the provenance pass can see it, and a design carrying one is delivered to
    you as an editable master and never sent to an agency.
    """

    kind: Literal["raster"] = "raster"
    description: str = Field(description="what the photographic area shows")
    box: Box = Field(description="where it sits on the page, 0..1 of the canvas")
    source: Box = Field(default_factory=lambda: Box(x=0.0, y=0.0, w=1.0, h=1.0),
                        description="the part of the source image to cut, 0..1 of it")
    opacity: float = Field(default=1.0, ge=0, le=1)


Element = TextElement | MotifElement | ShapeElement | RasterElement


# --------------------------------------------------------------------------
# background & layout grammar
# --------------------------------------------------------------------------

class Background(BaseModel):
    treatment: Literal["solid", "linear-gradient", "radial-gradient", "panel", "texture"] = "solid"
    base: ColourRole = ColourRole.BACKGROUND
    secondary: ColourRole | None = None
    angle: float = Field(default=90.0, ge=0, le=360)
    texture_hint: str | None = Field(default=None, description="e.g. watercolour wash, linen, speckle")


class Grid(BaseModel):
    margin_x: float = Field(default=0.08, ge=0, le=0.4)
    margin_y: float = Field(default=0.08, ge=0, le=0.4)
    symmetry: Literal["centred", "left", "right", "asymmetric", "split"] = "centred"
    vertical_rhythm: Literal["tight", "even", "airy"] = "even"


class Canvas(BaseModel):
    width_mm: float = Field(gt=0)
    height_mm: float = Field(gt=0)
    bleed_mm: float = Field(default=3.0, ge=0)

    @property
    def aspect(self) -> float:
        return self.width_mm / self.height_mm


class DesignDNA(BaseModel):
    """The reusable grammar. This is what makes 5k assets tractable: a family of
    forty listings usually shares one DNA and differs only in palette and text.
    """

    category: str = Field(description="invitation, greeting card, banner, poster, menu, ...")
    occasion: str = Field(description="wedding, halloween, christmas, easter, valentines, birthday, ...")
    style_tags: list[str] = Field(default_factory=list, description="minimal, botanical, art-deco, boho, vintage")
    grid: Grid = Field(default_factory=Grid)
    background: Background = Field(default_factory=Background)
    palette: Palette
    type_pairing: list[FontClass] = Field(default_factory=list, description="the 1-3 type voices in play")
    motif_vocabulary: list[str] = Field(default_factory=list, description="recurring decorative themes")


# --------------------------------------------------------------------------
# the spec itself
# --------------------------------------------------------------------------

class Page(BaseModel):
    """One printed surface.

    A greeting card is two pages (front, inside). A wedding suite can be five
    — cover, invitation, RSVP, details, menu. They share one DNA and differ
    only in their elements, which is exactly the relationship the schema needs
    to express: rebuild the suite, not five unrelated files.
    """

    name: str = Field(description="cover, inside, invitation, rsvp, details, back, ...")
    canvas: Canvas
    elements: list[Element] = Field(default_factory=list)
    # Which flat this surface was read from. The survey works it out and it was
    # thrown away, so every check downstream compared every surface against the
    # first image of the listing — the inside of a card judged against a picture
    # of its front. A workspace path, so it only means anything on the machine
    # that did the reading; None when the read predates this being recorded.
    source_image: str | None = Field(default=None)


def _raster_share(elements: list) -> float:
    """How much of a surface is photograph rather than rebuilt artwork, 0..1.

    Overlapping rasters are counted twice and the total is clamped, so this
    errs high. That is the safe direction: it is a check that refuses to call
    something a rebuild, and over-reporting sends a design to a human while
    under-reporting ships a photocopy.
    """
    total = sum(min(1.0, e.box.w) * min(1.0, e.box.h)
                for e in elements if isinstance(e, RasterElement))
    return min(1.0, total)


class Provenance(BaseModel):
    """Where the ingredients came from, and therefore where the output may go.

    This is not paperwork. Stock agencies require you to hold redistribution
    rights to every element in a submitted file, and a design assembled from a
    template library's stock art does not qualify however much of it you
    redraw. Getting this wrong does not cost a rejection — it costs the
    contributor account.

    So the pipeline tracks it per design and refuses to publish anything it
    cannot clear. Designs that fail the check still produce an editable master,
    which is the other half of why this project exists.
    """

    built_with: str | None = Field(default=None, description="canva, illustrator, procreate, unknown")
    raster_elements: list[str] = Field(
        default_factory=list,
        description="photographic or illustrated elements that cannot be rebuilt as clean vector",
    )
    third_party_suspected: bool = Field(
        default=False,
        description="true when the design appears to lean on a template library or bought clipart",
    )
    stock_safe: bool | None = Field(
        default=None,
        description="None = not yet assessed. False = editable master only, never published.",
    )
    reason: str = ""


class DesignSpec(BaseModel):
    schema_version: int = 2
    source_asset_id: str
    design_id: str | None = None
    listing_url: str | None = None

    dna: DesignDNA
    pages: list[Page] = Field(default_factory=list)
    provenance: Provenance = Field(default_factory=Provenance)

    confidence: float = Field(ge=0, le=1, description="the analyser's own read on how well it understood this")
    notes: str = Field(default="", description="anything the analyser was unsure about — feeds the review queue")
    warnings: list[str] = Field(default_factory=list)

    # -- convenience over all pages, or one -------------------------------

    def elements(self, page: int | None = None) -> list[Element]:
        pages = self.pages if page is None else [self.pages[page]]
        return [e for p in pages for e in p.elements]

    def texts(self, page: int | None = None) -> list[TextElement]:
        return [e for e in self.elements(page) if isinstance(e, TextElement)]

    def motifs(self, page: int | None = None) -> list[MotifElement]:
        return [e for e in self.elements(page) if isinstance(e, MotifElement)]

    def unresolved_motifs(self) -> list[MotifElement]:
        return [m for m in self.motifs() if m.library_id is None]

    def rasters(self, page: int | None = None) -> list[RasterElement]:
        return [e for e in self.elements(page) if isinstance(e, RasterElement)]

    def photocopied_pages(self, limit: float = 0.40) -> list[tuple[str, float]]:
        """Surfaces that came back as a picture of the design rather than a
        reading of it.

        This is the failure that looks most like success. The model says the
        whole surface is one photographic area, the renderer faithfully places
        the original pixels, the text is set beside them, and out comes a file
        that is the source image with words next to it. Every other check
        passes, because nothing else asks the one question that matters: did we
        rebuild this, or did we photocopy it?
        """
        out = []
        for page in self.pages:
            share = _raster_share(page.elements)
            if share > limit:
                out.append((page.name, share))
        return out

    @property
    def publishable(self) -> bool:
        return self.provenance.stock_safe is True


class CritiquePatch(BaseModel):
    """One correction the critique stage wants applied. Deliberately narrow —
    a patch names an element and a field, it never hands back a whole new spec.
    That keeps the loop convergent instead of oscillating."""

    page_index: int = Field(default=0)
    element_index: int | None = Field(default=None, description="None means the patch targets the DNA")
    path: str = Field(description="dotted field path, e.g. 'box.y' or 'dna.palette.swatches.0.hex'")
    value: str | float | int | bool
    reason: str


class Critique(BaseModel):
    similarity: float = Field(ge=0, le=1, description="how close the rebuild is to the source's intent")
    polish: float = Field(ge=0, le=1, description="is the rebuild actually *better* than the source")
    verdict: Literal["ship", "patch", "escalate"]
    patches: list[CritiquePatch] = Field(default_factory=list)
    commentary: str = ""
