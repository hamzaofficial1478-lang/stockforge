"""Getting to twenty-four designs when the reading keeps failing.

A real run, two lanes, three designs:

    13:17:44 lane 2 failed: ... could not produce valid PaletteRead:
             no parseable JSON in response
    13:22:39 lane 2 failed: ... could not produce valid PaletteRead:
             no parseable JSON in response

Two of three designs dead, both on the palette, both models in the chain
failing the same way. And the gate that needs twenty-four read designs before a
niche can be mixed from is then a wall with no door in it — the count stops
going up and nothing on screen says why.

Two things follow. A pass that has a sane answer without a model must not be
able to kill a design: the colours are measured off the artwork before a model
is involved, and only their roles were being asked for. And what did fail has
to be visible and retryable in one action, or it sits there for ever.
"""

from pathlib import Path

import pytest
from pydantic import BaseModel

from stockforge import collections as niches
from stockforge import providers
from stockforge.config import Settings
from stockforge.pipeline import Pipeline
from stockforge.providers.base import ProviderError, VisionProvider
from stockforge.sources import open_source
from stockforge.stages.analyse import TypeRead, palette_from_pixels

from conftest import build_font_library, build_motif_library
from test_pipeline import ScriptedProvider, _listing


@pytest.fixture
def workspace(tmp_path):
    build_font_library(tmp_path / "fonts")
    build_motif_library(tmp_path / "motifs")
    cfg = Settings(root=tmp_path / "work", fonts_dir=tmp_path / "fonts",
                   motifs_dir=tmp_path / "motifs", preserve_original=True)
    cfg.ensure_dirs()
    _listing(tmp_path / "in", "card")
    return cfg, tmp_path


def _pulled(workspace, provider):
    cfg, tmp = workspace
    providers.set_provider("vision", provider)
    providers.set_provider("reason", provider)
    pipe = Pipeline(cfg)
    pipe.pull(open_source("folder", str(tmp / "in")))
    return pipe, pipe.store.designs()[0]["id"]


class _Breaks(ScriptedProvider):
    """Answers one pass the way NVIDIA's llama-3.2-11b did: prose, not JSON."""

    def __init__(self, which):
        super().__init__()
        self.which = which

    def structured(self, system, user_text, images, model, **kw):
        if model.__name__ == self.which:
            raise ProviderError(
                f"could not produce valid {self.which}: no parseable JSON in "
                f"response\nIt said: Sure! Here are the colours I can see...")
        return super().structured(system, user_text, images, model, **kw)


# --- the palette cannot kill a design -------------------------------------

def test_a_design_survives_its_palette_failing(workspace):
    """The reported failure, and the reason it should never have been fatal:
    the colours were already measured off the artwork. Only the question of
    which one is the paper went unanswered."""
    pipe, design_id = _pulled(workspace, _Breaks("PaletteRead"))
    assert pipe.build(design_id) != "failed"


def test_the_colours_are_still_right_when_nobody_named_them(workspace):
    pipe, design_id = _pulled(workspace, _Breaks("PaletteRead"))
    pipe.build(design_id)
    swatches = pipe.store.get_spec(design_id)["dna"]["palette"]["swatches"]

    roles = [s["role"] for s in swatches]
    assert "background" in roles and "ink" in roles
    paper = next(s for s in swatches if s["role"] == "background")
    assert paper["coverage"] >= max(s["coverage"] for s in swatches), (
        "the biggest area of the artwork was not called the paper")


def test_it_says_the_palette_was_worked_out_rather_than_read(workspace):
    """Quietly substituting a guess is worse than failing. The design carries
    the note, so Review can show it."""
    pipe, design_id = _pulled(workspace, _Breaks("PaletteRead"))
    pipe.build(design_id)
    warnings = pipe.store.get_spec(design_id)["warnings"]
    assert any("palette could not be read" in w for w in warnings), warnings


def test_an_unreadable_provenance_holds_the_design_back_rather_than_losing_it(workspace):
    """Stock agencies want you to hold the rights to every element in a
    submitted file, and getting it wrong costs the contributor account. So "we
    could not check" has to mean master-only, not carry on — and not die."""
    pipe, design_id = _pulled(workspace, _Breaks("Provenance"))
    assert pipe.build(design_id) == "master_only"
    assert pipe.store.get_spec(design_id)["provenance"]["stock_safe"] is False


@pytest.mark.parametrize("pass_name", ["TypeRead", "StructureRead"])
def test_the_passes_that_are_the_design_still_fail_it(workspace, pass_name):
    """The other side. Without the type or the shapes there is nothing to
    draw, and pretending otherwise would ship an empty page."""
    pipe, design_id = _pulled(workspace, _Breaks(pass_name))
    with pytest.raises(ProviderError):
        pipe.build(design_id)


# --- what the fallback actually does --------------------------------------

def test_the_biggest_area_is_the_paper_and_the_darkest_is_the_ink():
    made = palette_from_pixels([("#f7f9fa", .62), ("#1a1a1a", .18),
                                ("#e07b2a", .12), ("#2b6349", .08)])
    by_role = {s.role.value: s.hex for s in made.swatches}
    assert by_role["background"] == "#f7f9fa"
    assert by_role["ink"] == "#1a1a1a"
    assert by_role["accent"] == "#e07b2a", "the most saturated leftover is the accent"


def test_no_colours_at_all_is_still_a_palette():
    """An all-white export measures as nothing. A design with no palette at all
    would fail validation further down, which is a worse way to find out."""
    assert palette_from_pixels([]).swatches


# --- a failed read is not the end of it -----------------------------------

def test_everything_that_failed_goes_back_in_one_action(workspace):
    """The alternative is finding them by eye in a list of five thousand, which
    nobody does — so they sit there and the count towards a usable niche simply
    stops going up."""
    pipe, design_id = _pulled(workspace, _Breaks("TypeRead"))
    with pytest.raises(ProviderError):
        pipe.build(design_id)
    pipe.store.set_design_state(design_id, "failed")

    done = pipe.retry_failed()
    assert done["queued"] == 1
    assert pipe.store.designs(state="pending"), "it did not go back in the queue"


def test_retrying_clears_the_reason_it_stopped(workspace):
    """A stale reason on a design that is about to run again reads as a fresh
    failure, and the queue looks broken when it is working."""
    pipe, design_id = _pulled(workspace, ScriptedProvider())
    pipe.store.queue_review(design_id, "something went wrong earlier", 0.0)
    pipe.store.set_design_state(design_id, "failed")

    pipe.retry_failed()
    left = pipe.store.conn.execute(
        "SELECT 1 FROM review WHERE design_id=?", (design_id,)).fetchone()
    assert left is None


def test_a_retry_is_confined_to_the_niche_being_worked_on(workspace):
    pipe, design_id = _pulled(workspace, ScriptedProvider())
    pipe.store.ensure_collection("other", "Other")
    pipe.store.add_made_design("elsewhere00000000000000", "x", "x", collection="other")
    pipe.store.set_design_state("elsewhere00000000000000", "failed")
    pipe.store.set_design_state(design_id, "failed")

    done = pipe.retry_failed("unfiled")
    assert done["queued"] == 1
    assert pipe.store.conn.execute(
        "SELECT state FROM designs WHERE id='elsewhere00000000000000'"
    ).fetchone()["state"] == "failed", "it reached into another niche"


# --- and the wait says what is holding it up ------------------------------

def test_the_count_towards_a_usable_niche_names_what_is_stuck():
    ready, why = niches.ready(
        {"name": "Halloween cards", "read": 2, "designs": 5, "failed": 3}, 24)
    assert not ready
    assert "3 failed" in why and "retry" in why


def test_it_does_not_tell_you_to_pull_more_in_when_the_problem_is_failures():
    """Pulling in more designs when three are stuck makes the pile bigger and
    the problem no smaller."""
    _, why = niches.ready(
        {"name": "Halloween cards", "read": 2, "designs": 5, "failed": 3}, 24)
    assert "Pull in" not in why


def test_it_does_say_to_pull_more_in_when_that_is_the_problem():
    _, why = niches.ready({"name": "Halloween", "read": 2, "designs": 2, "failed": 0}, 24)
    assert "Pull in 22 more" in why


# --- read the niche in before drawing from it -----------------------------

def test_the_first_designs_are_read_not_varied(tmp_path):
    """From a real run, a niche holding two designs:

        round 1: worst distinct=0.10 -> derive_further
        round 2: worst distinct=0.10 -> derive_further
        ac131054 -> review: still reads as a copy after every derivation round

    Every ingredient in both rounds came from the same single donor, so the
    second draft was the first with its hue nudged and the score did not move.
    Three model calls and four minutes per design to arrive at something
    countable before the first one — and a review queue filled with designs
    whose only fault was that there was nothing to build them from.
    """
    build_font_library(tmp_path / "fonts")
    build_motif_library(tmp_path / "motifs")
    cfg = Settings(root=tmp_path / "work", fonts_dir=tmp_path / "fonts",
                   motifs_dir=tmp_path / "motifs", preserve_original=False)
    cfg.ensure_dirs()
    _listing(tmp_path / "in", "only-one")

    asked: list[str] = []

    class Watching(ScriptedProvider):
        def structured(self, system, user_text, images, model, **kw):
            asked.append(model.__name__)
            return super().structured(system, user_text, images, model, **kw)

    provider = Watching()
    providers.set_provider("vision", provider)
    providers.set_provider("reason", provider)
    pipe = Pipeline(cfg)
    pipe.pull(open_source("folder", str(tmp_path / "in")))
    design_id = pipe.store.designs()[0]["id"]

    assert pipe.build(design_id) == "master_only"
    assert "Distinctiveness" not in asked, (
        "it paid a model to score a variation it had nothing to build")
    warnings = pipe.store.get_spec(design_id)["warnings"]
    assert any("learning phase" in w for w in warnings), warnings


def test_the_design_after_the_last_seed_is_the_first_one_varied(tmp_path):
    """The boundary the owner asked for, at a size a test can run: with the
    niche needing four, designs one to four are read in and the fifth is the
    first one made. Both halves matter — a guard that never lets go is the
    same bug wearing the opposite coat."""
    build_font_library(tmp_path / "fonts")
    build_motif_library(tmp_path / "motifs")
    cfg = Settings(root=tmp_path / "work", fonts_dir=tmp_path / "fonts",
                   motifs_dir=tmp_path / "motifs", preserve_original=False,
                   seed_designs=4)
    cfg.ensure_dirs()
    for n in range(5):
        _listing(tmp_path / "in", f"card{n}")

    providers.set_provider("vision", ScriptedProvider())
    providers.set_provider("reason", ScriptedProvider())
    pipe = Pipeline(cfg)
    pipe.pull(open_source("folder", str(tmp_path / "in")))
    rows = pipe.store.designs(state="pending")

    seeds = [pipe.build(row["id"]) for row in rows[:4]]
    assert seeds == ["master_only"] * 4, (
        f"a seed design was varied before the niche was read in: {seeds}")
    assert pipe.build(rows[4]["id"]) != "master_only", (
        "the fifth design was refused a variation with four others behind it")


def test_a_gap_in_our_own_library_does_not_queue_a_review_while_learning(tmp_path):
    """Twenty-four designs each queueing "no library match for a jack-o'-lantern"
    is twenty-four rows saying one thing, and that one thing is already on the
    motif-gaps list ranked by how many designs want it. The owner draws from
    that list; nobody reads the rows."""
    build_font_library(tmp_path / "fonts")
    empty = tmp_path / "no-motifs"
    empty.mkdir()
    cfg = Settings(root=tmp_path / "work", fonts_dir=tmp_path / "fonts",
                   motifs_dir=empty, preserve_original=False)
    cfg.ensure_dirs()
    _listing(tmp_path / "in", "card")

    providers.set_provider("vision", ScriptedProvider())
    providers.set_provider("reason", ScriptedProvider())
    pipe = Pipeline(cfg)
    pipe.pull(open_source("folder", str(tmp_path / "in")))
    design_id = pipe.store.designs()[0]["id"]

    assert pipe.build(design_id) == "master_only"
    assert not pipe.store.conn.execute(
        "SELECT 1 FROM review WHERE design_id=?", (design_id,)).fetchone(), (
        "a motif we have not drawn yet was queued as a fault against the design")


def test_a_misread_source_still_queues_while_learning(tmp_path):
    """The other side of that: what the learning phase must not swallow is a
    bad READ. A surface that came back as placed photograph is not a design we
    can draw from, and letting it into the pool quietly is how twenty-four
    designs of training data become twenty-three."""
    from stockforge.schema import DesignSpec

    build_font_library(tmp_path / "fonts")
    build_motif_library(tmp_path / "motifs")
    cfg = Settings(root=tmp_path / "work", fonts_dir=tmp_path / "fonts",
                   motifs_dir=tmp_path / "motifs", preserve_original=False)
    cfg.ensure_dirs()
    _listing(tmp_path / "in", "card")

    providers.set_provider("vision", ScriptedProvider())
    providers.set_provider("reason", ScriptedProvider())
    pipe = Pipeline(cfg)
    pipe.pull(open_source("folder", str(tmp_path / "in")))
    design_id = pipe.store.designs()[0]["id"]
    pipe.build(design_id)

    spec = DesignSpec.model_validate(pipe.store.get_spec(design_id))
    flagged = []
    real = pipe.store.queue_review

    def watch(did, why, score):
        flagged.append(why)
        return real(did, why, score)

    pipe.store.queue_review = watch
    monkey = type(spec).photocopied_pages
    type(spec).photocopied_pages = lambda self, cap: [("cover", 0.9)]
    try:
        pipe._export(spec, design_id, distinct=0.0, master_only=True, learning=True)
    finally:
        type(spec).photocopied_pages = monkey

    assert any("not rebuilt" in why for why in flagged), (
        f"a surface read as placed photograph went into the pool unremarked: {flagged}")


# --- a photograph is not something to measure a rebuild against -----------

def test_an_uncropped_source_is_not_called_a_misread_trim(tmp_path):
    """From the same run:

        'cover': the rebuild is the wrong shape — 0.71 against the source's
        1.00, so the trim was misread

    The source is a square listing photograph that was never cropped to the
    card. The rebuild is 0.71 because that is what a 5x7 card is, which is
    correct — and the reason blamed the rebuild for what the source did.
    """
    import cv2
    import numpy as np
    from stockforge.stages import critique as critique_stage

    square = tmp_path / "photo.png"
    card = tmp_path / "rebuild.png"
    cv2.imwrite(str(square), np.full((900, 900, 3), 230, np.uint8))
    shot = np.full((1000, 710, 3), 250, np.uint8)
    cv2.putText(shot, "HALLOWEEN", (40, 400), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (30, 30, 30), 3)
    cv2.imwrite(str(card), shot)

    blamed = critique_stage.signals(square, card, compare_shape=True)
    assert "wrong shape" in (blamed.fault or ""), "this test is measuring nothing"

    fair = critique_stage.signals(square, card, compare_shape=False)
    assert not fair.fault, f"it still faulted the rebuild: {fair.fault}"


def test_a_cropped_source_is_still_checked_for_shape(tmp_path):
    """The check is worth having where it means something — a rebuild at the
    wrong trim is a real fault and this is what catches it."""
    import cv2
    import numpy as np
    from stockforge.stages import critique as critique_stage

    portrait = tmp_path / "source.png"
    landscape = tmp_path / "rebuild.png"
    cv2.imwrite(str(portrait), np.full((1000, 710, 3), 240, np.uint8))
    wrong = np.full((710, 1000, 3), 250, np.uint8)
    cv2.putText(wrong, "HALLOWEEN", (40, 300), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (30, 30, 30), 3)
    cv2.imwrite(str(landscape), wrong)

    assert "wrong shape" in (critique_stage.signals(portrait, landscape).fault or "")


# --- a description is not nothing -----------------------------------------
#
# Three designs of seven died here in one run, both models in the chain
# answering the same way:
#
#     could not produce valid TypeRead: no parseable JSON in response
#     It said: The image depicts a Halloween-themed invitation, featuring a
#     white background with a purple border and a central illustration of a
#     haunted house ... * A ghost * Bats * A jack-o'-lantern * A crescent moon
#
# Which is the design. It looked, it got it right, and it wrote prose. Asking
# again does not help — a small vision model doing two hard things at once
# drops the formatting every time — but the looking is the expensive half and
# it has already happened.

class _Colour(BaseModel):
    hex: str
    role: str


class _Describes(VisionProvider):
    """Shown a picture it describes what it sees, however firmly it is asked
    for JSON. Given words and no picture, it does as it is told."""

    name = "prose-model"

    def __init__(self, prose: str = "", answers: bool = True):
        self.prose = prose or (
            "The image depicts a Halloween-themed invitation, featuring a white "
            "background with a purple border and a central illustration of a "
            "haunted house.")
        self.answers = answers
        self.carried: list[int] = []

    def chat(self, system, user_text, images, **kw):
        self.carried.append(len(images))
        if images:
            return self.prose
        if not self.answers:
            return "I'm afraid I can't help with that."
        return '{"hex": "#ffffff", "role": "background"}'


def test_a_read_that_comes_back_as_prose_is_built_from_the_prose():
    model = _Describes()
    got = model.structured("sys", "read this", [Path("card.png")], _Colour)

    assert got.hex == "#ffffff"
    assert model.carried[-1] == 0, (
        "it sent the picture again — the looking was already done, and sending "
        "it back is asking for the same failure a fourth time")


def test_the_salvage_does_not_fire_when_the_model_answered_properly():
    """Otherwise every read in the program quietly costs two calls instead of
    one, which is the whole budget doubled to fix a failure that did not
    happen."""
    class Fine(_Describes):
        def chat(self, system, user_text, images, **kw):
            self.carried.append(len(images))
            return '{"hex": "#101010", "role": "ink"}'

    model = Fine()
    assert model.structured("sys", "read this", [Path("card.png")], _Colour).role == "ink"
    assert len(model.carried) == 1, f"it asked {len(model.carried)} times"


def test_the_design_is_told_the_read_came_out_of_a_description():
    """Quietly substituting a softer answer is worse than failing. The sentence
    goes onto the design, where Review shows it."""
    said: list[str] = []
    _Describes().structured("sys", "read this", [Path("card.png")], _Colour,
                            note=said.append)

    assert said and "description" in said[0], said


def test_prose_with_nothing_usable_in_it_still_fails():
    """The salvage is a rescue, not a way of always returning something. A
    model that cannot answer has to say so, and the error still has to carry
    what it actually said."""
    model = _Describes(prose="I can't see the image.", answers=False)
    with pytest.raises(ProviderError) as blew:
        model.structured("sys", "read this", [Path("card.png")], _Colour)

    assert "can't see the image" in str(blew.value), (
        "the error lost what the model actually said")


def test_a_design_whose_type_came_back_as_prose_is_not_lost(workspace):
    """End to end, on the pass that killed three of seven. TypeRead has no
    fallback and cannot have one — it is the design — so the salvage is the
    only thing standing between prose and a dead design."""
    cfg, tmp = workspace

    class ProseOnType(ScriptedProvider):
        def structured(self, system, user_text, images, model, note=None, **kw):
            if model.__name__ == "TypeRead" and images:
                # Prose from the picture, as NVIDIA's llama-3.2-11b gives it;
                # then the real salvage path, with no image attached.
                return VisionProvider.structured(
                    self, system, user_text, images, model, note=note, **kw)
            return super().structured(system, user_text, images, model, **kw)

        def chat(self, system, user_text, images, **kw):
            if images:
                return ("The image is a Halloween party invitation with a black "
                        "background and white text. Title: 'Halloween PARTY'.")
            return super().structured(
                system, user_text, [], TypeRead).model_dump_json()

    pipe, design_id = _pulled(workspace, ProseOnType())
    assert pipe.build(design_id) != "failed"
    warnings = pipe.store.get_spec(design_id)["warnings"]
    assert any("description" in w for w in warnings), warnings


def test_the_artwork_is_collected_when_the_niche_finishes_being_read(tmp_path):
    """"Dig out the assets, collect them to use them" — on the read that
    completes the niche, and not on each of the twenty-four, because by then
    every sighting of a motif is available and it can take the best one."""
    build_font_library(tmp_path / "fonts")
    empty = tmp_path / "no-motifs"
    empty.mkdir()
    cfg = Settings(root=tmp_path / "work", fonts_dir=tmp_path / "fonts",
                   motifs_dir=empty, preserve_original=False, seed_designs=3)
    cfg.ensure_dirs()
    for n in range(3):
        _listing(tmp_path / "in", f"card{n}")

    providers.set_provider("vision", ScriptedProvider())
    providers.set_provider("reason", ScriptedProvider())
    pipe = Pipeline(cfg)
    pipe.pull(open_source("folder", str(tmp_path / "in")))
    rows = pipe.store.designs(state="pending")

    harvested = empty / "_harvested"
    for row in rows[:2]:
        pipe.build(row["id"])
    assert not list(harvested.glob("*.png")) if harvested.exists() else True, (
        "it harvested part-way through, before the best sightings were in")

    pipe.build(rows[2]["id"])
    assert harvested.is_dir() and list(harvested.glob("*.png")), (
        "the niche finished being read and nothing was cut out of it")


def test_a_failure_collecting_the_artwork_does_not_lose_the_design(tmp_path, monkeypatch):
    """The design is already read, exported and filed by this point. Losing it
    to a bad crop would be throwing away the expensive half over the free one."""
    build_font_library(tmp_path / "fonts")
    build_motif_library(tmp_path / "motifs")
    cfg = Settings(root=tmp_path / "work", fonts_dir=tmp_path / "fonts",
                   motifs_dir=tmp_path / "motifs", preserve_original=False,
                   seed_designs=1)
    cfg.ensure_dirs()
    _listing(tmp_path / "in", "card")

    providers.set_provider("vision", ScriptedProvider())
    providers.set_provider("reason", ScriptedProvider())
    pipe = Pipeline(cfg)
    pipe.pull(open_source("folder", str(tmp_path / "in")))

    from stockforge.stages import motifs as motifs_stage
    monkeypatch.setattr(motifs_stage, "harvest_all",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk went away")))

    assert pipe.build(pipe.store.designs()[0]["id"]) == "master_only"


# --- twenty-four bad reads is not a catalogue -----------------------------

def _photo_read(pipe, design_id):
    """Mark this design's source as a listing photo the artwork was never
    found inside — the `unsure` trim, exactly as ingest records it."""
    with pipe.store.tx() as c:
        c.execute("UPDATE assets SET trim='unsure', trim_note='could not find "
                  "the card in the photo' WHERE design_id=?", (design_id,))


def test_a_design_read_through_a_photograph_lends_nothing(tmp_path):
    """It measured the table as well as the card. Its grid, its margins and a
    good part of its palette belong to somebody's kitchen worktop, and the
    program said so at the time and then used it anyway."""
    build_font_library(tmp_path / "fonts")
    build_motif_library(tmp_path / "motifs")
    cfg = Settings(root=tmp_path / "work", fonts_dir=tmp_path / "fonts",
                   motifs_dir=tmp_path / "motifs", preserve_original=False,
                   seed_designs=0)
    cfg.ensure_dirs()
    for n in range(2):
        _listing(tmp_path / "in", f"card{n}")

    providers.set_provider("vision", ScriptedProvider())
    providers.set_provider("reason", ScriptedProvider())
    pipe = Pipeline(cfg)
    pipe.pull(open_source("folder", str(tmp_path / "in")))
    rows = pipe.store.designs(state="pending")
    for row in rows:
        pipe.build(row["id"])

    assert len(pipe._spec_pool(exclude=rows[0]["id"])) == 1, "the pool is not what this test thinks"
    _photo_read(pipe, rows[1]["id"])
    assert pipe._spec_pool(exclude=rows[0]["id"]) == [], (
        "a design read through an uncropped photograph was still lending ingredients")


def test_a_design_read_through_a_photograph_does_not_count_towards_the_seed(tmp_path):
    """Reaching twenty-four on reads that measured a tabletop is reaching
    nothing. The gate and the screen both have to know it."""
    build_font_library(tmp_path / "fonts")
    build_motif_library(tmp_path / "motifs")
    cfg = Settings(root=tmp_path / "work", fonts_dir=tmp_path / "fonts",
                   motifs_dir=tmp_path / "motifs", preserve_original=False,
                   seed_designs=0)
    cfg.ensure_dirs()
    _listing(tmp_path / "in", "card")

    providers.set_provider("vision", ScriptedProvider())
    providers.set_provider("reason", ScriptedProvider())
    pipe = Pipeline(cfg)
    pipe.pull(open_source("folder", str(tmp_path / "in")))
    design_id = pipe.store.designs()[0]["id"]
    pipe.build(design_id)

    assert pipe._collection_counts("unfiled")["read"] == 1
    _photo_read(pipe, design_id)
    after = pipe._collection_counts("unfiled")
    assert after["read"] == 0, "it still counted towards the twenty-four"
    assert after["unsure"] == 1, "and nothing on screen would say why"


def test_the_screen_says_why_the_count_stopped_moving():
    """The wall-with-no-door failure again, in its third costume. A number that
    stops going up with nothing saying why is the thing that wastes an
    afternoon."""
    ok, fix = niches.ready(
        {"name": "Halloween cards", "read": 20, "designs": 24,
         "failed": 0, "unsure": 4}, 24)
    assert not ok
    assert "4 read through the listing photo" in fix, fix
    assert "crop" in fix, "it named the problem and not the fix"
