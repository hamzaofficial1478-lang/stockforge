"""Tests for setting type: real metrics from the matched file, and fitting a
line to its box instead of letting it run off the page.

The font is built here rather than shipped, so these run on a machine with no
fonts installed and assert against numbers we chose ourselves.
"""

import json
import os
import re
from pathlib import Path

import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

from stockforge.schema import (
    Box, Canvas, ColourRole, DesignDNA, DesignSpec, FontClass, Page, Palette,
    Provenance, Swatch, TextElement, TypeRole,
)
from stockforge.stages import fonts as fonts_stage
from stockforge.stages.render import MM_PER_PX, render

# Every glyph is 600 units wide on a 1000 unit em, so a string of n characters
# is exactly 0.6 * n ems and the arithmetic in a test is checkable by eye.
ADVANCE = 600
UPEM = 1000


def _build_font(path: Path, cap_height: int = 700) -> Path:
    order = [".notdef", "space", "A"]
    fb = FontBuilder(UPEM, isTTF=True)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap({32: "space", 65: "A"})

    pen = TTGlyphPen(None)
    pen.moveTo((0, 0))
    pen.lineTo((400, 0))
    pen.lineTo((400, cap_height))
    pen.lineTo((0, cap_height))
    pen.closePath()
    fb.setupGlyf({n: (pen.glyph() if n == "A" else TTGlyphPen(None).glyph())
                  for n in order})

    fb.setupHorizontalMetrics({n: (ADVANCE, 0) for n in order})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "Testface", "styleName": "Regular",
                       "uniqueFontIdentifier": "testface",
                       "fullName": "Testface Regular",
                       "psName": "Testface-Regular", "version": "1.0"})
    fb.setupOS2(sCapHeight=cap_height, sTypoAscender=800, sTypoDescender=-200)
    fb.setupPost()
    path.parent.mkdir(parents=True, exist_ok=True)
    fb.save(str(path))
    return path


def _library(tmp_path: Path, cap_height: int = 700, weight: int = 400) -> Path:
    """A fonts folder with one usable face in it."""
    fonts_dir = tmp_path / "fonts"
    _build_font(fonts_dir / "Testface.ttf", cap_height)
    (fonts_dir / "manifest.json").write_text(json.dumps([{
        "path": "Testface.ttf", "family": "Testface", "style": "Regular",
        "category": "serif", "weight": weight, "contrast": "medium",
        "width": "normal", "mood": [], "licence": "OFL", "embeddable": True,
    }]))
    return fonts_dir


def _spec(content: str, *, size_ratio: float = 0.08, box_w: float = 0.8,
          weight: int = 400) -> DesignSpec:
    return DesignSpec(
        source_asset_id="abc",
        dna=DesignDNA(
            category="invitation", occasion="wedding",
            palette=Palette(swatches=[
                Swatch(role=ColourRole.BACKGROUND, hex="#ffffff", coverage=0.9),
                Swatch(role=ColourRole.INK, hex="#000000", coverage=0.1),
            ]),
        ),
        pages=[Page(name="cover", canvas=Canvas(width_mm=127, height_mm=178), elements=[
            TextElement(role=TypeRole.TITLE, content=content,
                        box=Box(x=0.1, y=0.4, w=box_w, h=0.12),
                        font=FontClass(category="serif", weight=weight),
                        size_ratio=size_ratio),
        ])],
        provenance=Provenance(stock_safe=True), confidence=0.9,
    )


def _rendered_size(svg: str) -> float:
    return float(re.search(r'font-size="([\d.]+)"', svg).group(1))


# --- measuring ------------------------------------------------------------

def test_a_line_is_measured_from_the_file_not_guessed_at(tmp_path):
    fonts_dir = _library(tmp_path)
    entry = fonts_stage.load_manifest(fonts_dir)[0]
    face = fonts_stage.open_face(entry, fonts_dir)

    assert face is not None
    assert face.units_per_em == UPEM
    assert face.measure("AAAA", 100) == pytest.approx(4 * 0.6 * 100)
    assert face.measure("AAAA", 200) == pytest.approx(2 * 4 * 0.6 * 100)
    # tracking is one gap between each pair, in em
    assert face.measure("AAAA", 100, 0.1) == pytest.approx(240 + 3 * 10)
    assert face.measure("", 100) == 0.0


def test_a_manifest_entry_with_no_file_behind_it_is_not_a_crash(tmp_path):
    fonts_dir = _library(tmp_path)
    entry = fonts_stage.load_manifest(fonts_dir)[0]
    (fonts_dir / "Testface.ttf").unlink()

    assert fonts_stage.open_face(entry, fonts_dir) is None
    # and the renderer still produces a page, it just cannot measure it
    result = render(_spec("AAA"), fonts_dir, tmp_path / "motifs")
    assert "<text" in result.svg
    assert result.refits == []


# --- cap height -----------------------------------------------------------

def test_type_size_follows_the_face_s_own_cap_height(tmp_path):
    """size_ratio is a cap height. Two faces with different cap heights must
    be set at different ems to put the same amount of ink on the page."""
    tall = render(_spec("A"), _library(tmp_path / "tall", cap_height=700),
                  tmp_path / "motifs")
    short = render(_spec("A"), _library(tmp_path / "short", cap_height=500),
                   tmp_path / "motifs")

    canvas_px = 178 / MM_PER_PX
    assert _rendered_size(tall.svg) == pytest.approx(0.08 * canvas_px / 0.70, rel=1e-3)
    assert _rendered_size(short.svg) == pytest.approx(0.08 * canvas_px / 0.50, rel=1e-3)


# --- fitting --------------------------------------------------------------

def test_a_line_that_would_run_off_the_page_is_set_smaller(tmp_path):
    fonts_dir = _library(tmp_path)
    result = render(_spec("A" * 40), fonts_dir, tmp_path / "motifs")

    assert result.refits, "a 40-character title at 0.08 cannot fit a 127mm card"
    label, scale = result.refits[0]
    assert "title" in label and scale < 1.0
    assert result.worst_refit == scale


def test_the_line_actually_fits_once_it_has_been_refitted(tmp_path):
    fonts_dir = _library(tmp_path)
    spec = _spec("A" * 40)
    result = render(spec, fonts_dir, tmp_path / "motifs")

    face = fonts_stage.open_face(fonts_stage.load_manifest(fonts_dir)[0], fonts_dir)
    box_px = spec.pages[0].elements[0].box.w * (127 / MM_PER_PX)
    assert face.measure("A" * 40, _rendered_size(result.svg)) <= box_px


def test_a_line_that_already_fits_is_left_at_the_size_it_asked_for(tmp_path):
    fonts_dir = _library(tmp_path)
    result = render(_spec("AAA", size_ratio=0.03), fonts_dir, tmp_path / "motifs")

    assert result.refits == []
    assert result.worst_refit == 1.0
    assert _rendered_size(result.svg) == pytest.approx(0.03 * (178 / MM_PER_PX) / 0.70,
                                                      rel=1e-3)


def test_a_narrower_box_forces_a_smaller_size(tmp_path):
    fonts_dir = _library(tmp_path)
    wide = render(_spec("A" * 20, box_w=0.8), fonts_dir, tmp_path / "motifs")
    narrow = render(_spec("A" * 20, box_w=0.4), fonts_dir, tmp_path / "motifs")
    assert _rendered_size(narrow.svg) < _rendered_size(wide.svg)


# --- which file actually gets drawn ---------------------------------------

def test_the_weight_written_out_is_the_face_we_matched(tmp_path):
    """Fontconfig picks a file by family and weight together. Asking for 400 of
    a family whose bold we matched would draw a different file from the one we
    measured."""
    fonts_dir = _library(tmp_path, weight=700)
    result = render(_spec("AAA", weight=300), fonts_dir, tmp_path / "motifs")
    assert 'font-weight="700"' in result.svg
    assert 'font-family="Testface"' in result.svg


# --- making the family name resolve at all --------------------------------

def test_the_fontconfig_file_points_at_our_own_folder(tmp_path):
    fonts_dir = _library(tmp_path)
    conf = fonts_stage.write_fontconfig(fonts_dir)
    body = conf.read_text()
    assert f"<dir>{fonts_dir.resolve()}</dir>" in body
    assert "/etc/fonts/fonts.conf" in body, "system fonts must still resolve"


def test_activate_puts_our_folder_on_the_search_path(tmp_path, monkeypatch):
    monkeypatch.delenv("FONTCONFIG_FILE", raising=False)
    fonts_dir = _library(tmp_path)
    conf = fonts_stage.activate(fonts_dir)
    assert conf is not None
    assert Path(os.environ["FONTCONFIG_FILE"]) == conf.resolve()


def test_activate_leaves_a_setting_of_your_own_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("FONTCONFIG_FILE", "/etc/fonts/fonts.conf")
    assert fonts_stage.activate(_library(tmp_path)) is None
    assert os.environ["FONTCONFIG_FILE"] == "/etc/fonts/fonts.conf"


def test_activate_does_nothing_when_there_are_no_fonts_yet(tmp_path, monkeypatch):
    monkeypatch.delenv("FONTCONFIG_FILE", raising=False)
    empty = tmp_path / "fonts"
    empty.mkdir()
    assert fonts_stage.activate(empty) is None
    assert "FONTCONFIG_FILE" not in os.environ
