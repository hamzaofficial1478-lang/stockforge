"""Finding the artwork inside a listing photo.

The owner ran a real Halloween invitation through and the review log said:

    'invitation': the rebuild is the wrong shape — 0.71 against the source's
    1.00, so the trim was misread

A source aspect of 1.00 is a square, and the card is a 5x7. The square was the
whole listing photograph: a white card lying on a pale backdrop with candy corn
and paper spiders scattered over it, including across its own edges. Flattening
never found the card, and — far worse — reported not finding it as "this image
must already be flat". Every stage after that measured itself against a
photograph of a card instead of the card, which is why the text came out beside
the artwork rather than on it.

Two faults, and the second is the one that did the damage:

  the detector    one Canny at fixed thresholds. Fine on a white card against
                  dark wood, useless on a white card against a pale backdrop,
                  where the edge is six or seven grey levels.
  the reporting   `_looks_flat(img, None)` returned True. A failure to find
                  became a confident wrong answer, silently.
"""

import cv2
import numpy as np
import pytest

from stockforge.stages import ingest


CARD_W, CARD_H = 560, 784           # 5x7, so aspect 0.714
TRUE_ASPECT = CARD_W / CARD_H


def _card() -> np.ndarray:
    art = np.full((CARD_H, CARD_W, 3), 252, np.uint8)
    cv2.putText(art, "LET'S GET", (120, 150), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (40, 40, 40), 3)
    cv2.putText(art, "Spooky", (180, 240), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (50, 50, 50), 3)
    cv2.putText(art, "HALLOWEEN PARTY", (70, 520), cv2.FONT_HERSHEY_SIMPLEX, 0.85, (40, 40, 40), 2)
    cv2.putText(art, "OCT 30 6PM", (170, 600), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (60, 60, 60), 2)
    return art


def _listing_photo(backdrop, props=True, shadow=True, size=1200, angle=1.5) -> np.ndarray:
    """A card photographed on a decorated surface — what an Etsy listing is."""
    rng = np.random.default_rng(11)
    photo = np.full((size, size, 3), backdrop, np.uint8)

    if props:
        for _ in range(70):                                   # candy corn
            x, y = int(rng.integers(0, size)), int(rng.integers(0, size))
            cv2.ellipse(photo, (x, y), (16, 24), float(rng.integers(0, 180)),
                        0, 360, (60, 150, 250), -1)
        for _ in range(28):                                   # paper spiders
            x, y = int(rng.integers(0, size)), int(rng.integers(0, size))
            cv2.circle(photo, (x, y), 11, (35, 35, 35), -1)
            for a in range(0, 360, 45):
                cv2.line(photo, (x, y),
                         (int(x + 26 * np.cos(np.radians(a))),
                          int(y + 26 * np.sin(np.radians(a)))), (35, 35, 35), 2)

    cx = cy = size // 2
    if shadow:
        # Offset and blurred enough to be visible around the card, which is
        # what a drop shadow looks like in any real product photograph and is
        # most of what makes a white card read against a white surface.
        mask = np.zeros((size, size), np.uint8)
        cv2.rectangle(mask, (cx - CARD_W // 2 + 26, cy - CARD_H // 2 + 32),
                      (cx + CARD_W // 2 + 26, cy + CARD_H // 2 + 32), 255, -1)
        soft = cv2.GaussianBlur(mask, (81, 81), 0).astype(np.float32) / 255.0
        photo = np.clip(photo.astype(np.float32) * (1 - 0.30 * soft[..., None]),
                        0, 255).astype(np.uint8)

    r = np.radians(angle)
    corners = []
    for px, py in [(-CARD_W / 2, -CARD_H / 2), (CARD_W / 2, -CARD_H / 2),
                   (CARD_W / 2, CARD_H / 2), (-CARD_W / 2, CARD_H / 2)]:
        corners.append([cx + px * np.cos(r) - py * np.sin(r),
                        cy + px * np.sin(r) + py * np.cos(r)])
    m = cv2.getPerspectiveTransform(
        np.float32([[0, 0], [CARD_W, 0], [CARD_W, CARD_H], [0, CARD_H]]),
        np.float32(corners))
    warped = cv2.warpPerspective(_card(), m, (size, size))
    mask = cv2.warpPerspective(np.full((CARD_H, CARD_W), 255, np.uint8), m, (size, size))
    photo[mask > 0] = warped[mask > 0]

    if props:                                   # props sitting ON the card edge
        for _ in range(14):
            t = rng.random()
            x = int(cx - CARD_W / 2 + t * CARD_W)
            y = int(cy - CARD_H / 2) if rng.random() < .5 else int(cy + CARD_H / 2)
            cv2.ellipse(photo, (x, y), (16, 24), float(rng.integers(0, 180)),
                        0, 360, (60, 150, 250), -1)
    return photo


def _cropped_aspect(photo):
    quad, state, confidence, note = ingest.read_trim(photo)
    assert quad is not None, f"the card was not found (state {state!r})"
    out = ingest._warp(photo, quad)
    return out.shape[1] / out.shape[0], state, confidence


# --- the detector --------------------------------------------------------

@pytest.mark.parametrize("backdrop,label", [
    ((244, 246, 247), "pale — a white card on a white surface, the owner's case"),
    ((60, 82, 105), "dark wood"),
    ((150, 150, 152), "mid grey"),
])
def test_the_card_is_cropped_to_its_real_shape(backdrop, label):
    """Within a few percent of 5x7, whatever it was photographed on. The old
    detector managed only the dark case."""
    aspect, state, _ = _cropped_aspect(_listing_photo(backdrop))
    assert state == "cropped", label
    assert abs(aspect - TRUE_ASPECT) / TRUE_ASPECT < 0.05, (
        f"{label}: cropped to {aspect:.3f}, the card is {TRUE_ASPECT:.3f}")


def test_the_crop_survives_props_lying_across_the_card_edge():
    """Candy corn on the border breaks the outline into pieces, none of which
    is a quadrilateral. That is what closing the mask first is for."""
    aspect, _, _ = _cropped_aspect(_listing_photo((244, 246, 247), props=True))
    assert abs(aspect - TRUE_ASPECT) / TRUE_ASPECT < 0.05


def test_a_tilted_card_comes_back_straight():
    photo = _listing_photo((244, 246, 247), angle=6.0)
    aspect, _, _ = _cropped_aspect(photo)
    assert abs(aspect - TRUE_ASPECT) / TRUE_ASPECT < 0.08


# --- knowing when it is already the artwork -------------------------------

def test_a_flat_export_is_left_alone():
    flat = np.full((1050, 750, 3), 250, np.uint8)
    cv2.putText(flat, "HALLOWEEN", (90, 400), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (40, 40, 40), 4)
    quad, state, _, note = ingest.read_trim(flat)
    assert state == "flat"
    assert quad is None, "a flat export was cropped"
    assert note == ""


def test_an_artwork_with_its_own_margin_is_not_cropped_to_the_ink():
    """The artwork inside a flat export is the same shape as the file, just
    smaller. Cropping to it throws away margin that is part of the design —
    and it was doing exactly that to the test fixtures."""
    flat = np.full((700, 500, 3), (240, 246, 250), np.uint8)
    cv2.rectangle(flat, (60, 60), (440, 200), (110, 143, 125), -1)
    for i, y in enumerate((300, 380, 460, 560)):
        cv2.rectangle(flat, (80 + i * 6, y), (420, y + 44), (40, 43, 43), -1)
    _, state, _, _ = ingest.read_trim(flat)
    assert state == "flat"


# --- and saying so when it cannot tell ------------------------------------

def test_failing_to_find_the_card_is_not_reported_as_already_flat(monkeypatch):
    """The fault that did the real damage, tested where it lived.

    `_looks_flat(img, None)` returned True, so "I could not find the card"
    came out as "this must already be flat" — and a square photograph of a 5x7
    card went downstream as the card, with every later stage measuring itself
    against it.

    The detector is stubbed rather than fed a deliberately awkward photo,
    because the decision is the thing that was broken: whatever the detector
    can or cannot manage on a given day, not finding the card must never be
    reported as having found nothing to look for.
    """
    monkeypatch.setattr(ingest, "find_card", lambda img, **kw: (None, 0.0))

    photo = _listing_photo((244, 246, 247))          # visibly a photo of a card
    quad, state, _, note = ingest.read_trim(photo)
    assert quad is None
    assert state == "unsure", "a failure to find the card was reported as flat"
    assert note, "it failed silently"
    assert "could not be found" in note
    assert "Crop it" in note, "the note does not say what to do about it"


def test_a_quiet_image_with_no_card_found_is_still_called_flat(monkeypatch):
    """The other half of the same decision. A plain export with nothing to
    find is flat, and must not start collecting warnings — a warning on every
    design is the same as no warning at all."""
    monkeypatch.setattr(ingest, "find_card", lambda img, **kw: (None, 0.0))

    flat = np.full((1050, 750, 3), 250, np.uint8)
    cv2.putText(flat, "HALLOWEEN", (90, 400), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (40, 40, 40), 4)
    _, state, _, note = ingest.read_trim(flat)
    assert state == "flat"
    assert note == ""


def test_the_doubt_is_recorded_on_the_asset(tmp_path, monkeypatch):
    """A warning in a log nobody reads is the same as no warning."""
    monkeypatch.setattr(ingest, "find_card", lambda img, **kw: (None, 0.0))

    path = tmp_path / "photo.jpg"
    cv2.imwrite(str(path), _listing_photo((244, 246, 247)))
    flat = ingest.flatten_image(path, tmp_path / "flats")
    assert flat.trim == "unsure"
    assert flat.note
    assert flat.is_mockup is True


def test_a_confident_crop_records_what_it_did(tmp_path):
    path = tmp_path / "photo.jpg"
    cv2.imwrite(str(path), _listing_photo((60, 82, 105)))
    flat = ingest.flatten_image(path, tmp_path / "flats")
    assert flat.trim == "cropped"
    assert flat.note == ""
    assert flat.trim_confidence > 0.5
    assert abs(flat.aspect - TRUE_ASPECT) / TRUE_ASPECT < 0.05, (
        f"recorded aspect {flat.aspect} is not the card's {TRUE_ASPECT:.3f}")


def test_a_rectangle_inside_a_flat_export_is_not_cropped_out_as_the_card():
    """A rectangle is not a card. The detector will find plenty of them inside
    a flat design — a panel, a band of colour, the block the type sits in — and
    cropping to one throws away margin that is part of the design.

    What settles it is whether anything is around it. Measured across the
    project's own fixtures and the photographs, every flat file scores 0.0 on
    this because what is outside the rectangle is the same paper as inside,
    while a card on a grey backdrop scores 50 and one on wood 160.
    """
    front = np.full((700, 500, 3), (240, 246, 250), np.uint8)
    cv2.rectangle(front, (60, 60), (440, 190), (110, 143, 125), -1)
    cv2.rectangle(front, (110, 330), (390, 400), (40, 43, 43), -1)

    quad, _ = ingest.find_card(front)
    assert quad is not None, "this test is pointless if nothing is found at all"
    assert not ingest._sits_on_something(front, quad), "the paper outside read as a backdrop"

    _, state, _, _ = ingest.read_trim(front)
    assert state == "flat", "a flat export was cropped to a rectangle inside itself"


def test_a_card_on_a_plain_studio_backdrop_is_still_cropped():
    """The other side of it. A clean flat-lay has no clutter to give it away,
    so the backdrop's colour is the only signal there is — and it has to be
    enough, or every tidily photographed listing would be read as flat."""
    art = np.full((440, 320, 3), (240, 246, 250), np.uint8)
    cv2.rectangle(art, (40, 40), (280, 120), (110, 143, 125), -1)
    photo = np.full((700, 700, 3), (180, 190, 200), np.uint8)
    photo[120:560, 160:480] = art

    quad, state, _, _ = ingest.read_trim(photo)
    assert state == "cropped", "a card on a plain backdrop was read as already flat"
    assert ingest._sits_on_something(photo, quad)
    out = ingest._warp(photo, quad)
    assert abs(out.shape[1] / out.shape[0] - 320 / 440) < 0.05
