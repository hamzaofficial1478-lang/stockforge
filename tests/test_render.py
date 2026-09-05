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
