"""The sheet the artwork is drawn on.

Two things the spec asked for that the renderer had never done: bleed, and the
background treatments it accepts but only half of which it could draw.
"""

import re
from pathlib import Path

import pytest

from stockforge.schema import (
    Background, Box, Canvas, ColourRole, DesignDNA, DesignSpec, FontClass, Grid,
    Page, Palette, Provenance, Swatch, TextElement, TypeRole,
)
from stockforge.stages.render import render

from conftest import build_font_library, build_motif_library


@pytest.fixture
def assets(tmp_path):
    return build_font_library(tmp_path / "fonts"), build_motif_library(tmp_path / "motifs")


def _spec(treatment="solid", secondary=None, hint=None, bleed=3.0):
    return DesignSpec(
        source_asset_id="x",
        dna=DesignDNA(
            category="invitation", occasion="wedding",
            grid=Grid(margin_x=0.08, margin_y=0.08),
            background=Background(treatment=treatment, base=ColourRole.BACKGROUND,
                                  secondary=secondary, texture_hint=hint),
            palette=Palette(swatches=[
                Swatch(role=ColourRole.BACKGROUND, hex="#faf6f0", coverage=0.8),
                Swatch(role=ColourRole.SURFACE, hex="#e8dfd2", coverage=0.1),
                Swatch(role=ColourRole.INK, hex="#2b2b28", coverage=0.1)])),
        pages=[Page(name="p", canvas=Canvas(width_mm=127, height_mm=178, bleed_mm=bleed),
                    elements=[TextElement(
                        role=TypeRole.TITLE, content="Amelia",
                        box=Box(x=0.1, y=0.4, w=0.8, h=0.1),
                        font=FontClass(category="serif", weight=400),
                        size_ratio=0.05)])],
        provenance=Provenance(stock_safe=True), confidence=1.0)


def _sheet(svg: str) -> tuple[float, float, list[float]]:
    head = svg.splitlines()[0]
    m = re.search(r'width="([\d.]+)mm" height="([\d.]+)mm" viewBox="([^"]+)"', head)
    return float(m.group(1)), float(m.group(2)), [float(v) for v in m.group(3).split()]


def _first_rect(svg: str) -> dict:
    tag = re.search(r"<rect[^/]+/>", svg).group(0)
    return {k: float(v) for k, v in re.findall(r'(x|y|width|height)="(-?[\d.]+)"', tag)}


# --- bleed ----------------------------------------------------------------

def test_without_bleed_the_sheet_is_the_trim(assets):
    fonts, motifs = assets
    w, h, box = _sheet(render(_spec(), fonts, motifs).svg)
    assert (w, h) == (127.0, 178.0)
    assert box[0] == 0 and box[1] == 0


def test_bleed_grows_the_sheet_on_every_side(assets):
    """Canvas.bleed_mm has defaulted to 3mm since the first commit and nothing
    read it, so every file was cut to the trim exactly — and any wander in a
    printer's guillotine shows as a white sliver down one edge."""
    fonts, motifs = assets
    w, h, box = _sheet(render(_spec(), fonts, motifs, bleed_mm=3.0).svg)

    assert (w, h) == (133.0, 184.0)          # 127 + 2x3, 178 + 2x3
    assert box[0] == box[1] < 0, "the origin has to move, or the art shifts"
    assert box[0] == pytest.approx(-3.0 / (25.4 / 96.0), abs=0.01)


def test_the_ground_runs_all_the_way_to_the_edge(assets):
    """Which is the entire point of bleed."""
    fonts, motifs = assets
    svg = render(_spec(), fonts, motifs, bleed_mm=3.0).svg
    rect = _first_rect(svg)
    _, _, box = _sheet(svg)

    assert rect["x"] == pytest.approx(box[0]) and rect["y"] == pytest.approx(box[1])
    assert rect["width"] == pytest.approx(box[2])
    assert rect["height"] == pytest.approx(box[3])


def test_the_artwork_does_not_move_when_the_sheet_grows(assets):
    """Geometry is normalised to the trim. Only the sheet around it grows."""
    fonts, motifs = assets
    trim = re.search(r'<tspan x="([\d.]+)" y="([\d.]+)"',
                     render(_spec(), fonts, motifs).svg).groups()
    bled = re.search(r'<tspan x="([\d.]+)" y="([\d.]+)"',
                     render(_spec(), fonts, motifs, bleed_mm=3.0).svg).groups()
    assert trim == bled


def test_the_file_says_what_the_trim_was(assets):
    fonts, motifs = assets
    svg = render(_spec(), fonts, motifs, bleed_mm=3.0).svg
    assert "trim 127x178mm, 3mm bleed" in svg
    assert "trim" not in render(_spec(), fonts, motifs).svg


# --- background treatments ------------------------------------------------

def test_a_solid_ground_reports_nothing(assets):
    fonts, motifs = assets
    assert render(_spec("solid"), fonts, motifs).unrendered == []


def test_a_panel_is_drawn_rather_than_flattened(assets):
    """`panel` was accepted by the schema and fell through to a flat colour."""
    fonts, motifs = assets
    result = render(_spec("panel", secondary=ColourRole.SURFACE), fonts, motifs)

    assert result.unrendered == []
    rects = re.findall(r"<rect[^/]+/>", result.svg)
    assert len(rects) == 2
    assert "#e8dfd2" in rects[1], "the panel should be the second colour"
    assert "#faf6f0" in rects[0]


def test_a_panel_with_no_second_colour_says_so(assets):
    fonts, motifs = assets
    result = render(_spec("panel"), fonts, motifs)
    assert result.unrendered and "panel" in result.unrendered[0]


def test_a_texture_is_reported_not_faked(assets):
    """Same rule as an unmatched motif: we do not invent a watercolour wash,
    and we do not quietly ship flat colour as though the spec never asked."""
    fonts, motifs = assets
    result = render(_spec("texture", hint="a loose watercolour wash"),
                    fonts, motifs)

    assert result.unrendered == ["background texture: a loose watercolour wash"]
    assert "#faf6f0" in result.svg, "the base colour still goes down"


def test_a_texture_with_no_hint_still_reports(assets):
    fonts, motifs = assets
    assert render(_spec("texture"), fonts, motifs).unrendered


# --- motifs keeping their shape -------------------------------------------

def _with_motif(tmp_path, stretch: bool, box: Box):
    from conftest import MOTIF
    from stockforge.schema import MotifElement, MotifKind

    motifs = tmp_path / "motifs"
    motifs.mkdir(parents=True, exist_ok=True)
    svg = MOTIF.format(kind="botanical", tags="sprig, leaves", title="Sprig")
    if stretch:
        svg = svg.replace('data-kind=', 'data-stretch="true" data-kind=')
    (motifs / "sprig-01.svg").write_text(svg)

    spec = _spec()
    spec.pages[0].elements.append(MotifElement(
        motif=MotifKind.BOTANICAL, description="a sprig", library_id="sprig-01", box=box))
    return spec, motifs


def _scale(svg: str) -> tuple[float, float]:
    m = re.search(r'transform="scale\(([\d.]+) ([\d.]+)\)"', svg)
    return float(m.group(1)), float(m.group(2))


def test_a_drawing_is_not_squashed_to_fit_its_box(assets, tmp_path):
    """Motifs were scaled on each axis independently, so a sprig in a box three
    times wider than it is tall came out three times too wide."""
    fonts, _ = assets
    spec, motifs = _with_motif(tmp_path, stretch=False,
                               box=Box(x=0.1, y=0.1, w=0.6, h=0.1))
    sx, sy = _scale(render(spec, fonts, motifs).svg)
    assert sx == pytest.approx(sy), "the drawing has been distorted"


def test_a_rule_may_still_be_pulled_to_width(assets, tmp_path):
    """A rule, a border or a band is meant to be stretched. It says so itself."""
    fonts, _ = assets
    spec, motifs = _with_motif(tmp_path, stretch=True,
                               box=Box(x=0.1, y=0.1, w=0.6, h=0.1))
    sx, sy = _scale(render(spec, fonts, motifs).svg)
    assert sx > sy * 2


def test_a_drawing_that_keeps_its_shape_is_centred_in_the_space(assets, tmp_path):
    fonts, _ = assets
    box = Box(x=0.1, y=0.1, w=0.6, h=0.1)
    spec, motifs = _with_motif(tmp_path, stretch=False, box=box)
    svg = render(spec, fonts, motifs).svg

    placed = re.search(r'<g transform="translate\(([\d.]+) ([\d.]+)\)"', svg)
    trim_w = 127 / (25.4 / 96.0)
    # it sits further right than the box's own edge, because it was centred
    assert float(placed.group(1)) > box.x * trim_w


# --- borders that stay the same thickness all the way round ---------------

SHIPPED = Path(__file__).resolve().parent.parent / "assets" / "motifs"


def _with_shipped(library_id: str, box: Box):
    from stockforge.schema import MotifElement, MotifKind
    spec = _spec()
    # Only the element under test: these measure where ink lands, and the rest
    # of a design's type would be measured along with it.
    spec.pages[0].elements = [MotifElement(
        motif=MotifKind.FRAME, description="a frame",
        library_id=library_id, box=box)]
    return spec


def _with_shape(shape):
    spec = _spec()
    spec.pages[0].elements = [shape]
    return spec


def _ink_box(svg_text, tmp_path, name, width=700):
    """Where the drawn ink actually lands, in pixels."""
    import cv2
    import numpy as np

    from stockforge.stages.export import svg_to_png
    src = tmp_path / f"{name}.svg"
    src.write_text(svg_text)
    png = svg_to_png(src, tmp_path / f"{name}.png", width=width)
    img = cv2.imread(str(png), cv2.IMREAD_GRAYSCALE)
    ink = img < 200
    ys, xs = np.where(ink)
    return img, ink, (xs.min(), ys.min(), xs.max(), ys.max())


def test_a_frame_motif_keeps_an_even_border_in_a_wide_box(tmp_path, fonts_dir):
    """Border thickness is baked into the 100-unit square a motif is drawn on,
    so scaling each axis to the box independently made the side bars a
    different weight from the top and bottom — five times heavier in a 5:1 box.
    """
    import numpy as np

    svg = render(_with_shipped("frame-thin-01", Box(x=0.05, y=0.40, w=0.90, h=0.18)),
                 fonts_dir, SHIPPED).svg
    img, ink, (x0, y0, x1, y1) = _ink_box(svg, tmp_path, "frame")

    mid_row = ink[(y0 + y1) // 2]
    mid_col = ink[:, (x0 + x1) // 2]
    side_bar = int(np.sum(mid_row[: (x0 + x1) // 2]))
    top_bar = int(np.sum(mid_col[: (y0 + y1) // 2]))

    assert side_bar > 0 and top_bar > 0, "nothing was drawn"
    ratio = max(side_bar, top_bar) / min(side_bar, top_bar)
    assert ratio < 1.5, (f"side bar {side_bar}px, top bar {top_bar}px "
                         f"— {ratio:.1f} times apart")


def test_a_frame_motif_is_not_distorted_by_its_box(tmp_path, fonts_dir):
    """It keeps its shape and is centred, rather than being pulled to fit."""
    svg = render(_with_shipped("frame-thin-01", Box(x=0.05, y=0.40, w=0.90, h=0.18)),
                 fonts_dir, SHIPPED).svg
    _img, _ink, (x0, y0, x1, y1) = _ink_box(svg, tmp_path, "square")
    w, h = x1 - x0, y1 - y0
    assert abs(w - h) / max(w, h) < 0.1, f"drawn {w}x{h}, which is not square"


# --- an arch has to fit the box it was given ------------------------------

@pytest.mark.parametrize("box,label", [
    (Box(x=0.05, y=0.60, w=0.90, h=0.15), "wide"),
    (Box(x=0.30, y=0.20, w=0.40, h=0.60), "tall"),
    (Box(x=0.20, y=0.30, w=0.60, h=0.30), "middling"),
])
def test_an_arch_stays_inside_its_box(tmp_path, fonts_dir, box, label):
    """The rise was half the width whatever the box, so any box wider than it
    was tall got an arc taller than the space it had: the sides ran downwards
    and the curve escaped off the top of the page."""
    from stockforge.schema import ShapeElement

    spec = _with_shape(ShapeElement(primitive="arch", fill=None,
                                    stroke=ColourRole.INK,
                                    stroke_ratio=0.004, box=box))
    svg = render(spec, fonts_dir, SHIPPED).svg
    img, _ink, (x0, y0, x1, y1) = _ink_box(svg, tmp_path, f"arch-{label}")

    px_h = img.shape[0]
    top, bottom = box.y * px_h, (box.y + box.h) * px_h
    assert y0 >= top - 4, f"{label}: the arch starts {top - y0:.0f}px above its box"
    assert y1 <= bottom + 4, f"{label}: it runs {y1 - bottom:.0f}px below its box"


def test_a_tall_arch_is_still_a_semicircle(fonts_dir):
    """The cap must not change the shape where it already fitted."""
    from stockforge.schema import ShapeElement

    spec = _with_shape(ShapeElement(primitive="arch", fill=None,
                                    stroke=ColourRole.INK, stroke_ratio=0.004,
                                    box=Box(x=0.25, y=0.15, w=0.50, h=0.70)))
    svg = render(spec, fonts_dir, SHIPPED).svg
    d = re.search(r'<path d="([^"]+)"', svg).group(1)
    rx, ry = re.search(r"A ([\d.]+) ([\d.]+)", d).groups()
    assert abs(float(rx) - float(ry)) < 0.5, f"a tall arch became elliptical: {rx}x{ry}"
