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


# --- a photograph is not a design -----------------------------------------
#
# The reported failure: a Halloween invitation lying on pale wood among
# pumpkins, bats and candles came back cropped to the card's left edge and
# everything to the right of it — most of the photograph, called a design. It
# scored 0.75 and was used, because nothing asked what was inside the
# rectangle it had chosen.

def _invitation_on_a_table(blur=0, wood=(176, 186, 198), size=1200):
    """A pale card on a pale surface with dark props around it."""
    rng = np.random.default_rng(4)
    photo = np.full((size, size, 3), wood, np.uint8)
    photo = cv2.add(photo, rng.integers(0, 9, (size, size, 3)).astype(np.uint8))
    # dark cloth across a corner, candles, pumpkins, bats
    cv2.fillPoly(photo, [np.array([[int(size*.62), 0], [size, 0],
                                   [size, int(size*.42)], [int(size*.78), int(size*.30)]])],
                 (46, 44, 44))
    cv2.ellipse(photo, (int(size*.90), int(size*.74)), (int(size*.085),)*2, 0, 0, 360, (40, 120, 225), -1)
    cv2.ellipse(photo, (int(size*.07), int(size*.90)), (int(size*.080),)*2, 0, 0, 360, (220, 228, 236), -1)
    for cx, cy in ((.05, .47), (.95, .88), (.62, .96)):
        cv2.circle(photo, (int(size*cx), int(size*cy)), int(size*.030), (30, 30, 32), -1)

    x0, y0 = int(size * .225), int(size * .055)
    cw, ch = int(size * .555), int(size * .885)
    cv2.rectangle(photo, (x0 + 8, y0 + 9), (x0 + cw + 11, y0 + ch + 12), (150, 158, 170), -1)
    cv2.rectangle(photo, (x0, y0), (x0 + cw, y0 + ch), (247, 249, 250), -1)
    font = cv2.FONT_HERSHEY_SIMPLEX
    for text, ry, scale in (("HALLOWEEN", .18, 1.5), ("Party", .26, 1.3),
                            ("NOVEMBER 1", .68, .55), ("RSVP BY OCT 24", .86, .45)):
        (tw, _), _ = cv2.getTextSize(text, font, scale, 2)
        cv2.putText(photo, text, (x0 + (cw - tw) // 2, y0 + int(ch * ry)),
                    font, scale, (40, 40, 40), 2, cv2.LINE_AA)
    cv2.ellipse(photo, (x0 + int(cw*.60), y0 + int(ch*.40)), (int(cw*.11),)*2, 0, 0, 360, (40, 130, 225), -1)

    if blur:
        photo = cv2.GaussianBlur(photo, (blur | 1, blur | 1), 0)
    return photo, (x0, y0, cw, ch)


def _iou(quad, truth, shape):
    got = np.zeros(shape[:2], np.uint8)
    cv2.fillConvexPoly(got, quad.astype(np.int32), 1)
    x, y, w, h = truth
    want = np.zeros(shape[:2], np.uint8)
    want[y:y + h, x:x + w] = 1
    return float((got & want).sum()) / max(1, (got | want).sum())


def test_the_card_is_found_among_the_props():
    photo, truth = _invitation_on_a_table()
    quad, state, _, _ = ingest.read_trim(photo)
    assert state == "cropped", "the card on the table was not found at all"
    assert _iou(quad, truth, photo.shape) > 0.90


@pytest.mark.parametrize("blur", [11, 21])
def test_a_crop_with_the_table_in_it_is_refused(blur):
    """The reported bug. With the edges softened the detector proposes a
    rectangle starting at the card's left edge and running to the far corner of
    the photo, and used to take it — it is rectangular, it is a plausible size,
    and it contrasts with what is outside it, which was all anything asked.

    What it is not is a design. Being told "I could not find it" and getting
    the whole photo read is recoverable; being handed a crop of the tablecloth
    and told it is your artwork is not.
    """
    photo, truth = _invitation_on_a_table(blur=blur)
    quad, state, _, note = ingest.read_trim(photo)
    if quad is not None:
        assert _iou(quad, truth, photo.shape) > 0.85, (
            "it cropped to something that is not the card")
    else:
        assert state == "unsure" and note, "it gave up without saying so"


def test_a_design_is_a_few_flat_colours_and_a_table_is_not():
    """The measure the refusal rests on. Named here because the numbers are the
    argument: across every fixture in this file the real card needed one or two
    colours to cover nine tenths of itself, and every rectangle that had the
    table in it needed three or more."""
    photo, (x, y, w, h) = _invitation_on_a_table()
    card = np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]], np.float32)
    whole = np.array([[0, 0], [photo.shape[1], 0],
                      [photo.shape[1], photo.shape[0]], [0, photo.shape[0]]], np.float32)

    assert ingest.flat_colours(photo, card) < ingest.TOO_MANY_COLOURS
    assert ingest.flat_colours(photo, whole) >= ingest.TOO_MANY_COLOURS, (
        "a whole photograph of a table counts as few enough colours to be a design")


def test_scattered_props_are_not_mistaken_for_the_wording():
    """Type chooses between candidates; it must never reject one. Candy corn
    and paper spiders pass every cheap glyph test there is, so a rule that
    demanded a crop hold "most of the text" would throw away the card in favour
    of the confetti around it — which is how the first attempt at this went."""
    photo = _listing_photo((244, 246, 247), props=True)
    quad, state, _, _ = ingest.read_trim(photo)
    assert state == "cropped"
    aspect, _, _ = _cropped_aspect(photo)
    assert abs(aspect - TRUE_ASPECT) / TRUE_ASPECT < 0.05


# --- a real design is not two flat colours --------------------------------
#
# Eight real Etsy Halloween invitations, run through the detector: seven came
# back "the artwork was not found inside the listing photo" with every single
# candidate scored at exactly zero. Not a near miss — a hard reject. The cards
# were in plain view in the middle of the frame.
#
# The gate was `flat_colours(quad) >= 4`. Those cards needed eleven to sixteen,
# because a rendered skull, spilled wine, blood splatter, cobwebs and four
# weights of type is not two flat colours. The fixtures above are all simple
# designs, so nothing here ever caught it.

def _busy_card() -> np.ndarray:
    """A card with as many colours as a real one — the case the fixtures above
    do not cover, and the one the whole shop is made of."""
    rng = np.random.default_rng(7)
    art = np.full((CARD_H, CARD_W, 3), 250, np.uint8)
    # an illustration with real tonal range, the way a rendered skull or a
    # watercolour ghost has one
    for i in range(34):
        cv2.circle(art, (int(rng.integers(90, CARD_W - 90)),
                         int(rng.integers(240, CARD_H - 190))),
                   int(rng.integers(14, 52)),
                   tuple(int(v) for v in rng.integers(20, 235, 3)), -1)
    cv2.putText(art, "LET'S GET", (110, 130), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (32, 32, 32), 3)
    cv2.putText(art, "Sheet Faced", (70, 210), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (28, 28, 170), 3)
    cv2.putText(art, "SATURDAY OCTOBER 26", (60, 690), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (40, 40, 40), 2)
    cv2.putText(art, "THE LAST CALL CRYPT", (62, 740), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (35, 35, 120), 2)
    return art


def _wood(size, rng):
    """Backdrop with grain in it.

    The earlier fixtures use a flat colour, which is fine for finding an edge
    and useless for this: a flat backdrop is FLATTER than any real card, so a
    detector that prefers the flatter rectangle would pick the tabletop and the
    fixture would call that correct. Every listing photo in the shop is wood,
    linen or board, and all three are a continuum of tones. Measured on the
    eight real photos: the frame needed 11 to 40 flat colours and the card
    inside it 11 to 32 — the card is always the flatter half, and a fixture
    that says otherwise is testing a world nobody photographs in.
    """
    base = np.full((size, size, 3), (96, 104, 120), np.uint8).astype(np.int16)
    grain = rng.normal(0, 26, (size, size, 1))
    for y in range(0, size, 47):                       # planks
        base[y:y + 3, :] -= 22
    base = np.clip(base + grain, 0, 255).astype(np.uint8)
    return cv2.GaussianBlur(base, (3, 3), 0)


def _busy_photo(size=1200):
    rng = np.random.default_rng(3)
    photo = _listing_photo((96, 104, 120), props=True, shadow=True, size=size, angle=1.5)
    wood = _wood(size, rng)
    plain = np.all(np.abs(photo.astype(np.int16) - np.array([96, 104, 120])) < 6, axis=2)
    photo[plain] = wood[plain]
    cx = cy = size // 2
    photo[cy - CARD_H // 2:cy + CARD_H // 2,
          cx - CARD_W // 2:cx + CARD_W // 2] = _busy_card()
    return photo


def test_a_colourful_design_is_still_found():
    """The reported failure. Nothing about this card is ambiguous to a human —
    it is the only rectangle in the picture — and the detector scored every
    candidate at zero because the artwork had too many colours in it."""
    photo = _busy_photo()
    quad, state, confidence, note = ingest.read_trim(photo)
    assert quad is not None, (
        f"a colourful card was not found at all (state {state!r}, "
        f"confidence {confidence:.2f}) — this is the gate rejecting real artwork")
    got, _, _ = _cropped_aspect(photo)
    assert abs(got - TRUE_ASPECT) < 0.08, f"cropped to {got:.2f}, card is {TRUE_ASPECT:.2f}"


# There is no synthetic test here for "the card is flatter than the photo it
# sits in", and that is deliberate. Two fixtures were written for it and both
# said the opposite — a painted backdrop quantises to three or four flat
# colours and an illustrated card to a dozen, so the FIXTURE is flatter outside
# the card than in. Real wood, linen and board are not: measured on eight real
# listing photos, the frame needed 11 to 40 and the card inside it 11 to 32.
#
# The fixture could have been bent until it agreed. That is how you get a test
# that passes for ever and protects nothing, so the claim rests on the real
# measurement and on `test_a_colourful_design_is_still_found` below, which asks
# the only question that matters: did it find the card.
#
# What this does mean, and it is worth knowing: on a clean studio backdrop the
# flatness term goes quiet rather than helping, because everything in the frame
# scores about the same. It earns its place on cluttered photographs, which is
# where the detector was failing.

def test_the_type_signal_cannot_be_won_by_taking_in_more_of_the_table():
    """The worst bug in the detector, and the quietest. Ranking on "how much of
    the type is inside this box" only ever goes UP as the box grows, so the
    rectangle that swallowed the card, the table and the props scored a perfect
    1.00 and the card itself scored 0.57. The signal meant to find the design
    was voting for the tablecloth on every photograph in the shop."""
    photo = _busy_photo()
    h, w = photo.shape[:2]
    ink = ingest.text_ink(photo)
    card = np.float32([[w // 2 - CARD_W // 2, h // 2 - CARD_H // 2],
                       [w // 2 + CARD_W // 2, h // 2 - CARD_H // 2],
                       [w // 2 + CARD_W // 2, h // 2 + CARD_H // 2],
                       [w // 2 - CARD_W // 2, h // 2 + CARD_H // 2]])
    whole = np.float32([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]])

    total = int((ink > 0).sum())
    assert (ingest.text_held(ink, whole, total)
            >= ingest.text_held(ink, card, total)), "this test is measuring nothing"
    assert ingest.text_density(ink, card) > ingest.text_density(ink, whole), (
        "the card is not more densely printed than the whole photograph, so "
        "the signal still rewards grabbing the table")


def test_a_crop_that_is_no_shape_anybody_prints_is_flagged():
    """Finding a card is not the same as framing it right, and a wrong crop
    that nothing flags is worse than no crop: everything downstream then
    measures itself against the wrong rectangle and says nothing about it.

    The flag is the aspect, not the confidence. Confidence was tried first and
    it does not work — see TRIMS in ingest.py. A printed card is 5x7 or A6 or
    square; a crop at 0.86 is between sizes, and on the real photos that was
    exactly the shape the clipped ones came out."""
    photo = _busy_photo()
    quad, state, confidence, note = ingest.read_trim(photo)
    assert state == "cropped", f"an easy fixture came back {state!r}"

    # The same crop, squashed to a shape no printer sells.
    squashed = quad.copy()
    squashed[:, 1] = quad[:, 1].mean() + (quad[:, 1] - quad[:, 1].mean()) * 0.82
    assert ingest.off_trim(squashed) > ingest.TRIM_SLACK, "this test measures nothing"
    assert ingest.off_trim(quad) <= ingest.TRIM_SLACK, (
        "a correct 5x7 crop was called an odd shape")


def test_the_flag_does_not_fire_on_a_hard_but_correct_crop():
    """The reason the confidence bar was dropped, kept as a test so it is not
    put back. A white card on a white backdrop is the owner's commonest photo
    and the hardest case here: it crops to an aspect error of 0.003 and scores
    0.81, which any sensible-looking confidence bar would have flagged. Being
    hard to find is not the same as being got wrong, and flagging it costs a
    design out of the twenty-four."""
    photo = _listing_photo((244, 246, 247))
    got, state, confidence = _cropped_aspect(photo)
    assert abs(got - TRUE_ASPECT) < 0.02, f"the fixture no longer crops right: {got:.3f}"
    assert state == "cropped", (
        f"a crop accurate to {abs(got - TRUE_ASPECT):.3f} was flagged as doubtful "
        f"(confidence {confidence:.2f}) — that is the bar being put back")
