"""The starter font library: enough faces to set a card, and real weights.

The shop is greetings cards, invitations and seasonal designs. Eight families
— one sans, one serif, two scripts and four oddments — is not a library you can
lay one out from, and the complaint was exactly that: the type comes back
looking the same every time because there is almost nothing to choose between.

Two things had to change for a bigger library to be possible at all. The files
now come from raw.githubusercontent rather than the GitHub API, because sixty
unauthenticated requests an hour does not cover forty-two families. And most of
the good families are published as one variable file rather than a file per
weight, so they are cut into static weights on install — otherwise a library of
forty-two families would contain no bold at all.
"""

import hashlib
import json
from pathlib import Path

import pytest

from stockforge.schema import FontClass
from stockforge.stages import font_download, fonts

from conftest import build_font, build_variable_font
from test_ui import panel, post_raw  # noqa: F401

CATALOGUE = json.loads(
    (Path(__file__).resolve().parent.parent
     / "stockforge" / "data" / "starter-fonts.json").read_text(encoding="utf-8"))


def _install(tmp_path, monkeypatch, resources, files):
    """Install a catalogue whose downloads come from a dict rather than a server."""
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(resources))
    monkeypatch.setattr(font_download, "CATALOG", path)
    monkeypatch.setattr(font_download, "get",
                        lambda url, **kw: files[url])
    library = tmp_path / "library"
    return library, font_download.download_starter(library)


# --- cutting weights out of a variable font -------------------------------

def _variable_catalogue(tmp_path):
    data = build_variable_font(tmp_path / "source.ttf").read_bytes()
    resources = [{
        "path": "varyface/Varyface[wght].ttf",
        "url": "https://example.invalid/Varyface[wght].ttf",
        "sha256": hashlib.sha256(data).hexdigest(),
        "font": {"category": "serif", "contrast": "high", "mood": ["elegant"]},
        "instances": [{"style": "Regular", "wght": 400}, {"style": "Bold", "wght": 700}],
    }]
    return resources, {"https://example.invalid/Varyface[wght].ttf": data}


def test_a_variable_font_installs_as_the_weights_it_was_asked_for(tmp_path, monkeypatch):
    resources, files = _variable_catalogue(tmp_path)
    library, entries = _install(tmp_path, monkeypatch, resources, files)

    styles = {e.style: e for e in entries if e.family == "Varyface"}
    assert set(styles) == {"Regular", "Bold"}, f"got {sorted(styles)}"
    assert styles["Regular"].weight == 400
    assert styles["Bold"].weight == 700


def test_the_cuts_are_actually_different_weights(tmp_path, monkeypatch):
    """The failure this exists for is silent and total: pin the axis wrong, or
    lose `updateFontNames`, and you get two files with different names and
    identical outlines. Every heading then sets at the same weight as its body
    text and nothing anywhere reports a problem."""
    from fontTools.ttLib import TTFont

    resources, files = _variable_catalogue(tmp_path)
    library, entries = _install(tmp_path, monkeypatch, resources, files)

    widths = {}
    for entry in entries:
        with TTFont(str(library / entry.path)) as tt:
            widths[entry.style] = tt["glyf"]["g000"].xMax
    # Measured on the fixture: 400 at Regular, 580 at Bold. Asserting on the
    # order rather than the numbers, since the fixture's delta may change.
    assert widths["Bold"] > widths["Regular"] * 1.1, (
        f"the Bold cut has the Regular's outlines: {widths}")


def test_the_variable_original_is_not_in_the_library(tmp_path, monkeypatch):
    """It would scan as a third face, at weight 400, called Regular — a
    duplicate of the cut sitting beside it and a coin toss which one the
    matcher picks."""
    resources, files = _variable_catalogue(tmp_path)
    library, entries = _install(tmp_path, monkeypatch, resources, files)

    assert (library / font_download.VARIABLE_DIR / "varyface/Varyface[wght].ttf").is_file(), (
        "the variable font was not kept, so the next install re-downloads it")
    assert not any(font_download.VARIABLE_DIR in e.path for e in entries)
    assert len([e for e in entries if e.family == "Varyface"]) == 2


def test_the_cuts_carry_the_licence_and_the_tags(tmp_path, monkeypatch):
    """Tags live on the variable file in the catalogue, and the faces that come
    out of it are different files. An untagged face is an unembeddable one as
    far as the matcher is concerned, so both cuts would be invisible."""
    resources, files = _variable_catalogue(tmp_path)
    library, entries = _install(tmp_path, monkeypatch, resources, files)

    for entry in entries:
        assert entry.embeddable, f"{entry.path} would never be picked"
        assert entry.licence == "OFL-1.1"
        assert entry.category == "serif"
        assert entry.mood == ["elegant"]


def test_a_bold_cut_of_an_italic_stays_italic(tmp_path, monkeypatch):
    """`Family-Italic[wght].ttf` cut at 700 is a Bold Italic. Naming it
    `Italic-Bold` would make the style read as neither, and the italic request
    the renderer makes would fall through to another family."""
    data = build_variable_font(tmp_path / "source.ttf").read_bytes()
    url = "https://example.invalid/Varyface-Italic[wght].ttf"
    resources = [{
        "path": "varyface/Varyface-Italic[wght].ttf", "url": url,
        "sha256": hashlib.sha256(data).hexdigest(),
        "instances": [{"style": "Regular", "wght": 400}, {"style": "Bold", "wght": 700}],
    }]
    library, _ = _install(tmp_path, monkeypatch, resources, {url: data})

    written = {p.name for p in library.rglob("*.ttf")
               if font_download.VARIABLE_DIR not in p.parts}
    assert written == {"Varyface-Italic.ttf", "Varyface-BoldItalic.ttf"}, written


def test_a_font_that_cannot_be_cut_costs_one_family_not_the_install(tmp_path, monkeypatch, caplog):
    """A variable font with no STAT table cannot have its names rewritten.
    That is one family missing from the menu; it is not a reason to leave
    someone with no fonts at all."""
    good_data = build_variable_font(tmp_path / "good.ttf").read_bytes()
    plain = build_font(tmp_path / "plain.ttf", family="Plainface").read_bytes()
    urls = {"https://example.invalid/good": good_data,
            "https://example.invalid/plain": plain}
    resources = [
        {"path": "plainface/Plainface-Regular.ttf", "url": "https://example.invalid/plain",
         "sha256": hashlib.sha256(plain).hexdigest(), "font": {"category": "sans"}},
        # A plain static font declared as if it were variable: instancing it
        # raises, which is the same shape of failure as a missing STAT table.
        {"path": "broken/Broken[wght].ttf", "url": "https://example.invalid/plain",
         "sha256": hashlib.sha256(plain).hexdigest(),
         "instances": [{"style": "Bold", "wght": 700}]},
        {"path": "varyface/Varyface[wght].ttf", "url": "https://example.invalid/good",
         "sha256": hashlib.sha256(good_data).hexdigest(),
         "instances": [{"style": "Regular", "wght": 400}]},
    ]
    library, entries = _install(tmp_path, monkeypatch, resources, urls)

    families = {e.family for e in entries}
    assert "Broken" not in families, (
        "the fixture cut cleanly, so this test is not exercising the failure")
    assert "Plainface" in families and "Varyface" in families, (
        f"one family that would not cut took the rest down with it: {families}")
    assert caplog.records, "it failed silently — nothing says which family is missing"
    assert "Broken" in caplog.text


def test_installing_twice_downloads_nothing_the_second_time(tmp_path, monkeypatch):
    resources, files = _variable_catalogue(tmp_path)
    library, _ = _install(tmp_path, monkeypatch, resources, files)

    monkeypatch.setattr(font_download, "get",
                        lambda *a, **k: pytest.fail("downloaded again"))
    entries = font_download.download_starter(library)
    assert len(entries) == 2


def test_a_cut_that_went_missing_comes_back(tmp_path, monkeypatch):
    """Someone deletes a face, or a cut fails halfway. The variable original is
    still there and verified, so it is re-cut from that rather than re-fetched
    — and the point is that it comes back at all."""
    resources, files = _variable_catalogue(tmp_path)
    library, _ = _install(tmp_path, monkeypatch, resources, files)
    (library / "varyface/Varyface-Bold.ttf").unlink()

    entries = font_download.download_starter(library)
    assert (library / "varyface/Varyface-Bold.ttf").is_file()
    assert len(entries) == 2


# --- folders the scan should leave alone ----------------------------------

def test_scan_ignores_folders_that_start_with_an_underscore(tmp_path):
    build_font(tmp_path / "real.ttf", family="Real")
    build_font(tmp_path / "_variable" / "hidden.ttf", family="Hidden")
    build_font(tmp_path / "_kept" / "deeper" / "also.ttf", family="Also")

    found = {e.family for e in fonts.scan(tmp_path)}
    assert found == {"Real"}, found


# --- what actually ships --------------------------------------------------

def test_the_catalogue_is_a_library_rather_than_a_sample():
    """The reported problem, as a number. Eight families is a sample; laying
    out a card needs a choice within each category, not one of each."""
    families = {Path(r["path"]).parts[0] for r in CATALOGUE if r["path"].endswith(".ttf")}
    assert len(families) >= 40, f"only {len(families)} families"


@pytest.mark.parametrize("category,least", [
    ("script", 8),      # the shop is invitations: scripts do the most work
    ("serif", 8),
    ("sans", 6),
    ("display", 5),
    ("slab", 2),
    ("mono", 2),
])
def test_every_category_has_something_to_choose_between(category, least):
    """The matcher asks for a category and a weight. One face in a category
    means every design that wants it gets the same face, which is what made
    five thousand outputs look like each other."""
    have = {Path(r["path"]).parts[0] for r in CATALOGUE
            if r.get("font", {}).get("category") == category}
    assert len(have) >= least, f"{category}: only {len(have)} — {sorted(have)}"


def test_every_family_ships_its_licence():
    """These are OFL fonts and the licence travels with them. Shipping the
    outlines without the text is the one thing that would make the whole
    library unusable for stock submission — which is why it exists."""
    families = {Path(r["path"]).parts[0] for r in CATALOGUE if r["path"].endswith(".ttf")}
    licensed = {Path(r["path"]).parts[0] for r in CATALOGUE
                if Path(r["path"]).name == "OFL.txt"}
    assert families - licensed == set(), f"no OFL.txt for {sorted(families - licensed)}"


def test_every_font_in_the_catalogue_is_tagged():
    """An untagged face is never matched, so it is dead weight in the download
    and a family the user thinks they have and does not."""
    untagged = [r["path"] for r in CATALOGUE
                if r["path"].endswith((".ttf", ".otf")) and "font" not in r]
    assert untagged == [], untagged


def test_every_entry_is_pinned_to_a_commit_and_a_hash():
    """An unpinned URL is a font that changes under you between installs, and
    a missing hash is one nobody checked."""
    for resource in CATALOGUE:
        assert len(resource["sha256"]) == 64, resource["path"]
        assert "/google/fonts/" in resource["url"], resource["path"]
        pinned = resource["url"].split("/google/fonts/")[1].split("/")[0]
        assert len(pinned) == 40, f"{resource['path']} is not pinned to a commit"


def test_bold_is_available_in_the_families_that_carry_the_headings():
    """A library that is all one weight cannot set a heading against a body.
    Cutting the variable fonts is what provides this, so it is worth naming."""
    with_bold = {Path(r["path"]).parts[0] for r in CATALOGUE
                 if any(i["wght"] >= 700 for i in r.get("instances", []))
                 or "Bold" in Path(r["path"]).name}
    assert len(with_bold) >= 15, f"only {len(with_bold)} families have a bold"


# --- reaching it from the panel -------------------------------------------

def test_the_panel_can_install_the_library(panel, monkeypatch):
    """It was a terminal command, and the terminal is not where this program is
    used. Someone whose designs all come back in one face has no way to guess
    that the answer is `stockforge fonts download`."""
    from stockforge.stages import font_download as fd

    base, cfg = panel
    data = build_variable_font(cfg.root / "src.ttf").read_bytes()
    catalogue = cfg.root / "catalog.json"
    catalogue.write_text(json.dumps([{
        "path": "varyface/Varyface[wght].ttf", "url": "https://example.invalid/v",
        "sha256": hashlib.sha256(data).hexdigest(),
        "font": {"category": "serif"},
        "instances": [{"style": "Regular", "wght": 400}, {"style": "Bold", "wght": 700}],
    }]))
    monkeypatch.setattr(fd, "CATALOG", catalogue)
    monkeypatch.setattr(fd, "get", lambda *a, **k: data)

    status, body = post_raw(base, "/api/fonts/download", {})
    assert status == 200, body
    # The workspace already has a small library; the point is what was added.
    assert "Varyface" in body["names"]
    assert body["faces"] >= 2 and body["where"] == str(cfg.fonts_dir)
    assert (cfg.fonts_dir / "varyface/Varyface-Regular.ttf").is_file()
    assert (cfg.fonts_dir / "varyface/Varyface-Bold.ttf").is_file()


def test_a_failed_install_says_so_rather_than_five_hundred(panel, monkeypatch):
    from stockforge.stages import font_download as fd

    base, cfg = panel
    catalogue = cfg.root / "catalog.json"
    catalogue.write_text(json.dumps([{
        "path": "x/y.ttf", "url": "https://example.invalid/y", "sha256": "0" * 64}]))
    monkeypatch.setattr(fd, "CATALOG", catalogue)
    monkeypatch.setattr(fd, "get", lambda *a, **k: b"not that font")

    status, body = post_raw(base, "/api/fonts/download", {})
    assert status == 400
    assert "Checksum" in body["error"]
