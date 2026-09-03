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
from pathlib import Path
from xml.sax.saxutils import escape

from ..schema import (
    Box, ColourRole, DesignSpec, MotifElement, ShapeElement, TextElement,
)
from .fonts import FontEntry, load_manifest, match

MM_PER_PX = 25.4 / 96.0


class RenderResult:
    def __init__(self, svg: str, missing_motifs: list[str], font_scores: list[float]):
        self.svg = svg
        self.missing_motifs = missing_motifs
        self.font_scores = font_scores

    @property
    def worst_font_score(self) -> float:
        return min(self.font_scores) if self.font_scores else 1.0


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


def _motif(spec: DesignSpec, el: MotifElement, w: float, h: float, motifs_dir: Path) -> str:
    """Place a motif from our own library. A motif with no match is left out
    and reported — a hole you can see beats a traced blob you cannot."""
    if not el.library_id:
        return ""
    src = motifs_dir / f"{el.library_id}.svg"
    if not src.exists():
        return ""

    x, y, bw, bh = _px(el.box, w, h)
    inner = src.read_text()
    body = inner.split(">", 1)[1].rsplit("</svg>", 1)[0] if "<svg" in inner else inner

    # motif files are authored on a 0..100 unit square so placement is trivial
    sx, sy = bw / 100.0, bh / 100.0
    flip = f' transform="translate({bw:.2f} 0) scale(-1 1)"' if el.flip_x else ""
    rot = _rot(el, bw / 2, bh / 2)
    return (
        f'<g transform="translate({x:.2f} {y:.2f})"><g{rot}><g transform="scale({sx:.4f} {sy:.4f})">'
        f'<g{flip} fill="{_fill(spec, el.colour)}">{body}</g></g></g></g>'
    )


def _text(spec: DesignSpec, el: TextElement, w: float, h: float,
          library: list[FontEntry]) -> tuple[str, float]:
    entry, score_ = match(el.font, library)
    family = entry.family if entry else "serif"

    content = el.content
    if el.case == "upper":
        content = content.upper()
    elif el.case == "lower":
        content = content.lower()
    elif el.case == "title":
        content = content.title()

    x, y, bw, bh = _px(el.box, w, h)
    cap = el.size_ratio * h
    size = cap / 0.70                       # cap height -> em, close enough for most faces
    anchor = {"left": "start", "center": "middle", "right": "end"}.get(el.align, "middle")
    tx = x if anchor == "start" else (x + bw if anchor == "end" else x + bw / 2)

    lines = content.split("\n")
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
        f'font-weight="{el.font.weight}" fill="{_fill(spec, el.colour)}" '
        f'letter-spacing="{el.tracking * size:.2f}" text-anchor="{anchor}" '
        f'dominant-baseline="middle"{rot}>{spans}</text>'
    )
    return node, score_


# --------------------------------------------------------------------------

def render(spec: DesignSpec, fonts_dir: Path, motifs_dir: Path) -> RenderResult:
    w = spec.canvas.width_mm / MM_PER_PX
    h = spec.canvas.height_mm / MM_PER_PX
    library = load_manifest(fonts_dir)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{spec.canvas.width_mm}mm" '
        f'height="{spec.canvas.height_mm}mm" viewBox="0 0 {w:.2f} {h:.2f}">',
        f"<title>{escape(spec.dna.occasion)} {escape(spec.dna.category)}</title>",
        '<g id="background">', _background(spec, w, h), "</g>",
    ]

    missing: list[str] = []
    scores: list[float] = []

    parts.append('<g id="structure">')
    for el in spec.elements:
        if isinstance(el, ShapeElement):
            parts.append(_shape(spec, el, w, h))
    parts.append("</g>")

    parts.append('<g id="decoration">')
    for el in spec.elements:
        if isinstance(el, MotifElement):
            node = _motif(spec, el, w, h, motifs_dir)
            if node:
                parts.append(node)
            else:
                missing.append(el.description)
    parts.append("</g>")

    parts.append('<g id="type">')
    for el in spec.elements:
        if isinstance(el, TextElement):
            node, s = _text(spec, el, w, h, library)
            parts.append(node)
            scores.append(s)
    parts.append("</g></svg>")

    return RenderResult("\n".join(parts), missing, scores)


def write_svg(result: RenderResult, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result.svg)
    return path
