"""Shared fixtures. Fonts and motifs are built here rather than shipped, so
the suite runs on a machine with nothing installed."""

import json
from pathlib import Path

import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen


def build_font(path: Path, family: str = "Testface", cap_height: int = 700,
               advance: int = 600, upem: int = 1000) -> Path:
    """A font with metrics we chose, so a test can assert on widths."""
    # Letters, digits and the punctuation the fixtures actually set. The
    # renderer now reports characters a face has no glyph for, so a test font
    # that cannot set "Amelia & Jonah" sends every fixture design to review for
    # a fault in the fixture rather than in the code.
    letters = ([chr(c) for c in range(0x41, 0x5B)] + [chr(c) for c in range(0x61, 0x7B)]
               + [chr(c) for c in range(0x30, 0x3A)]
               + list("&.,'\"-—–:;!?()/@#%+*"))
    order = [".notdef", "space"] + [f"g{i:03d}" for i in range(len(letters))]
    names = {c: f"g{i:03d}" for i, c in enumerate(letters)}
    fb = FontBuilder(upem, isTTF=True)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap({32: "space", **{ord(c): names[c] for c in letters}})

    pen = TTGlyphPen(None)
    pen.moveTo((0, 0))
    pen.lineTo((advance - 200, 0))
    pen.lineTo((advance - 200, cap_height))
    pen.lineTo((0, cap_height))
    pen.closePath()
    mark, blank = pen.glyph(), TTGlyphPen(None).glyph()

    fb.setupGlyf({n: (blank if n in (".notdef", "space") else mark) for n in order})
    fb.setupHorizontalMetrics({n: (advance, 0) for n in order})
    fb.setupHorizontalHeader(ascent=int(upem * 0.8), descent=-int(upem * 0.2))
    fb.setupNameTable({"familyName": family, "styleName": "Regular",
                       "uniqueFontIdentifier": family.lower(),
                       "fullName": f"{family} Regular",
                       "psName": f"{family}-Regular", "version": "1.0"})
    fb.setupOS2(sCapHeight=cap_height, sTypoAscender=int(upem * 0.8),
                sTypoDescender=-int(upem * 0.2))
    fb.setupPost()
    path.parent.mkdir(parents=True, exist_ok=True)
    fb.save(str(path))
    return path


def build_font_library(fonts_dir: Path) -> Path:
    """One face per category the matcher might ask for."""
    entries = []
    for family, category in [("Testserif", "serif"), ("Testsans", "sans"),
                             ("Testscript", "script"), ("Testdisplay", "display")]:
        build_font(fonts_dir / f"{family}.ttf", family=family)
        entries.append({"path": f"{family}.ttf", "family": family, "style": "Regular",
                        "category": category, "weight": 400, "contrast": "medium",
                        "width": "normal", "mood": [], "licence": "OFL",
                        "embeddable": True})
    (fonts_dir / "manifest.json").write_text(json.dumps(entries, indent=2))
    return fonts_dir


MOTIF = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"
     data-kind="{kind}" data-tags="{tags}">
  <title>{title}</title>
  <ellipse cx="50" cy="50" rx="34" ry="28"/>
</svg>
"""


def build_motif_library(motifs_dir: Path) -> Path:
    motifs_dir.mkdir(parents=True, exist_ok=True)
    for stem, kind, tags, title in [
        ("sprig-eucalyptus-01", "botanical", "eucalyptus, sprig, leaves, greenery",
         "Eucalyptus sprig"),
        ("rule-thin-01", "rule", "rule, divider, hairline", "Thin rule"),
        ("frame-arch-01", "frame", "arch, frame, border", "Arched frame"),
    ]:
        (motifs_dir / f"{stem}.svg").write_text(
            MOTIF.format(kind=kind, tags=tags, title=title))
    return motifs_dir


@pytest.fixture(autouse=True)
def clean_environment():
    """Give every test the environment it started with.

    Two things here reach for os.environ on purpose: the panel sets a saved
    setting on the running process so it takes effect without a restart, and
    the font stage puts our own folder on fontconfig's search path. Both are
    right, and both leak between tests without this.

    The provider registry goes with them. It caches per role and set_provider
    pins one, so a stub installed by one test would otherwise answer for every
    test after it.
    """
    import os

    from stockforge import providers
    from stockforge.config import settings

    before = dict(os.environ)
    providers.reset()
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(before)
        settings.reload()
        providers.reset()


@pytest.fixture
def fonts_dir(tmp_path):
    return build_font_library(tmp_path / "fonts")


@pytest.fixture
def motifs_dir(tmp_path):
    return build_motif_library(tmp_path / "motifs")
