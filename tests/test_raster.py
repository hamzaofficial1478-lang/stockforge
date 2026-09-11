"""Photographic areas.

A photorealistic or painted scene cannot honestly be rebuilt as vector, and the
program was right to refuse to fake one. What it did instead was leave nothing
there: Element was text, motif or shape, the renderer never emitted an <image>,
and the README promised "the master keeps it as a placed image" while the
master had a hole where the photograph had been.

The pixels are the design's own, cut out of the listing image it was read from.
That makes the master faithful and the design permanently unsellable, which is
the correct answer to both questions at once.
"""

import base64
import re
from pathlib import Path

import cv2
import numpy as np
import pytest

from stockforge.schema import (
    Background, Box, Canvas, ColourRole, DesignDNA, DesignSpec, Page, Palette,
    Provenance, RasterElement, Swatch, TextElement, TypeRole, FontClass,
)
from stockforge.stages.render import render


def _photo(path: Path, w: int = 800, h: int = 1000) -> Path:
    """Something no vector rebuild could honestly claim: noise, not shapes."""
    rng = np.random.default_rng(7)
    img = rng.integers(0, 255, (h, w, 3), dtype=np.uint8)
    cv2.circle(img, (w // 2, h // 3), w // 5, (30, 90, 200), -1)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), img)
    return path


def _spec(tmp_path, rasters, source=True) -> DesignSpec:
    src = _photo(tmp_path / "flat.jpg")
    return DesignSpec(
        source_asset_id="a", design_id="d",
        dna=DesignDNA(category="print", occasion="autumn",
                      background=Background(treatment="solid"),
                      palette=Palette(swatches=[
                          Swatch(role=ColourRole.BACKGROUND, hex="#ffffff", coverage=0.8),
                          Swatch(role=ColourRole.INK, hex="#222222", coverage=0.2)])),
        pages=[Page(name="front", canvas=Canvas(width_mm=210, height_mm=297),
                    elements=rasters,
                    source_image=str(src) if source else None)],
        provenance=Provenance(built_with="unknown"),
        confidence=0.8,
    )


def _hrefs(svg: str) -> list[str]:
    return re.findall(r'href="data:image/jpeg;base64,([^"]+)"', svg)


# --- it gets drawn at all -------------------------------------------------

def test_a_photographic_area_is_placed_not_dropped(tmp_path, fonts_dir, motifs_dir):
    el = RasterElement(description="a painted autumn wood",
                       box=Box(x=0.1, y=0.1, w=0.8, h=0.5))
    result = render(_spec(tmp_path, [el]), fonts_dir, motifs_dir)

    assert "<image" in result.svg, "the photograph left a hole"
    assert result.unrendered == [], result.unrendered


def test_the_pixels_are_embedded_and_really_are_the_source(tmp_path, fonts_dir, motifs_dir):
    """Embedded rather than linked, because a master that stops working when
    you move it off this machine is not a master. And actually decodable —
    a data URI that is not a real JPEG fails in the design app, not here."""
    el = RasterElement(description="a scene", box=Box(x=0, y=0, w=1, h=1))
    result = render(_spec(tmp_path, [el]), fonts_dir, motifs_dir)

    hrefs = _hrefs(result.svg)
    assert hrefs, "nothing was embedded"
    raw = base64.standard_b64decode(hrefs[0])
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    assert img is not None, "what was embedded is not a readable image"
    assert img.shape[0] > 10 and img.shape[1] > 10

    assert str(tmp_path) not in result.svg, "it linked to a local path as well"


def test_it_is_placed_where_the_spec_put_it(tmp_path, fonts_dir, motifs_dir):
    el = RasterElement(description="a scene", box=Box(x=0.25, y=0.5, w=0.5, h=0.25))
    svg = render(_spec(tmp_path, [el]), fonts_dir, motifs_dir).svg

    tag = re.search(r"<image[^>]*>", svg).group(0)
    w_px = 210 / (25.4 / 96.0)
    h_px = 297 / (25.4 / 96.0)
    assert abs(float(re.search(r'x="([\d.]+)"', tag).group(1)) - 0.25 * w_px) < 1
    assert abs(float(re.search(r'y="([\d.]+)"', tag).group(1)) - 0.50 * h_px) < 1
    assert abs(float(re.search(r'width="([\d.]+)"', tag).group(1)) - 0.50 * w_px) < 1
    assert abs(float(re.search(r'height="([\d.]+)"', tag).group(1)) - 0.25 * h_px) < 1


def test_only_the_named_part_of_the_source_is_cut(tmp_path, fonts_dir, motifs_dir):
    """`source` says which part of the listing image the area occupies. Cutting
    the whole image instead would place the type and the borders inside the
    photograph as well."""
    whole = RasterElement(description="all of it", box=Box(x=0, y=0, w=1, h=1),
                          source=Box(x=0, y=0, w=1, h=1))
    part = RasterElement(description="a quarter", box=Box(x=0, y=0, w=1, h=1),
                         source=Box(x=0.0, y=0.0, w=0.5, h=0.5))

    big = _hrefs(render(_spec(tmp_path, [whole]), fonts_dir, motifs_dir).svg)[0]
    small = _hrefs(render(_spec(tmp_path, [part]), fonts_dir, motifs_dir).svg)[0]

    def size(b64):
        raw = base64.standard_b64decode(b64)
        img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        return img.shape[1], img.shape[0]

    assert size(small) == (size(big)[0] // 2, size(big)[1] // 2), \
        f"cut {size(small)} out of {size(big)}"


def test_a_photograph_sits_under_the_type_not_over_it(tmp_path, fonts_dir, motifs_dir):
    """Drawn after the type it would bury the words the design is for."""
    el = RasterElement(description="a scene", box=Box(x=0, y=0, w=1, h=1))
    text = TextElement(role=TypeRole.TITLE, content="Harvest",
                       box=Box(x=0.1, y=0.4, w=0.8, h=0.1),
                       font=FontClass(category="serif", weight=400), size_ratio=0.05)
    svg = render(_spec(tmp_path, [el, text]), fonts_dir, motifs_dir).svg
    # rindex, not index: the last image has to be above the first word too,
    # or a photograph drawn a second time further down still buries the type.
    assert svg.rindex("<image") < svg.index("<text"), "the photograph covers the type"
    assert svg.count("<image") == 1, "it was drawn more than once"


# --- when it cannot be done ----------------------------------------------

def test_a_missing_source_leaves_a_gap_it_tells_you_about(tmp_path, fonts_dir, motifs_dir):
    """Same rule as an unmatched motif and an undrawable background: a gap you
    can see beats something invented."""
    spec = _spec(tmp_path, [RasterElement(description="a painted wood",
                                          box=Box(x=0, y=0, w=1, h=1))])
    Path(spec.pages[0].source_image).unlink()

    result = render(spec, fonts_dir, motifs_dir)
    assert "<image" not in result.svg
    assert result.unrendered, "it dropped the photograph without saying so"
    assert "painted wood" in result.unrendered[0]


def test_no_source_image_recorded_is_reported_too(tmp_path, fonts_dir, motifs_dir):
    spec = _spec(tmp_path, [RasterElement(description="a scene",
                                          box=Box(x=0, y=0, w=1, h=1))], source=False)
    result = render(spec, fonts_dir, motifs_dir)
    assert result.unrendered and "scene" in result.unrendered[0]


def test_an_unreadable_source_does_not_crash_the_render(tmp_path, fonts_dir, motifs_dir):
    spec = _spec(tmp_path, [RasterElement(description="a scene",
                                          box=Box(x=0, y=0, w=1, h=1))])
    Path(spec.pages[0].source_image).write_text("not a jpeg at all")
    result = render(spec, fonts_dir, motifs_dir)
    assert result.unrendered
    assert "<svg" in result.svg, "the whole page was lost over one element"


# --- and it must never be sold -------------------------------------------

def test_a_placed_photograph_makes_the_design_unsellable(tmp_path):
    """The owner's own artwork coming back to them, and nobody else's to sell."""
    spec = _spec(tmp_path, [RasterElement(description="a painted wood",
                                          box=Box(x=0, y=0, w=1, h=1))])
    spec.provenance.stock_safe = True          # as if the provenance pass missed it
    from stockforge.stages.analyse import _hold_back_rasters

    _hold_back_rasters(spec.pages, spec.provenance, spec.warnings)
    assert spec.publishable is False
    assert any("painted wood" in w for w in spec.warnings)


# --- when the photograph swallows the design -----------------------------
#
# The failure the owner hit, and the one that looks most like success. The
# model read a watercolour invitation as one photographic area. The renderer
# placed the original pixels faithfully — it was right to — and the text was
# set beside them. Out came the source image with words next to it. Every
# other check passed, because none of them asks whether anything was redrawn.

def _covering(tmp_path, share_w, share_h, **kw):
    return _spec(tmp_path, [RasterElement(
        description="the whole invitation as a photo",
        box=Box(x=0.01, y=0.01, w=share_w, h=share_h))], **kw)


def test_a_photograph_over_most_of_the_page_is_not_a_rebuild(tmp_path):
    """Roughly what came back: three quarters of the surface placed as one
    photograph, the text laid out beside it."""
    spec = _covering(tmp_path, 0.74, 0.96)
    flagged = spec.photocopied_pages(limit=0.40)
    assert flagged, "a design that is mostly photograph was called a rebuild"
    name, share = flagged[0]
    assert 0.70 < share < 0.72


def test_a_small_photographic_area_is_perfectly_fine(tmp_path):
    """A photo inside a design is normal and is what RasterElement is for.
    Only a photo that *is* the design is the failure."""
    assert _covering(tmp_path, 0.30, 0.25).photocopied_pages(limit=0.40) == []


def test_several_smaller_photographs_still_add_up(tmp_path):
    """Four quarter-page photographs are as much a photocopy as one big one,
    and splitting it up must not be a way round the check."""
    spec = _spec(tmp_path, [
        RasterElement(description=f"panel {i}",
                      box=Box(x=0.02 + 0.24 * i, y=0.1, w=0.22, h=0.8))
        for i in range(4)
    ])
    assert spec.photocopied_pages(limit=0.40), "four photographs got through"


def test_a_design_with_no_photograph_is_never_flagged(tmp_path):
    assert _spec(tmp_path, []).photocopied_pages(limit=0.40) == []


def test_the_limit_is_adjustable(tmp_path):
    spec = _covering(tmp_path, 0.6, 0.6)          # 36% of the page
    assert spec.photocopied_pages(limit=0.40) == []
    assert spec.photocopied_pages(limit=0.30), "the limit is not being honoured"
