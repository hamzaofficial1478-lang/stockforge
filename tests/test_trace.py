"""Turning a picture of a motif into a drawing the library can use.

The link that was missing. `motifs.draw` asked an image model for a picture and
wrote it to `_drawn/` with a note saying to trace it; `motifs.harvest` cut one
out of the owner's own artwork and wrote it to `_harvested/` with the same
note. The library only ever globs `*.svg`. So asking a model for a pumpkin
produced homework rather than a pumpkin, and no amount of drawing ever filled a
hole in a design.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from stockforge.schema import Box, MotifElement, MotifKind
from stockforge.stages import motifs as m
from stockforge.stages.trace import trace


def _flat_drawing(path: Path, blur: int = 3) -> Path:
    """What DRAW_SYSTEM asks a model for: flat colours, one subject, on white.

    Blurred, because nothing comes out of an image model with hard pixel edges
    and the soft rim is what the tracer has to cope with.
    """
    img = np.full((400, 400, 3), 255, np.uint8)
    cv2.ellipse(img, (200, 230), (120, 105), 0, 0, 360, (40, 120, 235), -1)
    cv2.rectangle(img, (188, 104), (212, 132), (60, 110, 70), -1)
    cv2.ellipse(img, (200, 265), (58, 30), 0, 0, 180, (20, 20, 25), -1)
    if blur:
        img = cv2.GaussianBlur(img, (blur, blur), 0)
    cv2.imwrite(str(path), img)
    return path


def _cutout(path: Path) -> Path:
    """What `harvest` produces: the artwork with the paper taken off, so it
    carries an alpha channel rather than sitting on a white square."""
    cut = np.zeros((300, 300, 4), np.uint8)
    cv2.circle(cut, (150, 150), 110, (70, 160, 230, 255), -1)
    cv2.circle(cut, (150, 150), 42, (30, 30, 40, 255), -1)
    cv2.imwrite(str(path), cut)
    return path


# --- it has to produce something the library will actually load -------------

def test_a_traced_picture_becomes_a_motif_the_library_reads(tmp_path):
    out = tmp_path / "pumpkin.svg"
    trace(_flat_drawing(tmp_path / "p.png"), out, kind="seasonal",
          name="Carved pumpkin", description="a carved pumpkin, lit from within",
          tags=["pumpkin", "halloween"])

    entry = m.read(out)
    assert entry.kind == "seasonal", "the library could not tell what kind it is"
    assert entry.name == "Carved pumpkin"
    assert "pumpkin" in entry.tags
    assert m.scan(tmp_path), "scan skipped it — check the filename rules"


def test_a_design_asking_for_it_now_finds_it(tmp_path):
    """The whole point. A hole in a design is a description that matched
    nothing; after this it matches a drawing."""
    said = "a carved pumpkin, lit from within"
    el = MotifElement(motif=MotifKind.SEASONAL, description=said,
                      box=Box(x=.1, y=.1, w=.2, h=.2))

    assert not m.rank(el, m.load(tmp_path)), "this test is measuring nothing"

    m.adopt(_flat_drawing(tmp_path / "p.png"), tmp_path,
            kind="seasonal", description=said)
    ranked = m.rank(el, m.load(tmp_path))
    assert ranked and ranked[0][1] >= m.DEFAULT_THRESHOLD, (
        f"the traced motif does not answer to what it is: {ranked[:1]}")


def test_a_cutout_with_alpha_traces_too(tmp_path):
    """The other source. `harvest` takes the paper off, so its pictures are
    alpha rather than white — and reading the background off the corners of a
    fully transparent image finds nothing."""
    entry = m.adopt(_cutout(tmp_path / "c.png"), tmp_path,
                    kind="icon", description="a full moon behind a dark circle")
    body = (tmp_path / f"{entry.library_id}.svg").read_text()
    assert body.count("<path") >= 2, "the hole in the middle was lost"


# --- the artefacts that make a trace look cheap ----------------------------

def test_the_soft_rim_round_a_shape_is_not_traced_as_a_halo(tmp_path):
    """Nothing comes out of an image model with hard edges, and k-means will
    happily spend a whole colour on the soft pixels along a boundary. Traced,
    that comes back as a pale ring around the shape — which the first version
    of this did, right round a pumpkin's body."""
    got = trace(_flat_drawing(tmp_path / "p.png", blur=9), tmp_path / "out.svg",
                colours=5)
    body = (tmp_path / "out.svg").read_text()

    # The rim colour sits between the orange body and the white paper: a pale
    # peach. If one is in the output it was traced as a shape.
    import re
    fills = re.findall(r'fill="#([0-9a-f]{6})"', body)
    def pale(hexc):
        r, g, b = (int(hexc[i:i + 2], 16) for i in (0, 2, 4))
        return min(r, g, b) > 170 and max(r, g, b) < 250
    assert not any(pale(f) for f in fills), (
        f"a boundary colour was traced as a shape: {[f for f in fills if pale(f)]}")
    assert got.paths, "it rejected everything"


def test_thinness_is_measured_but_does_not_decide_alone(tmp_path):
    """`_is_thin` is half the halo test and was briefly all of it, which cost
    half a real library. It still has to measure what it says it measures."""
    from stockforge.stages.trace import _is_thin

    solid = np.zeros((200, 200), np.uint8)
    cv2.circle(solid, (100, 100), 30, 255, -1)
    assert not _is_thin(solid), "a solid disc was called thin"

    rim = np.zeros((200, 200), np.uint8)
    cv2.circle(rim, (100, 100), 60, 255, 3)
    assert _is_thin(rim), "a three-pixel ring was not called thin"


def test_the_same_picture_traces_the_same_way_every_time(tmp_path):
    """k-means picks its starting centres at random. Without a seed the same
    picture becomes a different drawing on different runs, and nothing about a
    motif is reproducible — the same bug this project already fixed in the
    critique stage.

    The picture has to be one where the clustering is genuinely ambiguous. The
    first version of this test used the flat pumpkin above, which has three
    obvious colours and lands on them whatever the seed — so the test passed
    with the seeding deleted, which is a test that protects nothing. Measured
    on the picture below: unseeded gives three different answers in five runs.
    """
    rng = np.random.default_rng(5)
    busy = np.full((300, 300, 3), 255, np.uint8)
    for _ in range(60):
        cv2.circle(busy, tuple(rng.integers(40, 260, 2).tolist()),
                   int(rng.integers(18, 60)),
                   tuple(int(v) for v in rng.integers(0, 255, 3)), -1)
    src = tmp_path / "busy.png"
    cv2.imwrite(str(src), busy)

    first = None
    for run in range(4):
        out = tmp_path / f"run{run}.svg"
        trace(src, out, colours=6)
        body = out.read_text()
        if first is None:
            first = body
        assert body == first, f"run {run} traced differently from run 0"


# --- what it refuses to do -------------------------------------------------

def test_an_empty_picture_is_refused_rather_than_written(tmp_path):
    """A blank SVG in the library is worse than a gap: the gap is reported and
    the blank silently draws nothing."""
    blank = tmp_path / "blank.png"
    cv2.imwrite(str(blank), np.full((200, 200, 3), 255, np.uint8))
    with pytest.raises(ValueError):
        trace(blank, tmp_path / "out.svg")
    assert not (tmp_path / "out.svg").exists()


def test_where_it_came_from_is_written_inside_the_drawing(tmp_path):
    """Six months on, "did I draw this or did a model?" is a question about
    whether this may be sold. A file beside it gets separated from it the first
    time somebody tidies up."""
    entry = m.adopt(_flat_drawing(tmp_path / "p.png"), tmp_path,
                    kind="seasonal", description="a carved pumpkin",
                    provenance={"generated": True, "model": "some-image-model"})
    body = (tmp_path / f"{entry.library_id}.svg").read_text()
    assert "generated" in body and "some-image-model" in body


def test_a_description_with_markup_in_it_cannot_break_the_file(tmp_path):
    """These files are read back with a regex and handed to a renderer, and the
    description in them came from a model reading somebody's listing photo.

    Asserted by parsing rather than by looking for particular strings: a file
    that an XML parser accepts is a file whose description did not escape into
    its markup, whatever the description was trying to do.
    """
    import xml.etree.ElementTree as ET

    entry = m.adopt(_flat_drawing(tmp_path / "p.png"), tmp_path, kind="icon",
                    description='a <script>alert(1)</script> & "quoted" -- thing')
    path = tmp_path / f"{entry.library_id}.svg"
    ET.parse(path)                       # raises if the markup was broken
    body = path.read_text()
    assert "<script" not in body, "a tag from the description survived into the file"
    assert m.read(path).kind == "icon", "the metadata no longer reads back"
    assert entry.library_id.replace("-", "").isalnum(), (
        f"the filename is not plain, so scan() will skip it: {entry.library_id}")


def test_two_motifs_with_the_same_description_do_not_overwrite_each_other(tmp_path):
    said = "a carved pumpkin"
    first = m.adopt(_flat_drawing(tmp_path / "a.png"), tmp_path, description=said)
    second = m.adopt(_cutout(tmp_path / "b.png"), tmp_path, description=said)
    assert first.library_id != second.library_id
    assert len(m.scan(tmp_path)) == 2


# --- thin is not the same as smudged ---------------------------------------
#
# From a real run over a real shop: 32 motifs harvested, and 16 came back
# "nothing traceable in ...". Every one of them was something harvest had
# already marked `thin` — a spiderweb, a rule, a chevron, a spider on a thread.
# The halo test was "has it got an inside", and a web has not got an inside
# either. Half a library thrown away as smudges.
#
# Thinness is necessary for a halo and nowhere near sufficient. What actually
# marks one is its COLOUR: an anti-aliased rim is not a colour anybody chose,
# it is the two colours it lies between, mixed.

def _web(path: Path) -> Path:
    """A spiderweb cutout — all line, no inside, and a real motif."""
    web = np.zeros((300, 300, 4), np.uint8)
    for a in range(0, 360, 30):
        end = (int(150 + 140 * np.cos(np.radians(a))),
               int(150 + 140 * np.sin(np.radians(a))))
        cv2.line(web, (150, 150), end, (40, 40, 45, 255), 2)
    for r in (50, 90, 130):
        cv2.circle(web, (150, 150), r, (40, 40, 45, 255), 2)
    cv2.imwrite(str(path), web)
    return path


@pytest.mark.parametrize("draw,label", [
    (_web, "a spiderweb"),
    (lambda p: (cv2.imwrite(str(p), _chevrons()), p)[1], "a row of chevrons"),
])
def test_thin_artwork_is_traced_rather_than_thrown_away(tmp_path, draw, label):
    got = trace(draw(tmp_path / "thin.png"), tmp_path / "thin.svg")
    assert got.paths > 0, f"{label} was thrown away as a smudge"


def _chevrons() -> np.ndarray:
    art = np.zeros((200, 400, 4), np.uint8)
    for i in range(6):
        pts = np.array([[40 + i * 60, 60], [70 + i * 60, 100], [40 + i * 60, 140]])
        cv2.polylines(art, [pts], False, (60, 60, 70, 255), 3)
    return art


def test_a_rim_is_still_dropped_once_thin_alone_stops_deciding(tmp_path):
    """The other half. Loosening the halo test must not bring the halo back —
    and it did, for one round: the pale ring around a pumpkin blends into the
    PAPER, and with the paper masked out there was no second end to the blend
    for the test to find."""
    import re
    got = trace(_flat_drawing(tmp_path / "p.png", blur=9), tmp_path / "out.svg",
                colours=5)
    fills = re.findall(r'fill="#([0-9a-f]{6})"', (tmp_path / "out.svg").read_text())

    def pale(hexc):
        r, g, b = (int(hexc[i:i + 2], 16) for i in (0, 2, 4))
        return min(r, g, b) > 170 and max(r, g, b) < 250

    assert not any(pale(f) for f in fills), f"the halo is back: {[f for f in fills if pale(f)]}"
    assert got.paths, "and it threw away the drawing as well"


def test_a_colour_that_is_two_others_mixed_is_recognised(tmp_path):
    """The test itself, on its own. A blend sits between two colours and close
    to the straight line between them; ink does not."""
    from stockforge.stages.trace import _is_a_blend

    orange, white = (40, 120, 235), (255, 255, 255)
    halo = tuple(int((a + b) / 2) for a, b in zip(orange, white))
    assert _is_a_blend(halo, [orange, white]), "a midpoint was not called a blend"
    assert not _is_a_blend((40, 40, 45), [orange, white]), (
        "near-black ink was called a blend of orange and white")
    assert not _is_a_blend(orange, [orange, white]), "an endpoint is not a blend"
