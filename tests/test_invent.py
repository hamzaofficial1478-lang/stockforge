"""Designs written from a brief, with nothing read first.

The owner, after the learning phase went in:

    "if we are having a good vision llm model i think there will be no any
     need to make program firstly read 24 designs"

Half right, and the half that is right matters. The twenty-four exist because
the mixer builds a design out of parts of designs it has read — take the bag of
ingredients away and there is nothing to mix. But a model can write a layout
from a brief, and the renderer has never cared where a spec came from. So this
is a second way to make something, and it needs no seeding at all.

What it costs is the thing to keep honest about: the result is not derived from
the owner's catalogue, so it carries the shop's style only as far as the brief
described it, and it is one model call per design where the batch path does
forty-eight in one.
"""

import sys
from pathlib import Path

import pytest

from stockforge import providers
from stockforge.config import Settings
from stockforge.pipeline import Pipeline, out_dir_for
from stockforge.stages.invent import Brief, available_motifs, invent

from conftest import build_font_library, build_motif_library
from test_pipeline import ScriptedProvider


@pytest.fixture
def shop(tmp_path):
    build_font_library(tmp_path / "fonts")
    build_motif_library(tmp_path / "motifs")
    cfg = Settings(root=tmp_path / "work", fonts_dir=tmp_path / "fonts",
                   motifs_dir=tmp_path / "motifs", preserve_original=False)
    cfg.ensure_dirs()
    providers.set_provider("vision", ScriptedProvider())
    providers.set_provider("reason", ScriptedProvider())
    pipe = Pipeline(cfg)
    pipe.store.ensure_collection("halloween-cards", "Halloween cards")
    return pipe, cfg


# --- the point of the whole thing -----------------------------------------

def test_a_design_is_made_with_nothing_read_at_all(shop):
    """No sources pulled, no designs read, no donor pool, no twenty-four — and
    a finished file set at the end. That is the entire claim."""
    pipe, cfg = shop
    assert not pipe.store.designs(), "this test is meant to start from nothing"

    out = pipe.invent(1, Brief(niche="halloween-cards", occasion="halloween"))

    assert out["made"] == 1, out["note"]
    design_id = out["designs"][0]["design_id"]
    folder = out_dir_for(cfg.root, design_id, "halloween-cards")
    got = {p.suffix for p in folder.glob("*")}
    assert {".pdf", ".svg", ".eps"} <= got, f"no deliverable files: {sorted(got)}"


def test_it_is_filed_in_the_niche_it_was_asked_for(shop):
    """The wall between niches is the one rule that holds however a design came
    to exist. A Halloween card written from a brief is still a Halloween card."""
    pipe, cfg = shop
    out = pipe.invent(1, Brief(niche="halloween-cards"))
    did = out["designs"][0]["design_id"]
    row = pipe.store.conn.execute(
        "SELECT collection FROM designs WHERE id=?", (did,)).fetchone()
    assert row["collection"] == "halloween-cards"
    assert out_dir_for(cfg.root, did, "halloween-cards").is_dir()


def test_nothing_is_made_without_a_niche(shop):
    """Same rule as the mixer: making business cards out of Halloween cards is
    one instruction away, and it is not a mistake you spot in a batch."""
    pipe, cfg = shop
    cfg.collection = ""
    out = pipe.invent(2, Brief(niche=""))
    assert out["made"] == 0
    assert "niche" in out["note"].lower(), out["note"]


def test_a_niche_that_does_not_exist_is_not_invented_on_the_fly(shop):
    pipe, _ = shop
    out = pipe.invent(1, Brief(niche="business-cards"))
    assert out["made"] == 0 and "no niche" in out["note"].lower(), out["note"]


# --- the thing that made every early attempt useless ----------------------

def test_the_model_is_told_what_the_library_can_draw(tmp_path):
    """The single most useful line in the prompt.

    Without it the model asks for whatever a designer would want, most of it
    is not in the library, and every design comes out with an empty space and
    a trip to review — which is exactly what the first run of this did, three
    for three, on a motif that was sitting in the library under another form of
    words.
    """
    build_motif_library(tmp_path / "motifs")
    said = available_motifs(tmp_path / "motifs")
    assert said, "the library described itself as empty"

    prompt = Brief(niche="halloween-cards", available=said).as_prompt()
    assert "ONLY these" in prompt and said[0] in prompt, prompt


def test_an_empty_library_is_told_to_use_no_motifs(tmp_path):
    """Better a design carried on type and colour than one full of holes."""
    empty = tmp_path / "none"
    empty.mkdir()
    prompt = Brief(niche="x", available=available_motifs(empty)).as_prompt()
    assert "no drawings available" in prompt.lower(), prompt


def test_the_motifs_are_pointed_at_real_drawings(shop):
    """`build` resolves motifs after reading and this path did not, so every
    invented design came out with library_id unset and the renderer reported a
    hole for a drawing that was in the library all along. Three for three to
    review, for nothing."""
    pipe, _ = shop
    out = pipe.invent(1, Brief(niche="halloween-cards"))
    assert out["made"] == 1, out["note"]
    assert out["designs"][0]["state"] != "review", (
        "a design whose motifs are all in the library still went to review")


# --- it must not hand back the same design twice --------------------------

def test_designs_it_has_already_made_are_named_in_the_brief(shop):
    """Cheap half of not repeating yourself: say what the shop has before the
    drawing rather than discovering it after."""
    pipe, _ = shop
    pipe.invent(1, Brief(niche="halloween-cards"))

    seen = {}

    class Watching(ScriptedProvider):
        def structured(self, system, user_text, images, model, **kw):
            if model.__name__ == "Invented":
                seen["prompt"] = user_text
            return super().structured(system, user_text, images, model, **kw)

    providers.set_provider("vision", Watching())
    pipe.invent(1, Brief(niche="halloween-cards"))
    assert "Already made" in seen.get("prompt", ""), seen.get("prompt", "")[:400]


def test_a_repeat_is_thrown_away_and_replaced_rather_than_handed_back(shop):
    """The guarantee, as opposed to the hint above. A model that writes the
    same design every time must produce one design, not a pile of identical
    ones — and must say so instead of silently returning fewer."""
    pipe, _ = shop

    class Stuck(ScriptedProvider):
        def _invented(self):
            self.nth = 0          # the same design, every single time
            return super()._invented()

    providers.set_provider("vision", Stuck())
    out = pipe.invent(3, Brief(niche="halloween-cards"))

    assert out["made"] == 1, f"it handed back {out['made']} copies of one design"
    assert out["discarded"] >= 1, "nothing was recognised as a repeat"
    assert out["note"], "it came up short and said nothing about why"


# --- what it says about itself --------------------------------------------

def test_an_invented_design_says_it_was_invented(shop):
    """Six months from now the only way to tell an invented design from a
    recovered one is if it says so on the design."""
    pipe, _ = shop
    out = pipe.invent(1, Brief(niche="halloween-cards"))
    spec = pipe.store.get_spec(out["designs"][0]["design_id"])
    assert any("written from a brief" in w for w in spec["warnings"]), spec["warnings"]


def test_it_is_publishable_because_nothing_third_party_is_in_it(shop):
    """The layout came from a model; every mark on the page is set in our own
    fonts and drawn from our own motif library. That is the same footing a
    mixed design stands on, and it is why this may go to an agency."""
    pipe, _ = shop
    out = pipe.invent(1, Brief(niche="halloween-cards"))
    spec = pipe.store.get_spec(out["designs"][0]["design_id"])
    assert spec["provenance"]["third_party_suspected"] is False
    assert spec["provenance"]["stock_safe"] is True


def test_no_image_is_sent_because_there_is_nothing_to_look_at(shop):
    """It runs on a text model as happily as a vision one, and costs a
    fraction of a read. Sending an image would quietly make that untrue."""
    pipe, _ = shop
    carried = []

    class Watching(ScriptedProvider):
        def structured(self, system, user_text, images, model, **kw):
            if model.__name__ == "Invented":
                carried.append(len(images))
            return super().structured(system, user_text, images, model, **kw)

    providers.set_provider("vision", Watching())
    pipe.invent(1, Brief(niche="halloween-cards"))
    assert carried == [0], f"it sent {carried} image(s) to write a design"


# --- reachable from both front doors --------------------------------------

def test_the_panel_can_write_designs(tmp_path, monkeypatch):
    """The owner works from the panel, so a feature only reachable from the
    command line is a feature they do not have."""
    import json as _json
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer
    from stockforge.ui.server import Handler

    build_font_library(tmp_path / "fonts")
    build_motif_library(tmp_path / "motifs")
    cfg = Settings(root=tmp_path / "work", fonts_dir=tmp_path / "fonts",
                   motifs_dir=tmp_path / "motifs", preserve_original=False,
                   collection="halloween-cards")
    cfg.ensure_dirs()
    providers.set_provider("vision", ScriptedProvider())
    providers.set_provider("reason", ScriptedProvider())
    Pipeline(cfg).store.ensure_collection("halloween-cards", "Halloween cards")

    monkeypatch.setattr(Handler, "cfg", cfg)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05},
                     daemon=True).start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{srv.server_address[1]}/api/invent",
            data=_json.dumps({"count": 1, "occasion": "halloween"}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as fh:
            got = _json.loads(fh.read())
    finally:
        srv.shutdown()

    assert got["made"] == 1, got.get("note")
    assert got["collection"] == "halloween-cards"


def test_the_command_line_can_write_designs(tmp_path, monkeypatch, capsys):
    from stockforge import cli

    build_font_library(tmp_path / "fonts")
    build_motif_library(tmp_path / "motifs")
    for key, value in {"SF_ROOT": str(tmp_path / "work"),
                       "SF_FONTS": str(tmp_path / "fonts"),
                       "SF_MOTIFS": str(tmp_path / "motifs"),
                       "SF_COLLECTION": "halloween-cards",
                       "SF_PRESERVE_ORIGINAL": "0"}.items():
        monkeypatch.setenv(key, value)
    from stockforge.config import settings as live
    live.reload()
    live.ensure_dirs()
    providers.set_provider("vision", ScriptedProvider())
    providers.set_provider("reason", ScriptedProvider())
    Pipeline(live).store.ensure_collection("halloween-cards", "Halloween cards")

    code = cli.main(["invent", "1", "--occasion", "halloween"])
    said = capsys.readouterr().out
    assert code == 0, said
    assert "design(s) written" in said, said


# --- the words have to fit the box ----------------------------------------
#
# Run against real Gemini through a local bridge, three designs, all three to
# review: "type does not fit its box: title 'A WICKED NIGHT' at 43% of its
# intended size". The prompt was then given the arithmetic in words, the model
# followed it correctly, and it was STILL 43% — because the rule as written
# left out two things a prompt cannot carry. `size_ratio` is a cap height as a
# fraction of the canvas HEIGHT, the em is that over the face's own cap ratio,
# and the box is a fraction of the WIDTH. Getting between them needs the page
# aspect and the metrics of a font file nobody has opened yet.
#
# So it is measured in code instead, with the same face and the same measure
# the renderer uses.

def test_type_too_big_for_its_box_is_brought_down_before_it_is_drawn(tmp_path):
    from stockforge.schema import (Box, Canvas, DesignDNA, FontClass, Page,
                                   Palette, Swatch, ColourRole, TextElement,
                                   TypeRole, DesignSpec)
    from stockforge.stages.invent import fit_type

    build_font_library(tmp_path / "fonts")
    huge = TextElement(role=TypeRole.TITLE, content="A WICKED NIGHT",
                       box=Box(x=0.1, y=0.2, w=0.8, h=0.15),
                       font=FontClass(category="serif", weight=400),
                       size_ratio=0.16)
    spec = DesignSpec(
        source_asset_id="x", confidence=0.6,
        dna=DesignDNA(category="invitation", occasion="halloween",
                      palette=Palette(swatches=[
                          Swatch(role=ColourRole.BACKGROUND, hex="#ffffff", coverage=0.8),
                          Swatch(role=ColourRole.INK, hex="#000000", coverage=0.2)])),
        pages=[Page(name="front", canvas=Canvas(width_mm=127, height_mm=178),
                    elements=[huge])])

    changed = fit_type(spec, tmp_path / "fonts")
    assert changed, "a 14-character title at 0.16 on a 5x7 was left as it was"
    assert huge.size_ratio < 0.16, "the size did not come down"
    assert huge.box.h >= huge.size_ratio * 1.4, (
        "the box did not come down with it, so the line floats in it")


def test_type_that_already_fits_is_left_alone(tmp_path):
    """The other side. A pass that shrinks everything would quietly flatten
    every design's hierarchy, which is worse than the fault it fixes."""
    from stockforge.schema import (Box, Canvas, DesignDNA, FontClass, Page,
                                   Palette, Swatch, ColourRole, TextElement,
                                   TypeRole, DesignSpec)
    from stockforge.stages.invent import fit_type

    build_font_library(tmp_path / "fonts")
    small = TextElement(role=TypeRole.BODY, content="Oct 31",
                        box=Box(x=0.1, y=0.6, w=0.8, h=0.06),
                        font=FontClass(category="sans", weight=400),
                        size_ratio=0.025)
    spec = DesignSpec(
        source_asset_id="x", confidence=0.6,
        dna=DesignDNA(category="invitation", occasion="halloween",
                      palette=Palette(swatches=[
                          Swatch(role=ColourRole.BACKGROUND, hex="#ffffff", coverage=0.8),
                          Swatch(role=ColourRole.INK, hex="#000000", coverage=0.2)])),
        pages=[Page(name="front", canvas=Canvas(width_mm=127, height_mm=178),
                    elements=[small])])

    assert fit_type(spec, tmp_path / "fonts") == []
    assert small.size_ratio == 0.025


def test_the_fitting_pass_runs_before_a_design_is_exported(shop):
    """It has to be wired in, not merely available: the whole point is that no
    invented design reaches the renderer needing to be shrunk."""
    pipe, _ = shop
    called = {}
    import stockforge.stages.invent as invent_mod
    real = invent_mod.fit_type

    def watch(spec, fonts_dir):
        called["yes"] = True
        return real(spec, fonts_dir)

    invent_mod.fit_type = watch
    try:
        pipe.invent(1, Brief(niche="halloween-cards"))
    finally:
        invent_mod.fit_type = real
    assert called.get("yes"), "invented designs go to the renderer unfitted"


# --- working in the spirit of a design you already have --------------------
#
# The owner's workflow, in their words: "i added design like a photo or a link,
# program sync that and made me new design on the bases of" it. Twelve designs
# from one command and one reference, with no reading and no donor pool.

def test_the_reference_picture_is_actually_shown_to_the_model(shop, tmp_path):
    """Otherwise --like is a flag that changes a log line and nothing else."""
    import cv2
    import numpy as np

    pipe, _ = shop
    ref = tmp_path / "reference.png"
    cv2.imwrite(str(ref), np.full((400, 280, 3), 240, np.uint8))

    seen = {}

    class Watching(ScriptedProvider):
        def structured(self, system, user_text, images, model, **kw):
            if model.__name__ == "Invented":
                seen["images"] = [Path(i).name for i in images]
                seen["system"] = system
            return super().structured(system, user_text, images, model, **kw)

    providers.set_provider("vision", Watching())
    pipe.invent(1, Brief(niche="halloween-cards", like=ref))

    assert seen.get("images") == ["reference.png"], (
        f"the reference never reached the model: {seen.get('images')}")
    assert "in its spirit" in seen.get("system", ""), (
        "it was shown a picture with no instruction about what to do with it")


def test_without_a_reference_no_image_is_sent(shop):
    """The plain path stays cheap: a brief with no reference is words only, and
    runs on a text model."""
    pipe, _ = shop
    carried = []

    class Watching(ScriptedProvider):
        def structured(self, system, user_text, images, model, **kw):
            if model.__name__ == "Invented":
                carried.append(len(images))
            return super().structured(system, user_text, images, model, **kw)

    providers.set_provider("vision", Watching())
    pipe.invent(1, Brief(niche="halloween-cards"))
    assert carried == [0]


def test_a_reference_can_be_a_design_already_pulled_in(shop, tmp_path):
    """So "pull this link, then make me designs like it" is one continuous
    thing rather than a hunt through the workspace for the file it landed in."""
    from stockforge.sources import open_source
    from test_pipeline import _listing

    pipe, cfg = shop
    _listing(tmp_path / "in", "card")
    pipe.pull(open_source("folder", str(tmp_path / "in")))
    design_id = pipe.store.designs()[0]["id"]

    found = pipe._picture_for(design_id[:8])
    assert found is not None and found.is_file(), (
        f"the id {design_id[:8]} did not resolve to its artwork")


def test_a_reference_that_is_neither_a_file_nor_a_design_is_survivable(shop):
    """A typo in a path must not lose the run — the brief still works without
    a picture, and saying so beats stopping."""
    pipe, _ = shop
    assert pipe._picture_for("no-such-thing-anywhere") is None
    out = pipe.invent(1, Brief(niche="halloween-cards", like="no-such-thing-anywhere"))
    assert out["made"] == 1, out["note"]
