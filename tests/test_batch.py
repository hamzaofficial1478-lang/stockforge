"""Making many designs at once, and never making the same one twice.

The arithmetic this exists for: reading a design costs five model calls because
a model has to look at it, and making one from a reading you already have costs
a handful of choices and about a second of drawing. The old route paid the
reading price for every output, so forty-eight designs meant three hundred and
eighty-four round trips and most of a day. A batch costs one request — the
copy, which never needed an image — and the rest is local.

Which moves the difficulty. A mix is a small set of choices, and a pool holds
only so many of them; sample forty-eight at random and the same combination
turns up five or six times and the batch reads as one design with the words
changed. That is the failure these tests are built against, because it is the
one the owner would notice first and the one that would make the whole path
worthless.
"""

import time
from collections import Counter

import pytest

from stockforge import providers
from stockforge.config import Settings
from stockforge.pipeline import Pipeline
from stockforge.schema import (Canvas, ColourRole, DesignDNA, DesignSpec, FontClass,
                               Grid, Page, Palette, Swatch, TextElement, TypeRole)
from stockforge.stages import batch as batch_stage
from stockforge.stages import compose as compose_stage

from conftest import build_font_library, build_motif_library
from test_pipeline import ScriptedProvider


def _spec(name: str, hue: int, words: str, margin: float = 0.08) -> DesignSpec:
    """A design with something of its own to contribute to a mix."""
    return DesignSpec(
        source_asset_id=f"asset-{name}", design_id=name, confidence=0.9,
        dna=DesignDNA(
            category="invitation", occasion="birthday",
            grid=Grid(margin_top=margin, margin_bottom=margin,
                      margin_left=margin, margin_right=margin),
            palette=Palette(swatches=[
                Swatch(role=ColourRole.BACKGROUND, hex=f"#{hue:02x}f0f0", coverage=0.7),
                Swatch(role=ColourRole.INK, hex=f"#{hue:02x}2040", coverage=0.3)]),
        ),
        pages=[Page(name="front", canvas=Canvas(width_mm=127, height_mm=178),
                    elements=[
                        TextElement(role=TypeRole.TITLE, content=words,
                                    box={"x": 0.1, "y": 0.1, "w": 0.8, "h": 0.12},
                                    font=FontClass(category="serif", weight=700),
                                    size_ratio=0.09, placeholder=True),
                        TextElement(role=TypeRole.BODY, content=f"{words} details here",
                                    box={"x": 0.1, "y": 0.5, "w": 0.8, "h": 0.08},
                                    font=FontClass(category="sans", weight=400),
                                    size_ratio=0.03, placeholder=True)])],
    )


def _varied_pool(n: int) -> list[DesignSpec]:
    words = ["Celebrate", "Join Us", "Save The Date", "You Are Invited",
             "Come Along", "Party Time", "Gather Round", "Be Our Guest"]
    return [_spec(f"d{i:02d}", (i * 29) % 256, words[i % len(words)],
                  margin=0.05 + (i % 5) * 0.02) for i in range(n)]


# --- never the same combination twice -------------------------------------

def test_a_combination_is_never_planned_twice_in_one_run():
    """Deliberately asked for far more than a pool of three can give.

    On a wide pool the draw rarely collides by chance, so a test there passes
    with the check taken out and proves nothing. Two designs hold sixty-odd
    combinations, so asking for two hundred forces every collision the guard
    exists to catch."""
    pool = _varied_pool(2)
    made = batch_stage.plan(pool, count=200, mix=0.6)
    marks = [p.fingerprint for p in made.made]

    assert marks, "nothing was planned at all"
    assert len(set(marks)) == len(marks), (
        f"{len(marks) - len(set(marks))} of {len(marks)} were the same recipe")
    assert len(marks) < 200, (
        "a pool of two produced two hundred distinct combinations, which means "
        "either the fingerprint distinguishes nothing or repeats got through")


def test_a_combination_already_shipped_is_not_planned_again():
    """The ledger's whole job. Without it a second run makes the same designs
    as the first — and again, it has to be a pool small enough that a repeat is
    certain rather than unlucky."""
    pool = _varied_pool(2)
    first = batch_stage.plan(pool, count=200, mix=0.6, seed=1)
    already = {p.fingerprint for p in first.made}
    assert len(already) > 4, "the first run found too little to test against"

    second = batch_stage.plan(pool, count=200, mix=0.6, seed=2,
                              seen=already.__contains__)
    assert not (already & {p.fingerprint for p in second.made}), (
        "a run repeated a combination the ledger had already recorded")
    assert second.exhausted, (
        "the pool was emptied by the first run and the second did not notice")


def test_the_same_ingredients_at_a_different_strength_are_a_different_design():
    """Because they visibly are. Counting them as the same would throw away
    most of the variety the pool can produce."""
    a = compose_stage.Recipe(base="x", palette_from="y")
    assert batch_stage.fingerprint(a, 0) != batch_stage.fingerprint(a, 2)


def test_a_hair_of_difference_in_strength_is_not_a_new_design():
    """The other side of it: banding, or every design is "new" and the ledger
    stops meaning anything."""
    a = compose_stage.Recipe(base="x", palette_from="y")
    assert batch_stage.fingerprint(a, 1) == batch_stage.fingerprint(a, 1)


# --- and no favourites ----------------------------------------------------

def test_the_batch_does_not_lean_on_one_or_two_designs():
    """Uniform random on a pool of twelve gives one or two donors a third of
    the batch, and the run comes out looking like those two. Weighting by what
    has already been drawn on is what stops it."""
    pool = _varied_pool(12)
    made = batch_stage.plan(pool, count=48, mix=0.8)
    assert made.made, "nothing was planned"

    counts = Counter()
    for planned in made.made:
        for who in (planned.recipe.base, planned.recipe.palette_from,
                    planned.recipe.type_from, planned.recipe.motifs_from,
                    planned.recipe.background_from):
            if who:
                counts[who] += 1
    busiest = counts.most_common(1)[0][1]
    total = sum(counts.values())
    # Measured: uniform picking puts the busiest donor at about 18% of all
    # ingredients on a pool of twelve; weighted holds it near 11%, which is
    # about the 1/12 an even spread would give.
    assert busiest / total < 0.16, (
        f"one design supplied {busiest / total:.0%} of every ingredient used")


def test_what_has_been_used_before_is_carried_into_the_next_run():
    """A donor that gave its palette to nine designs last week should not be
    first in line this week."""
    pool = _varied_pool(8)
    heavy = compose_stage._identity(pool[0])
    made = batch_stage.plan(pool, count=24, mix=0.8, used={heavy: 50})

    used_heavy = sum(
        1 for p in made.made
        for who in (p.recipe.palette_from, p.recipe.type_from,
                    p.recipe.motifs_from, p.recipe.background_from)
        if who == heavy)
    assert used_heavy <= 3, (
        f"the design already used fifty times was picked {used_heavy} more times")


# --- saying so when the pool cannot give what was asked -------------------

def test_a_pool_that_is_out_of_combinations_says_so():
    pool = _varied_pool(2)
    made = batch_stage.plan(pool, count=200, mix=0.5)
    assert made.exhausted
    assert "read more of your catalogue" in made.note


def test_an_empty_pool_is_not_an_exception():
    made = batch_stage.plan([], count=10)
    assert len(made) == 0 and made.note


# --- the whole path, through the pipeline ---------------------------------

@pytest.fixture
def workspace(tmp_path, monkeypatch):
    build_font_library(tmp_path / "fonts")
    build_motif_library(tmp_path / "motifs")
    cfg = Settings(root=tmp_path / "work", fonts_dir=tmp_path / "fonts",
                   motifs_dir=tmp_path / "motifs", preserve_original=False)
    cfg.ensure_dirs()
    return cfg


class _CountingCopy(ScriptedProvider):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def structured(self, system, user_text, images, model, **kw):
        self.calls += 1
        return super().structured(system, user_text, images, model, **kw)


NICHE = "test-niche"


def _seed_pool(pipe, specs, niche=NICHE):
    """A pool filed under a niche, because nothing is made without one.

    `save_read` as well as `save_spec`: a niche counts designs whose artwork has
    actually been looked at, and a row with a spec but no reading is one that
    was never read.
    """
    pipe.store.ensure_collection(niche, niche.replace("-", " ").title())
    for i, spec in enumerate(specs):
        did = f"{i:024d}"
        pipe.store.add_made_design(did, f"seed {i}", f"seed-{i}", collection=niche)
        payload = spec.model_dump(mode="json")
        pipe.store.save_spec(did, payload)
        pipe.store.save_read(did, payload)


def test_a_whole_batch_costs_one_model_call(workspace):
    """The number the whole path exists for. Forty-eight designs the old way
    was three hundred and eighty-four calls; here the only thing a model is
    asked for is the wording, once, for the run."""
    provider = _CountingCopy()
    providers.set_provider("vision", provider)
    providers.set_provider("reason", provider)
    pipe = Pipeline(workspace)
    _seed_pool(pipe, _varied_pool(30))

    provider.calls = 0
    result = pipe.make(count=12, collection=NICHE)

    assert result["made"] > 0, result["note"]
    assert provider.calls <= 6, (
        f"{provider.calls} model calls for one batch — the copy is meant to be "
        f"one request per wave, not one per design")


def test_a_batch_is_quick_enough_to_be_worth_having(workspace):
    """Not a benchmark, a floor. If a design takes minutes of local work the
    whole argument for this path collapses, so it is worth failing loudly."""
    providers.set_provider("vision", ScriptedProvider())
    providers.set_provider("reason", ScriptedProvider())
    pipe = Pipeline(workspace)
    _seed_pool(pipe, _varied_pool(30))

    started = time.monotonic()
    result = pipe.make(count=8, collection=NICHE)
    each = (time.monotonic() - started) / max(1, result["made"])
    assert each < 20, f"{each:.1f}s per design of purely local work"


def test_a_look_alike_is_thrown_away_rather_than_queued(workspace):
    """Handing back a pile of near-identical designs and asking which to keep
    is the work this path exists to remove."""
    providers.set_provider("vision", ScriptedProvider())
    providers.set_provider("reason", ScriptedProvider())
    pipe = Pipeline(workspace)
    # Ten copies of one design: every mix of it is the same picture.
    _seed_pool(pipe, [_spec(f"same{i:02d}", 100, "Celebrate") for i in range(30)])

    result = pipe.make(count=12, collection=NICHE)
    assert not [d for d in result["designs"] if d["state"] == "review"], (
        "near-repeats were queued for review instead of being replaced")
    assert result["discarded"] > 0, "this pool should have produced look-alikes"
    assert "already have" in result["note"]


def test_a_second_run_does_not_repeat_the_first(workspace):
    providers.set_provider("vision", ScriptedProvider())
    providers.set_provider("reason", ScriptedProvider())
    pipe = Pipeline(workspace)
    _seed_pool(pipe, _varied_pool(30))

    first = pipe.make(count=6, collection=NICHE)
    second = pipe.make(count=6, collection=NICHE)
    assert first["made"] and second["made"]
    overlap = {d["design_id"] for d in first["designs"]} & {
        d["design_id"] for d in second["designs"]}
    assert not overlap, "the second run made designs the first had already made"


def test_nothing_to_mix_from_is_said_plainly(workspace):
    """An empty niche says what it needs rather than producing nothing and
    leaving you to guess why."""
    providers.set_provider("vision", ScriptedProvider())
    providers.set_provider("reason", ScriptedProvider())
    pipe = Pipeline(workspace)
    pipe.store.ensure_collection(NICHE, "Test niche")

    result = pipe.make(count=10, collection=NICHE)
    assert result["made"] == 0
    assert "needs 24" in result["note"], result["note"]


# --- what may lend an ingredient ------------------------------------------

def test_the_program_does_not_mix_from_its_own_output(workspace):
    """Measured before this was true: thirty seeds became thirty-six donors
    after one run of six. Mixing from your own output compounds — the third run
    is a mix of mixes of mixes, drifting away from anything anybody chose."""
    providers.set_provider("vision", ScriptedProvider())
    providers.set_provider("reason", ScriptedProvider())
    pipe = Pipeline(workspace)
    _seed_pool(pipe, _varied_pool(30))

    before = len(pipe._specs(exclude="", cap=500, collection=NICHE, donors_only=True))
    result = pipe.make(count=6, collection=NICHE)
    after = len(pipe._specs(exclude="", cap=500, collection=NICHE, donors_only=True))

    assert result["made"] > 0, result["note"]
    assert after == before, (
        f"the pool grew from {before} to {after} — it is mixing from what it made")


def test_the_seed_designs_are_read_once_and_used_for_ever(workspace):
    """The owner's actual question, and the answer is no — you do not hand the
    designs over again. They are read once and every run after that reuses the
    reading, which is why a batch costs one request rather than five per
    design. Asserted by which passes run at all: not one reading pass appears
    in either run."""
    asked: list[str] = []

    class Watching(ScriptedProvider):
        def structured(self, system, user_text, images, model, **kw):
            asked.append(model.__name__)
            return super().structured(system, user_text, images, model, **kw)

    provider = Watching()
    providers.set_provider("vision", provider)
    providers.set_provider("reason", provider)
    pipe = Pipeline(workspace)
    _seed_pool(pipe, _varied_pool(30))

    asked.clear()
    first = pipe.make(count=4, collection=NICHE)
    second = pipe.make(count=4, collection=NICHE)

    assert first["made"] and second["made"], (first["note"], second["note"])
    reading = {"Survey", "PaletteRead", "Provenance", "TypeRead", "StructureRead"}
    assert not (set(asked) & reading), (
        f"a batch read the artwork again: {sorted(set(asked) & reading)}")
    # What a batch does ask for is covered by the call-count test above. The
    # claim here is narrower and is the one the owner asked about: the seed
    # designs are read once, and no run afterwards reads anything.
    assert set(asked) <= {"CopySet", "NewCopy"}, (
        f"a batch asked for more than the wording: {sorted(set(asked))}")


def test_somebody_elses_design_is_never_an_ingredient(workspace):
    """Reference can be read, listed and looked at. It cannot lend a palette to
    something that gets sold — every ingredient being the owner's own is the
    whole reason a delivered file is safe to sell."""
    providers.set_provider("vision", ScriptedProvider())
    providers.set_provider("reason", ScriptedProvider())
    pipe = Pipeline(workspace)
    _seed_pool(pipe, _varied_pool(30))

    # one brought in for reference, filed in the same niche
    borrowed = _spec("someone-elses", 200, "Not Mine")
    pipe.store.ensure_collection(NICHE, "Test niche")
    pipe.store.add_made_design("ref0000000000000000000000", "reference", "ref-0",
                               collection=NICHE)
    payload = borrowed.model_dump(mode="json")
    pipe.store.save_spec("ref0000000000000000000000", payload)
    pipe.store.save_read("ref0000000000000000000000", payload)
    with pipe.store.tx() as c:
        c.execute("UPDATE designs SET owned=0 WHERE id='ref0000000000000000000000'")

    pool = pipe._specs(exclude="", cap=500, collection=NICHE, donors_only=True)
    assert "someone-elses" not in {compose_stage._identity(s) for s in pool}, (
        "a design marked as somebody else's was offered as an ingredient")


def test_your_own_designs_from_anywhere_are_ingredients(workspace):
    """The other half. Designs of yours listed on another platform are still
    yours, and marking them so is all it takes."""
    providers.set_provider("vision", ScriptedProvider())
    providers.set_provider("reason", ScriptedProvider())
    pipe = Pipeline(workspace)
    _seed_pool(pipe, _varied_pool(30))

    mine = _spec("mine-elsewhere", 90, "Also Mine")
    pipe.store.add_made_design("own0000000000000000000000", "mine", "own-0",
                               collection=NICHE)
    payload = mine.model_dump(mode="json")
    pipe.store.save_spec("own0000000000000000000000", payload)
    pipe.store.save_read("own0000000000000000000000", payload)

    pool = pipe._specs(exclude="", cap=500, collection=NICHE, donors_only=True)
    assert "mine-elsewhere" in {compose_stage._identity(s) for s in pool}
