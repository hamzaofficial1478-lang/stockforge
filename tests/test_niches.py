"""Keeping one niche out of another.

The owner spends a month on Halloween cards and then moves to business cards.
Both are in the same program and the same database, and a palette borrowed
across that line is not a new design — it is a mistake everybody notices and
nobody would ship.

There was a wall before this and it was not one. Donors were matched on the
`occasion` string the model wrote, and when nothing matched it **widened to the
whole pool**. So a niche with only a few designs in it borrowed from every
other niche, which is the failure at its very worst: it happens exactly when
the new niche is small, and that is exactly when nobody is checking.
"""

import pytest

from stockforge import collections as niches
from stockforge import providers
from stockforge.config import Settings
from stockforge.pipeline import Pipeline, out_dir_for
from stockforge.stages import compose as compose_stage

from conftest import build_font_library, build_motif_library
from test_batch import _spec, _varied_pool
from test_pipeline import ScriptedProvider


# --- names people actually type -------------------------------------------

@pytest.mark.parametrize("typed,want", [
    ("Halloween cards", "halloween-cards"),
    ("halloween cards", "halloween-cards"),
    ("  Halloween   Cards  ", "halloween-cards"),
    ("Halloween-Cards", "halloween-cards"),
    ("Wedding & Engagement", "wedding-engagement"),
])
def test_one_niche_however_it_is_typed(typed, want):
    """Three spellings of one niche becoming three niches is the mess this is
    here to avoid, and it is the easiest one to cause."""
    assert niches.slug(typed) == want


def test_an_empty_name_does_not_become_a_niche():
    assert niches.slug("") == "unnamed"
    assert niches.slug("   ") == "unnamed"


EXISTING = [
    {"id": "halloween-invitations", "name": "Halloween invitations", "read": 40, "designs": 40},
    {"id": "wedding-suites", "name": "Wedding suites", "read": 30, "designs": 30},
]


def test_the_same_name_is_found_outright():
    found = niches.look_up("Halloween invitations", EXISTING)
    assert found.exact and found.slug == "halloween-invitations"


def test_a_near_name_is_offered_and_never_taken():
    """Choosing a near match on the owner's behalf is precisely how a month of
    business cards ends up filed under Halloween. Suggest; do not decide."""
    found = niches.look_up("halloween cards", EXISTING)
    assert not found.exact and not found.slug, "it picked one on its own"
    assert [c["id"] for c in found.near] == ["halloween-invitations"]


def test_an_unrelated_name_matches_nothing():
    """The important half. A helpful fuzzy match that offers Halloween when you
    typed business cards is worse than no matching at all."""
    found = niches.look_up("business cards", EXISTING)
    assert not found.exact and not found.near, (
        f"business cards was matched to {[c['id'] for c in found.near]}")


@pytest.mark.parametrize("typed", ["christmas", "baby shower", "logos", "resume templates"])
def test_nothing_unrelated_is_ever_offered(typed):
    assert not niches.look_up(typed, EXISTING).near, f"{typed!r} was matched to something"


def test_the_filler_words_do_not_make_a_new_niche():
    """"Halloween cards" and "Halloween card designs" are one niche. The words
    that differ say nothing about what the niche is."""
    found = niches.look_up("Halloween invitation designs", EXISTING)
    assert found.near and found.near[0]["id"] == "halloween-invitations"


# --- enough to work from ---------------------------------------------------

def test_a_thin_niche_is_not_ready_and_says_what_to_do():
    ready, why = niches.ready({"name": "Business cards", "read": 6, "designs": 6}, 24)
    assert not ready
    assert "6" in why and "24" in why
    assert "Pull in 18 more" in why


def test_pulled_in_but_not_read_is_said_separately():
    """"You have 30" when none of them have been looked at is the sort of
    encouragement that wastes an afternoon."""
    ready, why = niches.ready({"name": "Business cards", "read": 4, "designs": 30}, 24)
    assert not ready
    assert "26 more are pulled in but not read yet" in why


def test_a_full_niche_is_ready():
    ready, why = niches.ready({"name": "Halloween", "read": 24, "designs": 24}, 24)
    assert ready and not why


def test_no_niche_at_all_is_refused():
    ready, why = niches.ready(None)
    assert not ready and "Pick one" in why


# --- the wall, through the pipeline ---------------------------------------

@pytest.fixture
def workspace(tmp_path):
    build_font_library(tmp_path / "fonts")
    build_motif_library(tmp_path / "motifs")
    cfg = Settings(root=tmp_path / "work", fonts_dir=tmp_path / "fonts",
                   motifs_dir=tmp_path / "motifs", preserve_original=False)
    cfg.ensure_dirs()
    providers.set_provider("vision", ScriptedProvider())
    providers.set_provider("reason", ScriptedProvider())
    return cfg


def _fill(pipe, slug, name, specs):
    pipe.store.ensure_collection(slug, name)
    for i, spec in enumerate(specs):
        did = f"{slug[:6]}{i:018d}"
        pipe.store.add_made_design(did, f"{name} {i}", f"{slug}-{i}", collection=slug)
        pipe.store.save_spec(did, spec.model_dump(mode="json"))
        pipe.store.save_read(did, spec.model_dump(mode="json"))


def test_a_batch_never_borrows_from_another_niche(workspace):
    """The whole point. Thirty Halloween designs sitting next to thirty
    business cards, and not one ingredient crosses over."""
    pipe = Pipeline(workspace)
    halloween = [_spec(f"hw{i:02d}", (i * 29) % 256, "Spooky Night") for i in range(30)]
    business = [_spec(f"bz{i:02d}", (i * 17) % 256, "Acme Consulting") for i in range(30)]
    _fill(pipe, "halloween-cards", "Halloween cards", halloween)
    _fill(pipe, "business-cards", "Business cards", business)

    result = pipe.make(count=12, collection="business-cards")
    assert result["made"] > 0, result["note"]

    theirs = {compose_stage._identity(s) for s in halloween}
    for row in pipe.store.conn.execute(
            "SELECT ingredients, base FROM recipes WHERE collection='business-cards'"):
        import json
        used = {row["base"], *[v for v in json.loads(row["ingredients"]).values()
                               if isinstance(v, str)]}
        assert not (used & theirs), (
            f"a business card borrowed from Halloween: {sorted(used & theirs)}")


def test_a_thin_niche_borrows_from_nowhere_rather_than_everywhere(workspace):
    """The old failure exactly. Too few designs in the new niche used to widen
    the search to the whole catalogue — so the first business cards would have
    come out of Halloween, which is when it matters most and is watched least."""
    pipe = Pipeline(workspace)
    _fill(pipe, "halloween-cards", "Halloween cards",
          [_spec(f"hw{i:02d}", (i * 29) % 256, "Spooky Night") for i in range(40)])
    _fill(pipe, "business-cards", "Business cards",
          [_spec(f"bz{i:02d}", (i * 17) % 256, "Acme") for i in range(3)])

    result = pipe.make(count=10, collection="business-cards")
    assert result["made"] == 0, "it made designs from a niche of three"
    assert "24" in result["note"], result["note"]


def test_nothing_is_made_without_a_niche(workspace):
    pipe = Pipeline(workspace)
    _fill(pipe, "halloween-cards", "Halloween cards", _varied_pool(30))
    workspace.collection = ""

    result = pipe.make(count=6)
    assert result["made"] == 0
    assert "Choose which niche" in result["note"]


def test_a_niche_that_does_not_exist_is_refused(workspace):
    pipe = Pipeline(workspace)
    _fill(pipe, "halloween-cards", "Halloween cards", _varied_pool(30))
    result = pipe.make(count=6, collection="nothing-like-this")
    assert result["made"] == 0 and "no niche called" in result["note"]


def test_the_files_land_in_the_niches_own_folder(workspace):
    """Kept apart on disk as well as in the database, because "are they mixed"
    is a question the owner answers by opening a folder."""
    pipe = Pipeline(workspace)
    _fill(pipe, "halloween-cards", "Halloween cards", _varied_pool(30))

    result = pipe.make(count=3, collection="halloween-cards")
    assert result["made"] > 0, result["note"]
    for made in result["designs"]:
        where = out_dir_for(workspace.root, made["design_id"])
        assert "halloween-cards" in where.parts, f"{where} is not under its niche"


def test_what_was_made_in_one_niche_does_not_block_another(workspace):
    """Two niches can arrive at the same shape of recipe. The ledger is per
    niche, so starting a new one does not begin half-exhausted."""
    pipe = Pipeline(workspace)
    _fill(pipe, "halloween-cards", "Halloween cards", _varied_pool(30))
    _fill(pipe, "business-cards", "Business cards", _varied_pool(30))

    first = pipe.make(count=8, collection="halloween-cards")
    second = pipe.make(count=8, collection="business-cards")
    assert first["made"] > 0 and second["made"] > 0, (first["note"], second["note"])


def test_forgetting_one_niche_leaves_the_others_alone(workspace):
    pipe = Pipeline(workspace)
    _fill(pipe, "halloween-cards", "Halloween cards", _varied_pool(30))
    _fill(pipe, "business-cards", "Business cards", _varied_pool(30))
    pipe.make(count=5, collection="halloween-cards")
    pipe.make(count=5, collection="business-cards")

    pipe.store.forget_recipes("halloween-cards")
    left = pipe.store.conn.execute(
        "SELECT collection, COUNT(*) n FROM recipes GROUP BY collection").fetchall()
    counts = {r["collection"]: r["n"] for r in left}
    assert counts.get("halloween-cards", 0) == 0
    assert counts.get("business-cards", 0) > 0, "clearing one niche cleared the other"


# --- what was there before niches -----------------------------------------

def test_designs_from_before_niches_existed_are_filed_not_lost(tmp_path):
    """A workspace that predates all of this must not appear to have lost its
    catalogue, and must not silently join whatever niche is started next."""
    from stockforge.db import Store

    path = tmp_path / "old.db"
    store = Store(path)
    store.conn.execute(
        "INSERT INTO designs(id, design_key, title, source, image_count, state, created_at)"
        " VALUES ('old1','k','An old one','folder',1,'ready',1)")
    store.conn.commit()
    with store.tx() as c:                      # as if the column never existed
        c.execute("UPDATE designs SET collection=NULL")
    store.conn.close()

    reopened = Store(path)
    row = reopened.conn.execute("SELECT collection FROM designs WHERE id='old1'").fetchone()
    assert row["collection"] == "unfiled"
    assert any(c["id"] == "unfiled" for c in reopened.collections())
