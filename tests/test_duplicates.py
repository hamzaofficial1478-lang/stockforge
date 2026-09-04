"""Not shipping the same thing twice.

The distinctness check in `derive` asks whether a rebuild still reads as a copy
of its own source. Nothing asked whether design four hundred looks like design
twelve, and that is the one an agency's similarity matcher answers for you, by
rejecting the batch.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from stockforge import providers
from stockforge.config import Settings
from stockforge.pipeline import Pipeline
from stockforge.sources import open_source
from stockforge.stages.duplicates import HASH_BITS, Twin, distance, fingerprint, nearest

from conftest import build_font_library, build_motif_library
from test_pipeline import ScriptedProvider, _listing


class _Row(dict):
    """The shape the store hands back."""

    def __getitem__(self, key):
        return dict.__getitem__(self, key)


def _row(design_id, page_name, phash, aspect=0.714):
    return _Row(design_id=design_id, page_name=page_name, phash=phash, aspect=aspect)


# --- the measures ---------------------------------------------------------

def test_distance_counts_the_bits_that_differ():
    assert distance("0000", "0000") == 0
    assert distance("0000", "0001") == 1
    assert distance("1111", "0000") == 4


def test_hashes_of_different_lengths_are_never_a_match():
    """Nothing should ever be judged close to a fingerprint taken a different
    way — an old row and a new one are not comparable."""
    assert distance("0000", "00000000") >= 4


def test_a_page_fingerprints_to_a_hash_and_its_shape(tmp_path):
    page = tmp_path / "p.png"
    img = np.full((700, 500, 3), 245, np.uint8)
    cv2.rectangle(img, (80, 200), (420, 300), (30, 30, 30), -1)
    cv2.imwrite(str(page), img)

    taken = fingerprint(page)
    assert taken is not None
    phash, aspect = taken
    assert len(phash) == HASH_BITS
    assert aspect == pytest.approx(500 / 700, abs=0.001)


def test_something_that_is_not_an_image_is_not_a_crash(tmp_path):
    bad = tmp_path / "p.png"
    bad.write_text("not a picture")
    assert fingerprint(bad) is None


# --- choosing a twin ------------------------------------------------------

def test_the_closest_one_within_the_limit_wins():
    mine = "0" * HASH_BITS
    others = [
        _row("far", "cover", "1" * 40 + "0" * (HASH_BITS - 40)),
        _row("near", "cover", "1" * 5 + "0" * (HASH_BITS - 5)),
        _row("middling", "cover", "1" * 12 + "0" * (HASH_BITS - 12)),
    ]
    twin = nearest(mine, 0.714, others, max_distance=20)
    assert twin is not None
    assert twin.design_id == "near"
    assert twin.distance == 5


def test_nothing_close_enough_is_no_twin():
    mine = "0" * HASH_BITS
    others = [_row("far", "cover", "1" * 40 + "0" * (HASH_BITS - 40))]
    assert nearest(mine, 0.714, others, max_distance=20) is None


def test_two_different_shapes_are_two_different_products():
    """A 5x7 invitation and a square social post cut from the same artwork are
    not the same submission, however alike the pixels are."""
    mine = "0" * HASH_BITS
    others = [_row("square", "cover", "0" * HASH_BITS, aspect=1.0)]
    assert nearest(mine, 0.714, others, max_distance=20) is None
    assert nearest(mine, 1.0, others, max_distance=20) is not None


# --- through the pipeline -------------------------------------------------

class _Unvarying(ScriptedProvider):
    """Reads every design exactly the same way, so every rebuild comes out the
    same. What a converging derivation would look like."""

    def _survey(self):
        survey = super()._survey()
        self.nth = 0
        return survey


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("FONTCONFIG_FILE", raising=False)
    build_font_library(tmp_path / "fonts")
    build_motif_library(tmp_path / "motifs")
    return Settings(root=tmp_path / "work", fonts_dir=tmp_path / "fonts",
                    motifs_dir=tmp_path / "motifs")


def _run(workspace, tmp_path, provider, count=2):
    providers.set_provider("vision", provider)
    providers.set_provider("reason", provider)
    for n in range(count):
        _listing(tmp_path / "exports", f"listing-{n}")
    pipe = Pipeline(workspace)
    pipe.pull(open_source("folder", str(tmp_path / "exports")))
    states = {}
    for row in pipe.store.designs(state="pending"):
        states[row["id"]] = pipe.build(row["id"])
    return pipe, states


def test_a_second_design_that_came_out_the_same_is_stopped(workspace, tmp_path):
    pipe, states = _run(workspace, tmp_path, _Unvarying())

    assert sorted(states.values()) == ["ready", "review"]
    held = [d for d, s in states.items() if s == "review"][0]
    kept = [d for d, s in states.items() if s == "ready"][0]

    reason = pipe.store.conn.execute(
        "SELECT reason FROM review WHERE design_id=?", (held,)).fetchone()["reason"]
    assert "too close to" in reason
    assert kept[:12] in reason, "it should say which design it collided with"


def test_designs_that_are_genuinely_different_both_go_through(workspace, tmp_path):
    _, states = _run(workspace, tmp_path, ScriptedProvider(), count=3)
    assert set(states.values()) == {"ready"}


def test_every_finished_page_is_remembered(workspace, tmp_path):
    pipe, _ = _run(workspace, tmp_path, ScriptedProvider(), count=2)
    rows = pipe.store.fingerprints()
    assert len(rows) == 2
    assert all(len(r["phash"]) == HASH_BITS for r in rows)
    assert all(r["aspect"] > 0 for r in rows)


def test_a_design_is_never_its_own_twin(workspace, tmp_path):
    """Re-running a design compares it against everything except itself, or it
    would collide with the copy it just replaced."""
    pipe, states = _run(workspace, tmp_path, ScriptedProvider(), count=1)
    design_id = next(iter(states))
    assert pipe.build(design_id) == "ready"
