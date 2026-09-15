"""Objects that are pictures, not drawings.

The owner sells printables, and was clear about what "editable" means to the
people who buy them:

    "if designer made design he picks the objects from stock marketplaces and
     made the real things to be editable like texts ... the main thing to be
     editable is fonts not the design ... he will only edit the texts instead
     of objects"

Which is how the trade actually works. A buyer changes the names and the date
and prints it; nobody opens a card to redraw the pumpkin. So an object in this
library may be a painted picture — from an image model, or cut out of the
owner's own artwork — placed, sized and positioned like any other object, while
the type stays live.

What a picture gives up is recolouring, which is one attribute on a path and a
repaint job on a photograph. That trade was made deliberately.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from stockforge import providers
from stockforge.config import Settings
from stockforge.pipeline import Pipeline, out_dir_for
from stockforge.schema import Box, MotifElement, MotifKind
from stockforge.stages import motifs as m
from stockforge.stages.invent import Brief, available_motifs

from conftest import build_font_library, build_motif_library
from test_pipeline import ScriptedProvider


def _painted(path: Path) -> Path:
    """A shaded object, the way an image model returns one — the case a trace
    would flatten and ruin."""
    art = np.zeros((420, 320, 4), np.uint8)
    for i in range(120):
        v = 235 - i // 3
        cv2.ellipse(art, (160, 170), (110 - i // 2, 130 - i // 2), 0, 0, 360,
                    (v, v, v, 255), -1)
    cv2.circle(art, (125, 150), 16, (35, 35, 40, 255), -1)
    cv2.circle(art, (195, 150), 16, (35, 35, 40, 255), -1)
    cv2.imwrite(str(path), art)
    return path


@pytest.fixture
def shop(tmp_path):
    build_font_library(tmp_path / "fonts")
    build_motif_library(tmp_path / "motifs")
    cfg = Settings(root=tmp_path / "work", fonts_dir=tmp_path / "fonts",
                   motifs_dir=tmp_path / "motifs", preserve_original=False,
                   collection="halloween-cards")
    cfg.ensure_dirs()
    providers.set_provider("vision", ScriptedProvider())
    providers.set_provider("reason", ScriptedProvider())
    pipe = Pipeline(cfg)
    pipe.store.ensure_collection("halloween-cards", "Halloween cards")
    return pipe, cfg


# --- a picture is a first-class object -------------------------------------

def test_a_picture_can_be_adopted_as_an_object(shop, tmp_path):
    _, cfg = shop
    entry = m.adopt(_painted(tmp_path / "ghost.png"), cfg.motifs_dir,
                    kind="seasonal", as_picture=True,
                    description="a soft painted ghost with dark eyes")

    assert entry.raster is True, "it was not recorded as a picture"
    assert entry.kind == "seasonal" and "ghost" in entry.description
    assert (cfg.motifs_dir / f"{entry.library_id}{m.RASTER_SUFFIX}").is_file()


def test_the_library_offers_pictures_alongside_drawings(shop, tmp_path):
    """The model is told what it may use. A picture the library holds and does
    not mention is a picture nothing will ever ask for."""
    _, cfg = shop
    m.adopt(_painted(tmp_path / "ghost.png"), cfg.motifs_dir, as_picture=True,
            description="a soft painted ghost with dark eyes")

    offered = available_motifs(cfg.motifs_dir)
    assert "a soft painted ghost with dark eyes" in offered
    assert "Eucalyptus sprig" in offered, "the drawings stopped being offered"


def test_a_design_asking_for_it_finds_it(shop, tmp_path):
    _, cfg = shop
    m.adopt(_painted(tmp_path / "ghost.png"), cfg.motifs_dir, as_picture=True,
            description="a soft painted ghost with dark eyes")

    el = MotifElement(motif=MotifKind.SEASONAL, box=Box(x=.3, y=.3, w=.4, h=.3),
                      description="a soft painted ghost with dark eyes")
    ranked = m.rank(el, m.load(cfg.motifs_dir))
    assert ranked and ranked[0][0].raster, f"it did not match the picture: {ranked[:1]}"


# --- and it reaches the finished file --------------------------------------

def _design_with(pipe, cfg, element):
    import stockforge.stages.invent as inv
    real = inv.invent

    def carrying(brief, provider=None):
        spec = real(brief, provider)
        spec.pages[0].elements.append(element)
        return spec

    inv.invent = carrying
    try:
        return pipe.invent(1, Brief(niche="halloween-cards"))
    finally:
        inv.invent = real


def test_the_picture_is_embedded_and_the_type_stays_live(shop, tmp_path):
    """The whole product in one assertion. Artwork is artwork — carried inside
    the file so it survives being moved off this machine — and the words are
    still words, which is the half a buyer edits."""
    pipe, cfg = shop
    m.adopt(_painted(tmp_path / "ghost.png"), cfg.motifs_dir, as_picture=True,
            description="a soft painted ghost with dark eyes")

    out = _design_with(pipe, cfg, MotifElement(
        motif=MotifKind.SEASONAL, description="a soft painted ghost with dark eyes",
        box=Box(x=0.28, y=0.30, w=0.44, h=0.34)))
    assert out["made"] == 1, out["note"]

    design_id = out["designs"][0]["design_id"]
    svg = next(iter((cfg.root / "renders").glob(f"{design_id[:16]}*.svg")))
    body = svg.read_text()

    assert "data:image/png;base64" in body, "the picture was not placed"
    assert body.count("<text") >= 2, "the type is no longer live text"

    files = {p.suffix for p in out_dir_for(cfg.root, design_id, "halloween-cards").glob("*")}
    assert {".pdf", ".png"} <= files, f"no deliverable: {sorted(files)}"


def test_a_design_carrying_a_picture_is_not_called_a_photocopy(shop, tmp_path):
    """The check that had to learn the difference. `photocopied_pages` catches
    a real failure — the model reading a whole card as one photograph, so the
    "rebuild" is the source image with text beside it. An object the owner
    placed on purpose is not that, however much of the page it covers."""
    pipe, cfg = shop
    m.adopt(_painted(tmp_path / "ghost.png"), cfg.motifs_dir, as_picture=True,
            description="a soft painted ghost with dark eyes")

    out = _design_with(pipe, cfg, MotifElement(
        motif=MotifKind.SEASONAL, description="a soft painted ghost with dark eyes",
        box=Box(x=0.05, y=0.05, w=0.90, h=0.80)))     # most of the page

    assert out["made"] == 1, out["note"]
    assert out["designs"][0]["state"] != "review", (
        "a big placed object was mistaken for a photocopy of the source")


# --- the rules it still keeps ----------------------------------------------

def test_a_drawing_wins_over_a_picture_of_the_same_name(shop, tmp_path):
    """Where both exist the drawing is used: it recolours and it scales without
    limit, and a picture does neither."""
    from stockforge.stages.render import _motif_file

    _, cfg = shop
    (cfg.motifs_dir / "twin.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        '<title>Twin</title><circle cx="50" cy="50" r="40"/></svg>\n')
    import shutil
    shutil.copyfile(_painted(tmp_path / "t.png"),
                    cfg.motifs_dir / f"twin{m.RASTER_SUFFIX}")

    assert _motif_file("twin", cfg.motifs_dir).suffix == ".svg"


def test_adopting_the_same_picture_again_replaces_it(shop, tmp_path):
    _, cfg = shop
    src = _painted(tmp_path / "ghost.png")
    a = m.adopt(src, cfg.motifs_dir, as_picture=True, description="a painted ghost")
    b = m.adopt(src, cfg.motifs_dir, as_picture=True, description="a painted ghost")

    assert a.library_id == b.library_id
    assert len(list(cfg.motifs_dir.glob(f"*{m.RASTER_SUFFIX}"))) == 1


def test_a_stray_png_is_not_treated_as_artwork(shop, tmp_path):
    """`_drawn/` and `_harvested/` are full of PNGs that are reference, not
    artwork, and somebody will one day copy one up a level. An object you can
    place has to have said so on purpose."""
    _, cfg = shop
    import shutil
    shutil.copyfile(_painted(tmp_path / "g.png"), cfg.motifs_dir / "stray.png")

    ids = [e.library_id for e in m.scan(cfg.motifs_dir)]
    assert "stray" not in ids, "a loose PNG became a placeable object"


# --- reporting a run that changed nothing ----------------------------------
#
# The owner ran `motifs trace --as-picture` over a library that already held a
# trace of every one of those pictures. It printed "32 adopted into
# assets\motifs" and not one of them would ever be drawn, because where a
# drawing and a picture share a name the drawing wins. A run that succeeds
# loudly and does nothing is worse than one that fails.

def test_pictures_a_drawing_will_shadow_are_reported(shop, tmp_path):
    _, cfg = shop
    m.adopt(_painted(tmp_path / "ghost.png"), cfg.motifs_dir, as_picture=True,
            description="a painted ghost")
    assert m.shadowed_by_a_drawing(cfg.motifs_dir) == [], "nothing shadows it yet"

    (cfg.motifs_dir / "a-painted-ghost.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        '<title>t</title><circle cx="50" cy="50" r="40"/></svg>\n')

    assert m.shadowed_by_a_drawing(cfg.motifs_dir) == ["a-painted-ghost"]


def test_the_command_line_says_so_and_says_what_to_do(tmp_path, monkeypatch, capsys):
    from stockforge import cli
    from stockforge.config import settings as live

    build_font_library(tmp_path / "fonts")
    build_motif_library(tmp_path / "motifs")
    harvested = tmp_path / "motifs" / m.HARVEST_DIR
    harvested.mkdir(parents=True)
    _painted(harvested / "ghost-abc.png")
    (harvested / "ghost-abc.json").write_text('{"description": "a painted ghost"}')
    (tmp_path / "motifs" / "a-painted-ghost.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        '<title>t</title><circle cx="50" cy="50" r="40"/></svg>\n')

    for key, value in {"SF_ROOT": str(tmp_path / "work"),
                       "SF_FONTS": str(tmp_path / "fonts"),
                       "SF_MOTIFS": str(tmp_path / "motifs")}.items():
        monkeypatch.setenv(key, value)
    live.reload()
    live.ensure_dirs()

    cli.main(["motifs", "trace", "--as-picture"])
    said = capsys.readouterr().out

    assert "will not be used" in said, f"it reported success and said nothing: {said}"
    assert "a-painted-ghost.svg" in said, "it did not name the file in the way"


def test_the_command_line_names_the_file_it_actually_wrote(tmp_path, monkeypatch, capsys):
    """It printed `-> name.svg` for every picture it adopted, because the
    suffix was hardcoded. The owner's log said one thing on the INFO line and
    the opposite on the line under it."""
    from stockforge import cli
    from stockforge.config import settings as live

    build_font_library(tmp_path / "fonts")
    build_motif_library(tmp_path / "motifs")
    harvested = tmp_path / "motifs" / m.HARVEST_DIR
    harvested.mkdir(parents=True)
    _painted(harvested / "ghost-abc.png")
    (harvested / "ghost-abc.json").write_text('{"description": "a painted ghost"}')

    for key, value in {"SF_ROOT": str(tmp_path / "work"),
                       "SF_FONTS": str(tmp_path / "fonts"),
                       "SF_MOTIFS": str(tmp_path / "motifs")}.items():
        monkeypatch.setenv(key, value)
    live.reload()
    live.ensure_dirs()

    cli.main(["motifs", "trace", "--as-picture"])
    said = capsys.readouterr().out

    assert "a-painted-ghost.art.png" in said
    assert "-> a-painted-ghost.svg" not in said, "it named a file it did not write"


def test_adopting_a_picture_does_not_log_about_tracing(tmp_path, caplog):
    """The log said `replacing X.svg — same picture, traced again` while
    adopting a picture, because the trace path ran first and then the picture
    branch quietly took over. Two contradictory lines about one file."""
    import logging

    (tmp_path / "a-painted-ghost.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        '<title>t</title><circle cx="50" cy="50" r="40"/></svg>\n')
    with caplog.at_level(logging.INFO, logger="stockforge.motifs"):
        m.adopt(_painted(tmp_path / "g.png"), tmp_path, as_picture=True,
                description="a painted ghost")

    assert "traced again" not in caplog.text, caplog.text


# --- a decision nobody can make from a log ---------------------------------
#
# The owner has 32 objects that exist both ways and has to choose per object.
# A path count cannot tell them apart: a silhouette traced to one path is
# perfect and recolours with the design; a watercolour traced to three has had
# everything that made it a watercolour thrown away. Both read the same in a
# log. So render them side by side and let the person who knows the shop look.

def test_the_comparison_sheet_shows_both_versions(shop, tmp_path):
    from stockforge.stages.trace import compare_sheet

    _, cfg = shop
    src = _painted(tmp_path / "ghost.png")
    m.adopt(src, cfg.motifs_dir, description="a painted ghost")
    m.adopt(src, cfg.motifs_dir, description="a painted ghost", as_picture=True)

    out, count = compare_sheet(cfg.motifs_dir, tmp_path / "sheet.png")
    assert count == 1
    sheet = cv2.imread(str(out))
    assert sheet is not None and sheet.shape[1] > sheet.shape[0] // 2, (
        "the sheet is not a side-by-side")


def test_only_objects_that_exist_both_ways_are_compared(shop, tmp_path):
    """A picture with no trace, or a trace with no picture, is not a choice —
    putting it on the sheet asks the owner to decide something already decided."""
    from stockforge.stages.trace import compare_sheet

    _, cfg = shop
    both = _painted(tmp_path / "both.png")
    m.adopt(both, cfg.motifs_dir, description="in both forms")
    m.adopt(both, cfg.motifs_dir, description="in both forms", as_picture=True)
    m.adopt(_painted(tmp_path / "only.png"), cfg.motifs_dir,
            description="only a picture", as_picture=True)

    _, count = compare_sheet(cfg.motifs_dir, tmp_path / "sheet.png")
    assert count == 1, "it put an object with nothing to compare on the sheet"


def test_nothing_to_compare_says_so_rather_than_writing_a_blank(shop, tmp_path):
    from stockforge.stages.trace import compare_sheet

    _, cfg = shop
    with pytest.raises(ValueError, match="nothing to compare"):
        compare_sheet(cfg.motifs_dir, tmp_path / "sheet.png")
    assert not (tmp_path / "sheet.png").exists()


def test_the_comparison_sheet_does_not_need_cairo(shop, tmp_path, monkeypatch):
    """It did, and that is how it reached the owner broken:

        no library called "cairo-2" was found
        cannot load library 'libcairo-2.dll'

    `cairosvg` needs a native cairo DLL that a pip install does not bring, and
    the machine already had the Inkscape this project uses for every PDF and
    EPS it writes. Calling cairosvg directly walked past it.
    """
    import builtins
    from stockforge.stages.trace import compare_sheet

    _, cfg = shop
    src = _painted(tmp_path / "ghost.png")
    m.adopt(src, cfg.motifs_dir, description="a painted ghost")
    m.adopt(src, cfg.motifs_dir, description="a painted ghost", as_picture=True)

    real = builtins.__import__

    def without_cairo(name, *a, **k):
        if name.startswith("cairosvg"):
            raise OSError('no library called "cairo-2" was found')
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", without_cairo)
    out, count = compare_sheet(cfg.motifs_dir, tmp_path / "sheet.png")

    sheet = cv2.imread(str(out))
    assert sheet is not None and count == 1
    left = sheet[34:, : sheet.shape[1] // 2]
    assert int((left < 240).sum()) > 400, "the drawing half came out blank"


def test_no_converter_at_all_says_so_rather_than_handing_over_blanks(shop, tmp_path, monkeypatch):
    """Half an empty sheet is worse than an error: it asks the owner to compare
    their pictures against nothing and looks like the pictures won."""
    import builtins
    from stockforge.stages import trace as trace_mod
    from stockforge.stages.trace import compare_sheet

    _, cfg = shop
    src = _painted(tmp_path / "ghost.png")
    m.adopt(src, cfg.motifs_dir, description="a painted ghost")
    m.adopt(src, cfg.motifs_dir, description="a painted ghost", as_picture=True)

    real = builtins.__import__

    def nothing_works(name, *a, **k):
        if name.startswith("cairosvg"):
            raise OSError('no library called "cairo-2" was found')
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", nothing_works)
    monkeypatch.setattr("stockforge.stages.export._inkscape", lambda: None)

    with pytest.raises(ValueError, match="nothing to compare them against"):
        compare_sheet(cfg.motifs_dir, tmp_path / "sheet.png")
    assert not (tmp_path / "sheet.png").exists()
