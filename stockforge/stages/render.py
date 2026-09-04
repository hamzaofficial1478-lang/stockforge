"""Stage 4 — turn a spec into artwork.

SVG is the intermediate on purpose. It is text, so it diffs cleanly and you can
read a bad render without opening a design app; it maps one-to-one onto layered
PDF; and Illustrator and Inkscape both open it with the layers intact.

Nothing here traces anything. Every mark on the page is drawn from the spec:
geometry from primitives, decoration from our own motif library, type set live
in a font we are allowed to embed.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from xml.sax.saxutils import escape

from ..schema import (
    Box, ColourRole, DesignSpec, MotifElement, Page, ShapeElement, TextElement,
)
from .fonts import FontEntry, load_manifest, match, open_face

MM_PER_PX = 25.4 / 96.0

# Leave a hair of room rather than setting a line flush to the edge of its box.
FIT_MARGIN = 0.98


class RenderResult:
    def __init__(self, svg: str, missing_motifs: list[str], font_scores: list[float],
                 refits: list[tuple[str, float]] | None = None):
        self.svg = svg
        self.missing_motifs = missing_motifs
        self.font_scores = font_scores
        # Lines that had to be set smaller than the spec asked in order to fit
        # their box, as (what it was, how much of the asked-for size survived).
        self.refits = refits or []

    @property
    def worst_font_score(self) -> float:
        return min(self.font_scores) if self.font_scores else 1.0

    @property
    def worst_refit(self) -> float:
        """1.0 when nothing had to shrink. Well under it means the analyser
        read a size the words do not fit into, which is a design fault rather
        than a rendering one — worth a human's eye."""
        return min((scale for _, scale in self.refits), default=1.0)


# --------------------------------------------------------------------------

def _px(box: Box, w: float, h: float) -> tuple[float, float, float, float]:
    return box.x * w, box.y * h, box.w * w, box.h * h


def _rot(el, cx: float, cy: float) -> str:
    r = getattr(el, "rotation", 0.0) or 0.0
    return f' transform="rotate({r:.2f} {cx:.2f} {cy:.2f})"' if r else ""


def _fill(spec: DesignSpec, role: ColourRole | None) -> str:
    return "none" if role is None else spec.dna.palette.get(role, "#000000")


# --------------------------------------------------------------------------

def _background(spec: DesignSpec, w: float, h: float) -> str:
    bg = spec.dna.background
    base = _fill(spec, bg.base)
    if bg.treatment in ("linear-gradient", "radial-gradient") and bg.secondary:
        second = _fill(spec, bg.secondary)
        if bg.treatment == "linear-gradient":
            a = math.radians(bg.angle)
            x2, y2 = 50 + 50 * math.cos(a), 50 + 50 * math.sin(a)
            grad = (f'<linearGradient id="bg" x1="{100 - x2:.1f}%" y1="{100 - y2:.1f}%" '
                    f'x2="{x2:.1f}%" y2="{y2:.1f}%">')
        else:
            grad = '<radialGradient id="bg" cx="50%" cy="50%" r="70%">'
        close = "linearGradient" if bg.treatment == "linear-gradient" else "radialGradient"
        return (f'<defs>{grad}<stop offset="0%" stop-color="{base}"/>'
                f'<stop offset="100%" stop-color="{second}"/></{close}></defs>'
                f'<rect width="{w:.2f}" height="{h:.2f}" fill="url(#bg)"/>')
    return f'<rect width="{w:.2f}" height="{h:.2f}" fill="{base}"/>'


def _shape(spec: DesignSpec, el: ShapeElement, w: float, h: float) -> str:
    x, y, bw, bh = _px(el.box, w, h)
    fill, stroke = _fill(spec, el.fill), _fill(spec, el.stroke)
    sw = el.stroke_ratio * h
    common = f'fill="{fill}" stroke="{stroke}" stroke-width="{sw:.2f}"'
    rot = _rot(el, x + bw / 2, y + bh / 2)

    if el.primitive == "rect":
        r = el.corner_radius * min(bw, bh)
        return f'<rect x="{x:.2f}" y="{y:.2f}" width="{bw:.2f}" height="{bh:.2f}" rx="{r:.2f}" {common}{rot}/>'
    if el.primitive == "ellipse":
        return (f'<ellipse cx="{x + bw / 2:.2f}" cy="{y + bh / 2:.2f}" '
                f'rx="{bw / 2:.2f}" ry="{bh / 2:.2f}" {common}{rot}/>')
    if el.primitive == "line":
        return (f'<line x1="{x:.2f}" y1="{y + bh / 2:.2f}" x2="{x + bw:.2f}" y2="{y + bh / 2:.2f}" '
                f'stroke="{stroke}" stroke-width="{sw:.2f}" stroke-linecap="round"{rot}/>')
    if el.primitive == "arch":
        # the ubiquitous arched panel — a rect with a semicircular top
        r = bw / 2
        return (f'<path d="M {x:.2f} {y + bh:.2f} L {x:.2f} {y + r:.2f} '
                f'A {r:.2f} {r:.2f} 0 0 1 {x + bw:.2f} {y + r:.2f} '
                f'L {x + bw:.2f} {y + bh:.2f} Z" {common}{rot}/>')
    if el.primitive == "polygon":
        n = el.sides or 6
        cx, cy, rx, ry = x + bw / 2, y + bh / 2, bw / 2, bh / 2
        pts = " ".join(
            f"{cx + rx * math.cos(2 * math.pi * i / n - math.pi / 2):.2f},"
            f"{cy + ry * math.sin(2 * math.pi * i / n - math.pi / 2):.2f}"
            for i in range(n)
        )
        return f'<polygon points="{pts}" {common}{rot}/>'
    return ""


_SVG_OPEN = re.compile(r"<svg\b[^>]*>", re.I)
_MOTIF_METADATA = re.compile(r"<!--.*?-->|<(title|desc|metadata)\b[^>]*>.*?</\1>", re.S | re.I)


def _motif_body(raw: str) -> str:
    """The drawing itself — no wrapper, no metadata.

    A motif file carries a title, a description and usually a comment or two.
    Those are for the matcher and for whoever draws the next one; they have no
    business inside a file we deliver.
    """
    if m := _SVG_OPEN.search(raw):
        raw = raw[m.end():].rsplit("</svg>", 1)[0]
    return _MOTIF_METADATA.sub("", raw).strip()


def _motif_file(library_id: str | None, motifs_dir: Path) -> Path | None:
    """A library id names a file in the motif folder and nothing else.

    It arrives on a spec, and a spec has been through a model — the schema we
    hand the analyser includes this field, so it can fill it in with anything
    it likes. Not something to join onto a path unchecked.
    """
    if not library_id:
        return None
    try:
        src = (motifs_dir / f"{library_id}.svg").resolve()
        src.relative_to(motifs_dir.resolve())
    except (ValueError, OSError):
        return None
    return src if src.is_file() else None


def _motif(spec: DesignSpec, el: MotifElement, w: float, h: float, motifs_dir: Path) -> str:
    """Place a motif from our own library. A motif with no match is left out
    and reported — a hole you can see beats a traced blob you cannot."""
    src = _motif_file(el.library_id, motifs_dir)
    if src is None:
        return ""

    x, y, bw, bh = _px(el.box, w, h)
    body = _motif_body(src.read_text())

    # motif files are authored on a 0..100 unit square so placement is trivial
    sx, sy = bw / 100.0, bh / 100.0
    flip = f' transform="translate({bw:.2f} 0) scale(-1 1)"' if el.flip_x else ""
    rot = _rot(el, bw / 2, bh / 2)
    return (
        f'<g transform="translate({x:.2f} {y:.2f})"><g{rot}><g transform="scale({sx:.4f} {sy:.4f})">'
        f'<g{flip} fill="{_fill(spec, el.colour)}">{body}</g></g></g></g>'
    )


def _text(spec: DesignSpec, el: TextElement, w: float, h: float,
          library: list[FontEntry], fonts_dir: Path) -> tuple[str, float, float]:
    """Set one text element. Returns the node, the font match score, and how
    much of the asked-for size survived fitting it to its box."""
    entry, score_ = match(el.font, library)
    family = entry.family if entry else "serif"
    # The weight of the face we actually matched, not the one the analyser
    # asked for. Fontconfig picks the file by family and weight together, so
    # asking for 400 of a family whose bold we matched draws — and measures —
    # a different file from the one we chose.
    weight = entry.weight if entry else el.font.weight
    face = open_face(entry, fonts_dir) if entry else None

    content = el.content
    if el.case == "upper":
        content = content.upper()
    elif el.case == "lower":
        content = content.lower()
    elif el.case == "title":
        content = content.title()

    x, y, bw, bh = _px(el.box, w, h)
    # size_ratio is a cap height. Turning it into an em needs the face's own
    # cap ratio; 0.70 is only the fallback for when we have no file to ask.
    size = (el.size_ratio * h) / (face.cap_ratio if face else 0.70)

    lines = content.split("\n")
    refit = 1.0
    if face and bw > 0:
        widest = max((face.measure(line, size, el.tracking) for line in lines),
                     default=0.0)
        if widest > bw * FIT_MARGIN:
            # Set it smaller rather than letting it run off the page. Shrinking
            # keeps the design's structure; rewrapping would change what the
            # analyser read, and overflowing is simply broken.
            refit = (bw * FIT_MARGIN) / widest
            size *= refit

    anchor = {"left": "start", "center": "middle", "right": "end"}.get(el.align, "middle")
    tx = x if anchor == "start" else (x + bw if anchor == "end" else x + bw / 2)

    leading = size * el.line_height
    # vertically centre the block inside its box
    y0 = y + (bh - leading * (len(lines) - 1)) / 2

    spans = "".join(
        f'<tspan x="{tx:.2f}" y="{y0 + i * leading:.2f}">{escape(line)}</tspan>'
        for i, line in enumerate(lines)
    )
    rot = _rot(el, x + bw / 2, y + bh / 2)
    node = (
        f'<text font-family="{escape(family)}" font-size="{size:.2f}" '
        f'font-weight="{weight}" fill="{_fill(spec, el.colour)}" '
        f'letter-spacing="{el.tracking * size:.2f}" text-anchor="{anchor}" '
        f'dominant-baseline="middle"{rot}>{spans}</text>'
    )
    return node, score_, refit


# --------------------------------------------------------------------------

def render(spec: DesignSpec, fonts_dir: Path, motifs_dir: Path,
           page_index: int = 0) -> RenderResult:
    """Render one surface. A greeting card is two calls, a wedding suite five —
    each becomes its own file, which is how a print shop wants them anyway."""
    page: Page = spec.pages[page_index]
    w = page.canvas.width_mm / MM_PER_PX
    h = page.canvas.height_mm / MM_PER_PX
    library = load_manifest(fonts_dir)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{page.canvas.width_mm}mm" '
        f'height="{page.canvas.height_mm}mm" viewBox="0 0 {w:.2f} {h:.2f}">',
        f"<title>{escape(spec.dna.occasion)} {escape(spec.dna.category)} — {escape(page.name)}</title>",
        '<g id="background">', _background(spec, w, h), "</g>",
    ]

    missing: list[str] = []
    scores: list[float] = []
    refits: list[tuple[str, float]] = []

    parts.append('<g id="structure">')
    for el in page.elements:
        if isinstance(el, ShapeElement):
            parts.append(_shape(spec, el, w, h))
    parts.append("</g>")

    parts.append('<g id="decoration">')
    for el in page.elements:
        if isinstance(el, MotifElement):
            node = _motif(spec, el, w, h, motifs_dir)
            if node:
                parts.append(node)
            else:
                missing.append(el.description)
    parts.append("</g>")

    parts.append('<g id="type">')
    for el in page.elements:
        if isinstance(el, TextElement):
            node, s, refit = _text(spec, el, w, h, library, fonts_dir)
            parts.append(node)
            scores.append(s)
            if refit < 1.0:
                first = (el.content.splitlines() or [""])[0]
                refits.append((f"{el.role.value} {first[:40]!r}", refit))
    parts.append("</g></svg>")

    return RenderResult("\n".join(parts), missing, scores, refits)


def write_svg(result: RenderResult, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result.svg)
    return path
