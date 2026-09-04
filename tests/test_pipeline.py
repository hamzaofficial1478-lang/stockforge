"""The whole pipeline, end to end, with only the model replaced.

Every stage of this project worked on its own long before any two of them had
ever run in sequence. This is the test that chains them: real images off disk,
real flattening, real font and motif matching, real SVG, real Inkscape export.
The only thing standing in for a GPU is a provider that answers each of the
small schemas with something a competent model would have said.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from stockforge import providers
from stockforge.config import Settings
from stockforge.pipeline import Pipeline
from stockforge.providers.base import VisionProvider
from stockforge.schema import (
    Background, Box, ColourRole, Critique, DesignSpec, FontClass, Grid,
    MotifElement, MotifKind, Provenance, ShapeElement, TextElement, TypeRole,
)
from stockforge.sources import open_source
from stockforge.stages import analyse as analyse_stage
from stockforge.stages import derive as derive_stage

from conftest import build_font_library, build_motif_library


# --------------------------------------------------------------------------
# a provider that answers like a model that read the brief
# --------------------------------------------------------------------------

class ScriptedProvider(VisionProvider):
    """Answers each analysis pass with a plausible, valid object.

    It overrides `structured` rather than `chat` on purpose: the JSON recovery
    loop is already covered by its own tests, and what this test is for is
    whether the stages fit together.
    """

    name = "scripted"

    def __init__(self, occasion: str = "wedding", accent: str = "#7d8f6e",
                 flagged: bool = False):
        self.occasion = occasion
        self.accent = accent
        self.flagged = flagged
        self.seen: list[str] = []

    def chat(self, system, user_text, images, **kw):
        raise AssertionError("structured() is overridden; chat should not be reached")

    def structured(self, system, user_text, images, model, **kw):
        self.seen.append(model.__name__)
        return getattr(self, f"_{model.__name__.lower()}")()

    # --- the five analysis passes ------------------------------------
    def _survey(self):
        return analyse_stage.Survey(
            category="invitation", occasion=self.occasion,
            style_tags=["botanical", "minimal"],
            surfaces=[analyse_stage.Surface(name="invitation", image_index=0,
                                            width_mm=127, height_mm=178)],
            mockup_indices=[1], confidence=0.82, notes="")

    def _paletteread(self):
        return analyse_stage.PaletteRead(
            assignments=[
                analyse_stage.RoleAssignment(hex="#faf6f0", role=ColourRole.BACKGROUND),
                analyse_stage.RoleAssignment(hex="#2b2b28", role=ColourRole.INK),
                analyse_stage.RoleAssignment(hex=self.accent, role=ColourRole.ACCENT),
            ], temperature="warm", contrast="high")

    def _provenance(self):
        if self.flagged:
            return Provenance(built_with="canva", raster_elements=["a painted scene"],
                              third_party_suspected=True,
                              reason="a Canva badge on the listing")
        return Provenance(built_with="illustrator", raster_elements=[],
                          third_party_suspected=False)

    def _metadatadraft(self):
        from stockforge.publish.metadata import MetadataDraft

        return MetadataDraft(
            title="Botanical wedding invitation template with eucalyptus sprigs",
            keywords=["wedding", "invitation", "botanical", "eucalyptus", "greenery"],
            category="Graphic Resources")

    def _typeread(self):
        return analyse_stage.TypeRead(
            elements=[
                TextElement(role=TypeRole.EYEBROW, content="together with their families",
                            box=Box(x=0.1, y=0.34, w=0.8, h=0.05),
                            font=FontClass(category="sans", weight=400, mood=["clean"]),
                            size_ratio=0.016, tracking=0.18),
                TextElement(role=TypeRole.TITLE, content="Amelia & Jonah",
                            box=Box(x=0.1, y=0.42, w=0.8, h=0.14),
                            font=FontClass(category="serif", weight=400,
                                           contrast="high", mood=["elegant"]),
                            size_ratio=0.045, placeholder=True),
                TextElement(role=TypeRole.BODY, content="Saturday the fourteenth of June",
                            box=Box(x=0.1, y=0.58, w=0.8, h=0.06),
                            font=FontClass(category="serif", weight=400),
                            size_ratio=0.020, placeholder=True),
                TextElement(role=TypeRole.SIGNOFF, content="Reception to follow",
                            box=Box(x=0.1, y=0.76, w=0.8, h=0.06),
                            font=FontClass(category="script", weight=400),
                            size_ratio=0.022),
            ],
            grid=Grid(margin_x=0.09, margin_y=0.08, symmetry="centred"),
            type_pairing=[FontClass(category="serif", weight=400, contrast="high"),
                          FontClass(category="sans", weight=400)])

    def _structureread(self):
        return analyse_stage.StructureRead(
            background=Background(treatment="solid", base=ColourRole.BACKGROUND),
            shapes=[ShapeElement(primitive="line", box=Box(x=0.35, y=0.70, w=0.30, h=0.004),
                                 stroke=ColourRole.ACCENT)],
            motifs=[
                MotifElement(motif=MotifKind.BOTANICAL,
                             description="a sprig of eucalyptus, oval leaves on a slender stem",
                             box=Box(x=0.10, y=0.08, w=0.16, h=0.22)),
                MotifElement(motif=MotifKind.BOTANICAL,
                             description="a sprig of eucalyptus, mirrored",
                             box=Box(x=0.74, y=0.08, w=0.16, h=0.22), flip_x=True),
            ],
            motif_vocabulary=["eucalyptus", "botanical"])

    # --- derivation and critique -------------------------------------
    def _newcopy(self):
        return derive_stage.NewCopy(replacements=["Rosa & Elliot",
                                                  "Sunday the sixth of September"])

    def _distinctiveness(self):
        return derive_stage.Distinctiveness(
            distinct=0.78, same_family=0.85, verdict="ship",
            what_still_reads_as_copied=[], suggestion="")

    def _critique(self):
        return Critique(similarity=0.82, polish=0.79, verdict="ship",
                        patches=[], commentary="reads well")


# --------------------------------------------------------------------------

def _listing(folder: Path, stem: str, accent=(110, 143, 125)) -> None:
    """One listing: a flat artwork file plus a staged mockup, as Etsy exports."""
    folder.mkdir(parents=True, exist_ok=True)

    flat = np.full((700, 500, 3), (240, 246, 250), np.uint8)
    cv2.rectangle(flat, (60, 60), (440, 200), accent, -1)
    for i, y in enumerate((300, 380, 460, 560)):
        cv2.rectangle(flat, (80 + i * 6, y), (420, y + 44), (40, 43, 43), -1)
    cv2.imwrite(str(folder / f"{stem}-1.png"), flat)

    mockup = np.full((700, 700, 3), (180, 190, 200), np.uint8)
    mockup[120:560, 160:480] = cv2.resize(flat, (320, 440))
    cv2.imwrite(str(folder / f"{stem}-2.png"), mockup)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("FONTCONFIG_FILE", raising=False)
    build_font_library(tmp_path / "fonts")
    build_motif_library(tmp_path / "motifs")
    return Settings(root=tmp_path / "work",
                    fonts_dir=tmp_path / "fonts",
                    motifs_dir=tmp_path / "motifs")


def test_a_design_goes_all_the_way_through(workspace, tmp_path):
    provider = ScriptedProvider()
    providers.set_provider("vision", provider)
    providers.set_provider("reason", provider)

    _listing(tmp_path / "exports", "wedding-invite")
    pipe = Pipeline(workspace)

    assert pipe.pull(open_source("folder", str(tmp_path / "exports"))) == 1
    assert pipe.status()["designs"] == 1
    assert pipe.status()["images"] == 2, "both listing images belong to the one design"

    design_id = pipe.store.designs()[0]["id"]
    state = pipe.build(design_id)

    reason = pipe.store.conn.execute(
        "SELECT reason FROM review WHERE design_id=?", (design_id,)).fetchone()
    assert state in ("ready", "master_only"), reason["reason"] if reason else state

    # the spec survived the round trip and is a real design
    spec = DesignSpec.model_validate(pipe.store.get_spec(design_id))
    assert spec.dna.occasion == "wedding"
    assert len(spec.pages) == 1
    assert len(spec.texts()) == 4
    assert spec.unresolved_motifs() == [], "the eucalyptus is in the library"
    assert all(m.library_id == "sprig-eucalyptus-01" for m in spec.motifs())

    # the placeholders were rewritten, so it is not the same piece of paper
    assert spec.texts()[1].content == "Rosa & Elliot"

    # and there are files on disk to show for it
    out = workspace.root / "out" / design_id[:16]
    assert (out / f"{design_id[:16]}-invitation-master.pdf").is_file()
    assert (out / f"{design_id[:16]}-invitation.eps").is_file()
    assert (out / f"{design_id[:16]}-invitation-preview.jpg").is_file()


def test_the_analysis_passes_all_ran(workspace, tmp_path):
    provider = ScriptedProvider()
    providers.set_provider("vision", provider)
    providers.set_provider("reason", provider)

    _listing(tmp_path / "exports", "wedding-invite")
    pipe = Pipeline(workspace)
    pipe.pull(open_source("folder", str(tmp_path / "exports")))
    pipe.build(pipe.store.designs()[0]["id"])

    assert {"Survey", "PaletteRead", "Provenance", "TypeRead",
            "StructureRead"} <= set(provider.seen)


def test_a_second_design_mixes_ingredients_from_the_first(workspace, tmp_path):
    provider = ScriptedProvider()
    providers.set_provider("vision", provider)
    providers.set_provider("reason", provider)

    exports = tmp_path / "exports"
    _listing(exports, "invite-one")
    pipe = Pipeline(workspace)
    pipe.pull(open_source("folder", str(exports)))
    pipe.build(pipe.store.designs()[0]["id"])

    # a second listing, different palette, same occasion — an eligible donor
    provider.accent = "#8f6e7d"
    _listing(exports, "invite-two", accent=(125, 110, 143))
    pipe.pull(open_source("folder", str(exports)))
    second = [r for r in pipe.store.designs() if r["state"] == "pending"][0]["id"]

    assert pipe.build(second) in ("ready", "master_only", "review")
    pool = pipe._spec_pool(exclude=second)
    assert pool, "the first design should be available to mix from"


def test_pulling_again_does_not_throw_away_work_already_done(workspace, tmp_path):
    """Adding ten listings and re-running the pull is the most ordinary thing
    there is. It must not send the other 4,990 back round the loop."""
    provider = ScriptedProvider()
    providers.set_provider("vision", provider)
    providers.set_provider("reason", provider)

    exports = tmp_path / "exports"
    _listing(exports, "invite-one")
    pipe = Pipeline(workspace)
    pipe.pull(open_source("folder", str(exports)))

    first = pipe.store.designs()[0]["id"]
    pipe.build(first)
    before = pipe.store.conn.execute(
        "SELECT state, stock_safe FROM designs WHERE id=?", (first,)).fetchone()
    assert before["state"] != "pending"

    _listing(exports, "invite-two")
    assert pipe.pull(open_source("folder", str(exports))) == 2

    after = pipe.store.conn.execute(
        "SELECT state, stock_safe FROM designs WHERE id=?", (first,)).fetchone()
    assert after["state"] == before["state"], "a finished design went back to pending"
    assert after["stock_safe"] == before["stock_safe"], "its provenance verdict was erased"
    assert [r["id"] for r in pipe.store.designs(state="pending")] != [first]


def test_pulling_again_does_not_re_flatten_what_we_already_hold(workspace, tmp_path):
    provider = ScriptedProvider()
    providers.set_provider("vision", provider)
    providers.set_provider("reason", provider)

    exports = tmp_path / "exports"
    _listing(exports, "invite-one")
    pipe = Pipeline(workspace)
    pipe.pull(open_source("folder", str(exports)))

    flats = sorted((workspace.root / "flats").glob("*.png"))
    stamps = {p.name: p.stat().st_mtime_ns for p in flats}
    assert stamps

    pipe.pull(open_source("folder", str(exports)))
    again = {p.name: p.stat().st_mtime_ns for p in (workspace.root / "flats").glob("*.png")}
    assert again == stamps, "the same images were flattened a second time"


def test_an_image_used_by_two_listings_belongs_to_both(workspace, tmp_path):
    """A shop puts the same size chart, the same 'instant download' graphic and
    the same mockup backdrop on every listing it has. Keying assets on the
    bytes alone gave that image to whichever listing was pulled first, and a
    listing whose images were all shared ended up with none and failed."""
    import shutil

    provider = ScriptedProvider()
    providers.set_provider("vision", provider)
    providers.set_provider("reason", provider)

    exports = tmp_path / "exports"
    _listing(exports, "invite-one")
    # the second listing reuses the first one's images, byte for byte
    for n in (1, 2):
        shutil.copy(exports / f"invite-one-{n}.png", exports / f"invite-two-{n}.png")

    pipe = Pipeline(workspace)
    assert pipe.pull(open_source("folder", str(exports))) == 2

    for row in pipe.store.designs():
        linked = pipe.store.conn.execute(
            "SELECT COUNT(*) n FROM assets WHERE design_id=?", (row["id"],)).fetchone()
        assert linked["n"] == 2, f"{row['design_key']} lost its images"

    assert pipe.status()["images"] == 2, "still only two distinct images"
    for row in pipe.store.designs():
        assert pipe.build(row["id"]) != "failed"


def _built(workspace, tmp_path, provider) -> Pipeline:
    providers.set_provider("vision", provider)
    providers.set_provider("reason", provider)
    _listing(tmp_path / "exports", "wedding-invite")
    pipe = Pipeline(workspace)
    pipe.pull(open_source("folder", str(tmp_path / "exports")))
    pipe.build(pipe.store.designs()[0]["id"])
    return pipe


def test_a_flagged_design_gets_its_master_and_goes_no_further(workspace, tmp_path):
    pipe = _built(workspace, tmp_path, ScriptedProvider(flagged=True))

    design = pipe.store.designs()[0]
    assert design["state"] == "master_only"
    assert (workspace.root / "out" / design["id"][:16]).is_dir(), "the master is still made"
    assert pipe.deliverable() == ([], [])
    assert pipe.held_back() == 1


def test_publish_all_sends_a_flagged_design_after_all(workspace, tmp_path):
    """The setting is documented as the owner's escape hatch. The command line
    was refusing exactly the designs it exists to let through."""
    workspace.publish_all = True
    pipe = _built(workspace, tmp_path, ScriptedProvider(flagged=True))

    assert pipe.store.designs()[0]["state"] == "ready"
    rows, files = pipe.deliverable()
    assert len(files) == 1
    assert rows[0].title.startswith("Botanical wedding invitation")


def test_metadata_is_written_once_and_kept(workspace, tmp_path):
    """`publish --dry-run` then `publish` is the normal way to use this.
    Drafting a title and keywords twice for the same file is a model call each
    time, and on a local card that is an hour for nothing."""
    provider = ScriptedProvider()
    pipe = _built(workspace, tmp_path, provider)

    rows, files = pipe.deliverable()
    assert files
    first = provider.seen.count("MetadataDraft")
    assert first == len(files)

    again_rows, again_files = pipe.deliverable()
    assert provider.seen.count("MetadataDraft") == first, "it drafted them a second time"
    assert [r.title for r in again_rows] == [r.title for r in rows]
    assert again_files == files


def test_the_run_loop_reports_what_it_did(workspace, tmp_path):
    provider = ScriptedProvider()
    providers.set_provider("vision", provider)
    providers.set_provider("reason", provider)

    _listing(tmp_path / "exports", "wedding-invite")
    pipe = Pipeline(workspace)
    pipe.pull(open_source("folder", str(tmp_path / "exports")))

    tally = pipe.run()
    assert sum(tally.values()) == 1
    assert "failed" not in tally, tally
