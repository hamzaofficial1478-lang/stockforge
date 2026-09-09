"""Designs with more than one printed surface.

A greeting card is a front and an inside. A wedding suite is an invitation, an
RSVP and a details card. The schema has said so from the first commit and no
test had ever built one — so only the first surface was ever checked for
distinctness or critiqued, and the rest were rendered, exported and shipped
unexamined. There was not even a record of which image each surface came from,
so the inside of a card could only have been judged against a photograph of
its front.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from stockforge import providers
from stockforge.config import Settings
from stockforge.pipeline import Pipeline
from stockforge.schema import (
    Background, Box, ColourRole, Critique, DesignSpec, FontClass, Grid,
    MotifElement, MotifKind, ShapeElement, TextElement, TypeRole,
)
from stockforge.sources import open_source
from stockforge.stages import analyse as analyse_stage
from stockforge.stages import derive as derive_stage

from conftest import build_font_library, build_motif_library
from test_pipeline import ScriptedProvider


def _card(folder: Path, stem: str) -> None:
    """A greeting card as exported: the front, and the inside."""
    folder.mkdir(parents=True, exist_ok=True)

    front = np.full((700, 500, 3), (240, 246, 250), np.uint8)
    cv2.rectangle(front, (60, 60), (440, 190), (110, 143, 125), -1)
    cv2.rectangle(front, (110, 330), (390, 400), (40, 43, 43), -1)
    cv2.imwrite(str(folder / f"{stem}-1.png"), front)

    inside = np.full((700, 500, 3), (250, 250, 246), np.uint8)
    for y in (180, 260, 340, 420, 500):
        cv2.rectangle(inside, (90, y), (410, y + 30), (55, 55, 52), -1)
    cv2.imwrite(str(folder / f"{stem}-2.png"), inside)


class _TwoSurfaces(ScriptedProvider):
    """Reads the listing as a card: a front and an inside, one image each."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.surface = -1

    def _survey(self):
        self.nth += 1
        return analyse_stage.Survey(
            category="greeting card", occasion="wedding", style_tags=["botanical"],
            surfaces=[
                analyse_stage.Surface(name="front", image_index=0,
                                      width_mm=127, height_mm=178),
                analyse_stage.Surface(name="inside", image_index=1,
                                      width_mm=127, height_mm=178),
            ],
            mockup_indices=[], confidence=0.8, notes="")

    def _typeread(self):
        self.surface += 1
        drop = 0.10 * self.surface
        return analyse_stage.TypeRead(
            elements=[
                TextElement(role=TypeRole.TITLE, content=f"Surface {self.surface}",
                            box=Box(x=0.1, y=0.30 + drop, w=0.8, h=0.14),
                            font=FontClass(category="serif", weight=400),
                            size_ratio=0.045, placeholder=True),
                TextElement(role=TypeRole.BODY, content="with every good wish",
                            box=Box(x=0.1, y=0.58 + drop, w=0.8, h=0.06),
                            font=FontClass(category="sans", weight=400),
                            size_ratio=0.020),
            ],
            grid=Grid(margin_x=0.09, margin_y=0.08),
            type_pairing=[FontClass(category="serif", weight=400)])

    def _structureread(self):
        return analyse_stage.StructureRead(
            background=Background(treatment="solid", base=ColourRole.BACKGROUND),
            shapes=[ShapeElement(primitive="line",
                                 box=Box(x=0.3, y=0.72, w=0.4, h=0.004),
                                 stroke=ColourRole.ACCENT)],
            motifs=[MotifElement(
                motif=MotifKind.BOTANICAL,
                description="a sprig of eucalyptus, oval leaves on a slender stem",
                box=Box(x=0.40 + 0.05 * self.surface, y=0.10, w=0.18, h=0.22))],
            motif_vocabulary=["eucalyptus"])


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("FONTCONFIG_FILE", raising=False)
    build_font_library(tmp_path / "fonts")
    build_motif_library(tmp_path / "motifs")
    return Settings(root=tmp_path / "work", fonts_dir=tmp_path / "fonts", preserve_original=False,
                    motifs_dir=tmp_path / "motifs")


def _build(workspace, tmp_path, provider):
    providers.set_provider("vision", provider)
    providers.set_provider("reason", provider)
    _card(tmp_path / "exports", "wedding-card")
    pipe = Pipeline(workspace)
    pipe.pull(open_source("folder", str(tmp_path / "exports")))
    design_id = pipe.store.designs()[0]["id"]
    return pipe, design_id, pipe.build(design_id)


# --- reading two surfaces -------------------------------------------------

def test_a_card_comes_back_as_two_surfaces(workspace, tmp_path):
    pipe, design_id, state = _build(workspace, tmp_path, _TwoSurfaces())

    reason = pipe.store.conn.execute(
        "SELECT reason FROM review WHERE design_id=?", (design_id,)).fetchone()
    assert state in ("ready", "master_only"), reason["reason"] if reason else state

    spec = DesignSpec.model_validate(pipe.store.get_read(design_id))
    assert [p.name for p in spec.pages] == ["front", "inside"]


def test_each_surface_remembers_the_image_it_was_read_from(workspace, tmp_path):
    """Without this every surface was compared against the first image of the
    listing, whatever it actually showed."""
    pipe, design_id, _ = _build(workspace, tmp_path, _TwoSurfaces())
    spec = DesignSpec.model_validate(pipe.store.get_read(design_id))

    sources = [p.source_image for p in spec.pages]
    assert all(sources), "a surface with no source is a surface nothing can judge"
    assert sources[0] != sources[1]
    assert all(Path(s).is_file() for s in sources)


# --- checking two surfaces ------------------------------------------------

def test_both_surfaces_are_judged_not_just_the_first(workspace, tmp_path):
    provider = _TwoSurfaces()
    _build(workspace, tmp_path, provider)

    assert provider.seen.count("Distinctiveness") == 2, \
        "only the first surface was checked for standing on its own"
    assert provider.seen.count("Critique") == 2, \
        "only the first surface was art-directed"


def test_each_surface_is_judged_against_its_own_image(workspace, tmp_path):
    """Recording the source is only half of it. The inside has to actually be
    compared against the inside, not against a picture of the front."""

    class _Watching(_TwoSurfaces):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.judged: list[Path] = []

        def structured(self, system, user_text, images, model, **kw):
            if model.__name__ == "Distinctiveness":
                self.judged.append(images[0])       # the source it was given
            return super().structured(system, user_text, images, model, **kw)

    provider = _Watching()
    pipe, design_id, _ = _build(workspace, tmp_path, provider)

    assert len(provider.judged) == 2
    assert provider.judged[0] != provider.judged[1], \
        "both surfaces were judged against the same image"

    spec = DesignSpec.model_validate(pipe.store.get_read(design_id))
    assert [str(p) for p in provider.judged] == [p.source_image for p in spec.pages]


def test_both_surfaces_produce_their_own_files(workspace, tmp_path):
    pipe, design_id, _ = _build(workspace, tmp_path, _TwoSurfaces())
    out = workspace.root / "out" / design_id[:16]

    names = sorted(p.name for p in out.glob("*-master.pdf"))
    assert len(names) == 2
    assert any("front" in n for n in names) and any("inside" in n for n in names)
    assert len(list(out.glob("*.eps"))) == 2


def test_both_surfaces_are_fingerprinted(workspace, tmp_path):
    pipe, design_id, _ = _build(workspace, tmp_path, _TwoSurfaces())
    rows = pipe.store.fingerprints()
    assert sorted(r["page_name"] for r in rows) == ["front", "inside"]


def test_a_second_surface_that_is_too_close_holds_the_whole_design(workspace, tmp_path):
    """One file of a suite reading as a copy is enough to get the batch
    flagged, so it has to be enough to hold the design."""

    class _SecondIsACopy(_TwoSurfaces):
        def _distinctiveness(self):
            self.checks = getattr(self, "checks", 0) + 1
            if self.checks % 2 == 1:
                return derive_stage.Distinctiveness(
                    distinct=0.9, same_family=0.9, verdict="ship")
            return derive_stage.Distinctiveness(
                distinct=0.2, same_family=0.9, verdict="derive_further",
                what_still_reads_as_copied=["the whole inside"])

    pipe, design_id, state = _build(workspace, tmp_path, _SecondIsACopy())
    assert state == "review"
    reason = pipe.store.conn.execute(
        "SELECT reason FROM review WHERE design_id=?", (design_id,)).fetchone()["reason"]
    assert "reads as a copy" in reason


def test_a_critic_escalating_on_the_inside_names_the_surface(workspace, tmp_path):
    class _InsideIsWrong(_TwoSurfaces):
        def _critique(self):
            self.crits = getattr(self, "crits", 0) + 1
            if self.crits % 2 == 1:
                return Critique(similarity=0.8, polish=0.8, verdict="ship")
            return Critique(similarity=0.3, polish=0.3, verdict="escalate",
                            commentary="the inside has lost its rule")

    pipe, design_id, state = _build(workspace, tmp_path, _InsideIsWrong())
    assert state == "review"
    reason = pipe.store.conn.execute(
        "SELECT reason FROM review WHERE design_id=?", (design_id,)).fetchone()["reason"]
    assert "inside" in reason


# --- falling back ---------------------------------------------------------

def test_a_surface_whose_source_has_gone_still_gets_judged(workspace, tmp_path):
    """Specs read before this was recorded have no source, and a workspace can
    be moved. Neither should stop the design being checked."""
    pipe = Pipeline(workspace)
    fallback = tmp_path / "fallback.png"
    _card(tmp_path, "x")
    fallback.write_bytes((tmp_path / "x-1.png").read_bytes())

    class _Page:
        source_image = None
    assert pipe._source_for(_Page(), fallback) == fallback

    class _Gone:
        source_image = str(tmp_path / "not-here.png")
    assert pipe._source_for(_Gone(), fallback) == fallback
