"""A drawing through the type.

Four invitations came out of the invent path with the headline sliced by an
arch and a spider's web drawn across the opening line. Every check passed and
all four were filed `ready`: the exporter wrote the PDF, the EPS and the
preview, and nothing was queued for a human. Type and decoration are placed
from the same spec and neither has ever known the other is there.

The critic would have caught it if it could see the page. Through the local
bridge it cannot — that model answers text and never answers an image — so the
one check that would have looked at the result was not looking.

These tests are about the measurement, and the thing they have to get right is
not "is there overlap" but "is the overlap a fault". Type on a filled block is
ordinary design. Type with a rule through it is not.
"""

from pathlib import Path

import pytest

from stockforge.schema import (
    Background, Box, Canvas, ColourRole, DesignDNA, DesignSpec, FontClass, Grid,
    Page, Palette, Provenance, ShapeElement, Swatch, TextElement, TypeRole,
)
from stockforge.stages.collide import struck_type
from stockforge.stages.render import MM_PER_PX, render

from conftest import build_font_library, build_motif_library


W_MM, H_MM = 127.0, 178.0
PAGE = (W_MM / MM_PER_PX, H_MM / MM_PER_PX)


@pytest.fixture
def assets(tmp_path):
    return build_font_library(tmp_path / "fonts"), build_motif_library(tmp_path / "motifs")


def _spec(*elements):
    return DesignSpec(
        source_asset_id="x",
        dna=DesignDNA(
            category="invitation", occasion="halloween",
            grid=Grid(margin_x=0.08, margin_y=0.08),
            background=Background(treatment="solid", base=ColourRole.BACKGROUND),
            palette=Palette(swatches=[
                Swatch(role=ColourRole.BACKGROUND, hex="#faf6f0", coverage=0.8),
                Swatch(role=ColourRole.INK, hex="#1a1a1a", coverage=0.1),
                Swatch(role=ColourRole.LINE, hex="#1a1a1a", coverage=0.1)])),
        pages=[Page(name="p", canvas=Canvas(width_mm=W_MM, height_mm=H_MM, bleed_mm=0),
                    elements=list(elements))],
        provenance=Provenance(stock_safe=True), confidence=1.0)


def _headline(y=0.40, h=0.10, content="MIDNIGHT GATHERING", rotation=0.0):
    return TextElement(
        role=TypeRole.TITLE, content=content,
        box=Box(x=0.10, y=y, w=0.80, h=h),
        font=FontClass(category="serif", weight=400),
        size_ratio=0.05, rotation=rotation)


def _rule(y, thickness=0.004, x=0.05, w=0.90):
    """A hairline straight across the page — an arch leg, in effect."""
    return ShapeElement(primitive="rect", box=Box(x=x, y=y, w=w, h=thickness),
                        fill=ColourRole.LINE, stroke=None)


def _struck(spec, assets):
    fonts, motifs = assets
    result = render(spec, fonts, motifs)
    return struck_type(result.svg, result.text_ink, PAGE)


def test_a_rule_through_the_headline_is_found(assets):
    """The fault as it actually happened: a line of ink across a line of words."""
    spec = _spec(_headline(), _rule(y=0.445))
    found = _struck(spec, assets)
    assert found, "a rule drawn across the headline was not reported"
    assert "MIDNIGHT GATHERING" in found[0].label
    assert found[0].share >= 0.02


def test_type_clear_of_the_drawing_is_left_alone(assets):
    """The same rule, moved off the words. Nothing to say."""
    assert _struck(_spec(_headline(), _rule(y=0.80)), assets) == []


def test_type_on_a_filled_block_is_not_a_fault(assets):
    """A banner behind a line is a normal thing to design.

    The drawing is under every letter rather than through two of them, and
    whether it reads is a question about colour. Flagging this would send
    every reversed-out headline in the catalogue to a human.
    """
    block = ShapeElement(primitive="rect", box=Box(x=0.05, y=0.36, w=0.90, h=0.18),
                         fill=ColourRole.LINE, stroke=None)
    assert _struck(_spec(_headline(), block), assets) == []


def test_a_page_with_no_drawings_is_not_rasterised(assets, monkeypatch):
    """Two rasterisations a page, five thousand designs. Don't do it for nothing."""
    import stockforge.stages.collide as collide

    monkeypatch.setattr(collide, "_ink", lambda *a, **k: pytest.fail("rasterised anyway"))
    assert _struck(_spec(_headline()), assets) == []


def test_rotated_type_is_not_judged(assets):
    """The renderer hands back no rectangle for a line it has turned, so there
    is nothing to measure against. Guessing the upright box would report a
    strike wherever the drawing is, which is worse than saying nothing."""
    spec = _spec(_headline(rotation=30.0), _rule(y=0.445))
    assert _struck(spec, assets) == []


def test_the_worst_line_is_reported_first(assets):
    """A human reads the first clause of the reason and stops."""
    spec = _spec(
        _headline(y=0.20, h=0.10, content="GRAZED"),
        _headline(y=0.50, h=0.10, content="WRECKED"),
        _rule(y=0.253, thickness=0.002),
        _rule(y=0.50, thickness=0.060),
    )
    found = _struck(spec, assets)
    assert len(found) == 2, [f.line() for f in found]
    assert "WRECKED" in found[0].label
    assert found[0].share > found[1].share


def test_it_says_which_line_and_how_much(assets):
    spec = _spec(_headline(), _rule(y=0.445))
    said = _struck(spec, assets)[0].line()
    assert "MIDNIGHT GATHERING" in said
    assert "%" in said


def test_a_page_that_will_not_rasterise_says_nothing(assets, monkeypatch):
    """Not "this design has a layout fault". The exporter already fails a
    design that produces no file; a missing converter must not also arrive as
    a second, wrong answer about the layout."""
    import stockforge.stages.collide as collide

    def broken(*a, **k):
        raise RuntimeError("no converter on this machine")

    monkeypatch.setattr(collide, "_ink", broken)
    assert _struck(_spec(_headline(), _rule(y=0.445)), assets) == []


def test_a_serif_touching_a_drawing_is_not_a_fault(assets):
    """The floor, and what it is for.

    A rule that clips the tail of the last letter measures about 1.5% of the
    line's ink. On the real cards that band — 0.6%, 0.8% — was a serif meeting
    an arch leg, which is a thing you have to look for to see. Without a floor
    every design with a drawing anywhere near the type goes to a human, and a
    queue that long is read by nobody.
    """
    grazed = _spec(_headline(), _rule(y=0.44, thickness=0.02, x=0.86, w=0.02))
    assert _struck(grazed, assets) == []


def test_it_looks_where_the_ink_is_not_where_the_box_is(assets):
    """A short line centred in a wide box.

    The box is where the line was allowed to go; the renderer fitted, anchored
    and centred it inside that. Measuring the box instead would read the empty
    left-hand third of it, find no type ink there, and report a clean page.
    """
    short = TextElement(
        role=TypeRole.TITLE, content="OCT",
        box=Box(x=0.05, y=0.40, w=0.90, h=0.10),
        font=FontClass(category="serif", weight=400), size_ratio=0.05)
    found = _struck(_spec(short, _rule(y=0.445)), assets)
    assert found, "a rule through a centred short line was not reported"
    assert "OCT" in found[0].label


def test_the_rectangle_follows_a_line_that_had_to_shrink(assets):
    """The renderer sets an oversized line smaller rather than letting it run
    off the page. The rectangle it reports has to shrink with it: left at the
    asked-for width it covers paper the line never reached, and a drawing over
    a neighbouring line gets blamed on this one.

    Checked against the ink itself rather than against arithmetic — render the
    type on its own and see where the marks are.
    """
    import tempfile

    import numpy as np

    from stockforge.stages.collide import _ink, _only

    fonts, motifs = assets
    long_line = TextElement(
        role=TypeRole.TITLE, content="AN EXCEEDINGLY LONG HEADLINE INDEED",
        box=Box(x=0.10, y=0.40, w=0.40, h=0.10),
        font=FontClass(category="serif", weight=400), size_ratio=0.05)
    result = render(_spec(long_line), fonts, motifs)

    assert result.refits and result.refits[0][1] < 1.0, "this line was meant to shrink"
    (_, (x, y, w, h)), = result.text_ink

    with tempfile.TemporaryDirectory() as tmp:
        typed = _ink(_only(result.svg, ("type",)), Path(tmp), "t")
    rows, cols = np.nonzero(typed)
    ph, pw = typed.shape[:2]
    sx, sy = pw / PAGE[0], ph / PAGE[1]

    # every mark inside the rectangle, and the rectangle not much bigger
    assert cols.min() >= x * sx - 2 and cols.max() <= (x + w) * sx + 2
    assert rows.min() >= y * sy - 2 and rows.max() <= (y + h) * sy + 2
    assert w * sx <= (cols.max() - cols.min()) * 1.15


def test_a_drawing_that_brought_its_own_group_is_still_measured(assets, tmp_path):
    """A motif file is inlined into the page whole.

    Plenty of drawings carry a wrapper group of their own — anything that has
    been through a drawing app does. Switching layers off by "any group with a
    lowercase id" would switch that wrapper off too, and the marks inside it
    would go missing from the very picture being measured. Under-detection, so
    nothing breaks and nothing is reported: the worst kind.
    """
    from stockforge.schema import MotifElement

    fonts, motifs = assets
    (motifs / "barred.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" '
        'data-kind="rule" data-tags="bar"><title>Bar</title>'
        '<g id="art"><rect x="0" y="45" width="100" height="10"/></g></svg>')

    bar = MotifElement(motif="rule", description="a bar",
                       box=Box(x=0.05, y=0.40, w=0.90, h=0.10),
                       colour=ColourRole.LINE, library_id="barred")
    found = _struck(_spec(_headline(), bar), assets)
    assert found, "a drawing inside its own group was not measured"
    assert "MIDNIGHT GATHERING" in found[0].label


def test_a_placed_picture_across_a_line_is_not_a_strike(assets):
    """An object in this library may be a drawing or a picture.

    A picture is an area, not a stroke — setting type across the edge of one
    is a thing designers do on purpose, and it is what gets read back off a
    listing the owner already sells. Judged like a drawn line it put a design
    with a small photo in it, and a design carrying a big painted object,
    straight into the review queue.

    The geometry here is the one that actually broke: a picture across the
    middle of a line, covering 16% of its ink. A picture covering the whole
    line proves nothing, because that reads as a ground either way.
    """
    picture = _picture_motif(assets, Box(x=0.35, y=0.40, w=0.28, h=0.10))
    assert _struck(_spec(_headline(), picture), assets) == []


def test_a_rule_is_still_found_on_a_page_that_also_has_a_picture(assets):
    """Dropping the pictures must leave the page still readable.

    Take out the opening tag and leave the closing one and the SVG is
    malformed, so neither layer rasterises, so nothing is ever reported again
    — a check that has quietly stopped working looks exactly like a clean
    catalogue.
    """
    picture = _picture_motif(assets, Box(x=0.35, y=0.15, w=0.28, h=0.10))
    found = _struck(_spec(_headline(), picture, _rule(y=0.445)), assets)
    assert found, "the rule went missing once a picture was on the page"
    assert "MIDNIGHT GATHERING" in found[0].label


def test_a_drawing_between_two_pictures_is_not_swallowed(assets):
    """Pictures come out one at a time, not everything from the first to the
    last. A design with a photograph at the top and a painted object at the
    bottom would otherwise lose every drawn mark in between — and lose it
    silently, because a page with nothing drawn on it has nothing to report.
    """
    found = _struck(_spec(
        _headline(),
        _picture_motif(assets, Box(x=0.05, y=0.10, w=0.30, h=0.10)),
        _bar(assets),
        _picture_motif(assets, Box(x=0.65, y=0.80, w=0.30, h=0.10)),
    ), assets)
    assert found, "the drawing between the two pictures went missing"
    assert "MIDNIGHT GATHERING" in found[0].label


def test_dropping_the_pictures_leaves_a_page_that_still_parses(assets):
    """Inkscape forgives a stray closing tag. The cairosvg fallback — what
    runs on a machine with no Inkscape on it — does not, and there the whole
    check would fail to a clean bill of health on every design."""
    from xml.etree import ElementTree

    from stockforge.stages.collide import _PICTURE, _only

    fonts, motifs = assets
    spec = _spec(_headline(), _picture_motif(assets, Box(x=0.35, y=0.40, w=0.28, h=0.10)))
    svg = render(spec, fonts, motifs).svg
    assert "<image" in svg, "this page was meant to carry a picture"
    ElementTree.fromstring(_PICTURE.sub("", _only(svg, ("decoration",))))


def _bar(assets):
    """A drawn motif, straight through the headline."""
    from stockforge.schema import MotifElement

    _, motifs = assets
    (motifs / "barred.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" '
        'data-kind="rule" data-tags="bar"><title>Bar</title>'
        '<g id="art"><rect x="0" y="45" width="100" height="10"/></g></svg>')
    return MotifElement(motif="rule", description="a bar",
                        box=Box(x=0.05, y=0.40, w=0.90, h=0.10),
                        colour=ColourRole.LINE, library_id="barred")


def _picture_motif(assets, box):
    """A motif that is a photograph rather than a drawing."""
    import numpy as np
    from PIL import Image

    from stockforge.schema import MotifElement

    _, motifs = assets
    Image.fromarray(np.zeros((40, 400, 3), dtype=np.uint8)).save(
        motifs / "painted.art.png")
    return MotifElement(motif="seasonal", description="a painted object",
                        box=box, colour=ColourRole.LINE, library_id="painted")
