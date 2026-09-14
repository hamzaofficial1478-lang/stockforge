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

import pytest

from stockforge import collections as niches
from stockforge import providers
from stockforge.config import Settings
from stockforge.pipeline import Pipeline
from stockforge.providers.base import ProviderError
from stockforge.sources import open_source
from stockforge.stages.analyse import palette_from_pixels

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


# --- not paying to find out what was knowable in advance ------------------

def test_a_variation_with_nothing_to_borrow_from_is_not_attempted(tmp_path):
    """From a real run, a pool of two:

        round 1: worst distinct=0.10 -> derive_further
        round 2: worst distinct=0.10 -> derive_further
        ac131054 -> review

    The second round borrowed every ingredient it could and the score did not
    move, because every ingredient came from the same single donor. Three model
    calls and four minutes to learn something countable in advance.
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
    assert any("variation needs at least" in w for w in warnings), warnings


def test_a_real_catalogue_still_gets_its_variation(tmp_path):
    """The other side: the guard must not quietly turn everybody's variations
    into masters the moment it exists."""
    build_font_library(tmp_path / "fonts")
    build_motif_library(tmp_path / "motifs")
    cfg = Settings(root=tmp_path / "work", fonts_dir=tmp_path / "fonts",
                   motifs_dir=tmp_path / "motifs", preserve_original=False)
    cfg.ensure_dirs()
    for n in range(6):
        _listing(tmp_path / "in", f"card{n}")

    providers.set_provider("vision", ScriptedProvider())
    providers.set_provider("reason", ScriptedProvider())
    pipe = Pipeline(cfg)
    pipe.pull(open_source("folder", str(tmp_path / "in")))
    rows = pipe.store.designs(state="pending")
    for row in rows[:-1]:
        pipe.build(row["id"])

    assert pipe.build(rows[-1]["id"]) != "master_only", (
        "a design with five others to borrow from was refused a variation")


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
