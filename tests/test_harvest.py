"""Cutting a missing motif out of the design it already appears in.

The review queue said seven drawings were needed and every one of them was
already drawn — in the owner's own cards, which is where the descriptions came
from. The analyser had recorded the box each one sits in, and since flattening
was fixed those boxes land on the card rather than on a photograph of it.

So the first answer to "the library has no drawing for this" is not to draw one
and not to generate one. It is to cut out the one that is already there and
sold. Free, exact, and the owner's own artwork rather than something invented
that merely resembles it.
"""

import cv2
import numpy as np
import pytest

from stockforge.schema import Box
from stockforge.stages import motifs as motifs_stage
from stockforge.stages.motifs import Gap, Sighting, harvest, harvest_all


PAPER = (238, 244, 250)          # a cream card, as BGR


def _card_with_a_pumpkin(path):
    """A flattened card: cream paper, some type, and one orange motif."""
    card = np.full((1050, 750, 3), PAPER, np.uint8)
    cv2.putText(card, "HALLOWEEN", (120, 180), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (40, 40, 40), 3)
    cv2.ellipse(card, (375, 560), (150, 125), 0, 0, 360, (40, 120, 235), -1)
    cv2.rectangle(card, (360, 415), (392, 450), (50, 90, 60), -1)
    cv2.ellipse(card, (375, 620), (60, 26), 0, 0, 180, (25, 25, 25), -1)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), card)
    return path


def _gap(source, box=None, **kw):
    return Gap(
        description=kw.pop("description", "grinning carved pumpkin with a stem"),
        kind=kw.pop("kind", "seasonal"),
        designs=kw.pop("designs", 3),
        sightings=[Sighting(design_id="abc12345", page=0,
                            box=box or Box(x=0.28, y=0.37, w=0.44, h=0.24),
                            source_image=str(source), description="pumpkin")],
        **kw)


# --- the cut ---------------------------------------------------------------

def test_a_motif_is_cut_out_of_the_card_it_appears_on(tmp_path):
    card = _card_with_a_pumpkin(tmp_path / "flats" / "card.png")
    got = harvest(_gap(card), tmp_path / "motifs")

    assert got is not None, "nothing was cut"
    assert got.path.is_file()
    out = cv2.imread(str(got.path), cv2.IMREAD_UNCHANGED)
    assert out.shape[2] == 4, "no transparency — the paper is still there"


def test_the_paper_becomes_transparent_and_the_drawing_does_not(tmp_path):
    """The whole value of it. A crop with the background still on is just a
    rectangle of card, no use to trace and no use to place."""
    card = _card_with_a_pumpkin(tmp_path / "flats" / "card.png")
    got = harvest(_gap(card), tmp_path / "motifs")
    out = cv2.imread(str(got.path), cv2.IMREAD_UNCHANGED)

    h, w = out.shape[:2]
    assert out[4, 4, 3] < 64, "the corner paper was kept"
    assert out[h // 2, w // 2, 3] > 192, "the middle of the drawing was cut away"
    assert 0.15 < got.coverage < 0.85, (
        f"{got.coverage:.0%} of the crop is subject — that is a whole rectangle "
        f"or nothing at all, not a motif")


def test_the_cut_keeps_the_motifs_own_colours(tmp_path):
    """Not a silhouette. Somebody has to look at these and decide whether they
    are worth tracing, and a black blob tells them nothing."""
    card = _card_with_a_pumpkin(tmp_path / "flats" / "card.png")
    got = harvest(_gap(card), tmp_path / "motifs")
    out = cv2.imread(str(got.path), cv2.IMREAD_UNCHANGED)

    solid = out[:, :, 3] > 200
    mean = out[:, :, :3][solid].mean(axis=0)          # BGR
    assert mean[2] > mean[0] + 30, f"the orange is gone: {mean}"


def test_a_generous_box_does_not_drag_in_the_type(tmp_path):
    """The analyser's boxes are loose and often catch a corner of something
    else. Only what is connected to the middle is kept."""
    card = _card_with_a_pumpkin(tmp_path / "flats" / "card.png")
    # a box stretched up far enough to include the HALLOWEEN line
    wide = Box(x=0.10, y=0.12, w=0.80, h=0.50)
    got = harvest(_gap(card, box=wide), tmp_path / "motifs")
    out = cv2.imread(str(got.path), cv2.IMREAD_UNCHANGED)

    # Measured: keeping only what touches the middle leaves this band at 0.0,
    # and dropping that step leaves 12.7 — the heading's thin strokes surviving.
    # The line sits well below the second and well above the first, so it tells
    # the two apart rather than passing either way.
    top_band = out[: out.shape[0] // 5, :, 3]
    assert top_band.mean() < 3, (
        f"the heading came along with the motif: {top_band.mean():.1f}")


# --- when it cannot -------------------------------------------------------

def test_a_gap_nobody_ever_saw_is_not_invented(tmp_path):
    """No sighting means no drawing. It must not fall back to making something
    up — a hole you can see beats a picture nobody asked for."""
    assert harvest(Gap(description="a thing", kind="icon", designs=1),
                   tmp_path / "motifs") is None


def test_a_missing_source_image_is_skipped_not_faked(tmp_path):
    gap = _gap(tmp_path / "flats" / "gone.png")
    assert harvest(gap, tmp_path / "motifs") is None


def test_the_next_sighting_is_tried_when_the_first_has_gone(tmp_path):
    """One design's flattened image being gone must not lose the motif when
    three other designs have it too."""
    card = _card_with_a_pumpkin(tmp_path / "flats" / "card.png")
    gap = Gap(description="pumpkin", kind="seasonal", designs=2, sightings=[
        Sighting(design_id="missing1", page=0, box=Box(x=0.3, y=0.4, w=0.4, h=0.2),
                 source_image=str(tmp_path / "flats" / "gone.png"), description="p"),
        Sighting(design_id="abc12345", page=0, box=Box(x=0.28, y=0.37, w=0.44, h=0.24),
                 source_image=str(card), description="p"),
    ])
    got = harvest(gap, tmp_path / "motifs")
    assert got is not None
    assert "abc12345" in got.path.name


def test_a_box_too_small_to_be_anything_is_skipped(tmp_path):
    card = _card_with_a_pumpkin(tmp_path / "flats" / "card.png")
    tiny = Box(x=0.5, y=0.5, w=0.002, h=0.002)
    assert harvest(_gap(card, box=tiny), tmp_path / "motifs") is None


def test_a_crop_that_is_all_subject_says_so(tmp_path):
    """A photographic area has no paper to remove, so nothing comes off. Worth
    saying rather than handing back a rectangle and calling it a motif."""
    photo = tmp_path / "flats" / "photo.png"
    photo.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(3)
    cv2.imwrite(str(photo), rng.integers(0, 255, (600, 600, 3), dtype=np.uint8))

    got = harvest(_gap(photo, box=Box(x=0.2, y=0.2, w=0.6, h=0.6)), tmp_path / "motifs")
    assert got is not None
    assert got.note, "a crop that lost nothing was reported as a clean motif"


# --- the whole list --------------------------------------------------------

def test_every_gap_that_can_be_cut_is(tmp_path):
    card = _card_with_a_pumpkin(tmp_path / "flats" / "card.png")
    gaps = [_gap(card, description=f"motif {i}") for i in range(3)]
    gaps.append(Gap(description="never seen", kind="icon", designs=1))

    cut = harvest_all(gaps, tmp_path / "motifs")
    assert len(cut) == 3, "the ones with sightings were not all cut"
    assert all(c.path.is_file() for c in cut)


def test_the_cuts_go_somewhere_the_matcher_will_not_pick_them_up(tmp_path):
    """They are pictures, not vectors. A PNG sitting in the motif folder would
    be found by nothing and confuse everything."""
    card = _card_with_a_pumpkin(tmp_path / "flats" / "card.png")
    got = harvest(_gap(card), tmp_path / "motifs")
    assert got.path.parent.name == motifs_stage.HARVEST_DIR
    assert got.path.parent.parent == tmp_path / "motifs"

    library = motifs_stage.load(tmp_path / "motifs")
    assert all(not str(e.path).endswith(".png") for e in library), (
        "a harvested picture was loaded into the vector library")


# --- gaps know where they were seen ---------------------------------------

def test_a_gap_records_where_the_motif_actually_is(tmp_path):
    """Without this the crop has nothing to crop from. It comes off the spec,
    which already had it — the box was recorded and never used."""
    from stockforge.schema import (Canvas, ColourRole, DesignDNA, DesignSpec,
                                   MotifElement, MotifKind, Page, Palette, Swatch)

    card = _card_with_a_pumpkin(tmp_path / "flats" / "card.png")
    spec = DesignSpec(
        source_asset_id="a", design_id="d1", confidence=0.9,
        dna=DesignDNA(category="invitation", occasion="halloween",
                      palette=Palette(swatches=[Swatch(role=ColourRole.BACKGROUND,
                                                       hex="#ffffff", coverage=1.0)])),
        pages=[Page(name="front", canvas=Canvas(width_mm=127, height_mm=178),
                    source_image=str(card),
                    elements=[MotifElement(motif=MotifKind.SEASONAL,
                                           description="a grinning carved pumpkin",
                                           box=Box(x=0.28, y=0.37, w=0.44, h=0.24))])],
    )

    found = motifs_stage.gaps([spec], tmp_path / "empty-library")
    assert found, "the pumpkin was not reported missing"
    assert found[0].sightings, "the gap does not know where the motif is"
    assert found[0].sightings[0].source_image == str(card)

    got = harvest(found[0], tmp_path / "motifs")
    assert got is not None, "a gap built from a real spec could not be cut"
