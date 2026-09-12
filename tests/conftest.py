"""Shared fixtures. Fonts and motifs are built here rather than shipped, so
the suite runs on a machine with nothing installed."""

import json
import os
import tempfile
from pathlib import Path

# Before stockforge.config is imported anywhere, because it reads .env at
# import. Without this the suite reads whatever .env is sitting in the checkout
# — a real one, with the developer's own thresholds and keys in it — and tests
# pass or fail on settings that have nothing to do with the change under test.
# Not hypothetical: a motif threshold of 0.9 in a local .env sent four worker
# tests and two pipeline tests to review and read exactly like a broken matcher.
#
# A fresh folder each run, not one fixed path. Tests of the panel save settings
# through the real endpoint, which writes to whatever `env_file()` returns — so
# a fixed path accumulates their values and hands them to every later run,
# which is the same bug wearing the fix's clothes.
_ENV_SANDBOX = tempfile.mkdtemp(prefix="stockforge-tests-")
os.environ["SF_ENV_FILE"] = str(Path(_ENV_SANDBOX) / ".env")
for _leaked in [k for k in os.environ if k.startswith("SF_") and k != "SF_ENV_FILE"]:
    del os.environ[_leaked]

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


def build_variable_font(path: Path, family: str = "Varyface") -> Path:
    """A one-axis variable font whose glyphs really do get fatter.

    The starter library is mostly variable now — a font per family rather than
    a font per weight — and it is cut into static weights on install. A fixture
    that only pretended to vary would let a cut that quietly produced four
    identical Regulars pass.

    The STAT table is not decoration: fontTools refuses to rewrite the name
    records without one, which is what turns a cut into a properly named Bold
    rather than another file called Regular.
    """
    from fontTools.otlLib.builder import buildStatTable
    from fontTools.ttLib import newTable
    from fontTools.ttLib.tables.TupleVariation import TupleVariation

    upem, advance, cap = 1000, 600, 700
    letters = [chr(c) for c in range(0x41, 0x5B)] + [chr(c) for c in range(0x61, 0x7B)]
    order = [".notdef", "space"] + [f"g{i:03d}" for i in range(len(letters))]
    fb = FontBuilder(upem, isTTF=True)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap({32: "space", **{ord(c): f"g{i:03d}" for i, c in enumerate(letters)}})

    pen = TTGlyphPen(None)
    pen.moveTo((0, 0))
    pen.lineTo((400, 0))
    pen.lineTo((400, cap))
    pen.lineTo((0, cap))
    pen.closePath()
    mark, blank = pen.glyph(), TTGlyphPen(None).glyph()
    fb.setupGlyf({n: (blank if n in (".notdef", "space") else mark) for n in order})
    fb.setupHorizontalMetrics({n: (advance, 0) for n in order})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": family, "styleName": "Regular",
                       "uniqueFontIdentifier": family.lower(), "fullName": family,
                       "psName": f"{family}-Regular", "version": "1.0"})
    fb.setupOS2(sTypoAscender=800, usWinAscent=800, usWinDescent=200, usWeightClass=400)
    fb.setupPost()
    fb.setupFvar(axes=[("wght", 400, 400, 900, "Weight")], instances=[])

    gvar = newTable("gvar")
    gvar.version, gvar.reserved = 1, 0
    gvar.variations = {
        name: [TupleVariation({"wght": (0.0, 1.0, 1.0)},
                              [(0, 0), (300, 0), (300, 0), (0, 0), None, None, None, None])]
        for name in order if name not in (".notdef", "space")}
    fb.font["gvar"] = gvar
    buildStatTable(fb.font, [{"tag": "wght", "name": "Weight", "values": [
        {"value": 400, "name": "Regular", "flags": 0x2, "linkedValue": 700},
        {"value": 700, "name": "Bold"},
        {"value": 900, "name": "Black"}]}])

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
