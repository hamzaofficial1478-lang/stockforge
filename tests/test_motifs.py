"""Tests for the motif matcher — the step that decides whether a rebuild has
decoration on it or a hole where the decoration should be."""

from pathlib import Path

import pytest

from stockforge.schema import (
    Box, Canvas, ColourRole, DesignDNA, DesignSpec, MotifElement, MotifKind,
    Page, Palette, Provenance, Swatch,
)
from stockforge.stages import motifs as motifs_stage
from stockforge.stages.render import _motif_body, render


TAGGED = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"
     data-kind="seasonal" data-tags="pumpkin, jack-o-lantern, carved, halloween">
  <title>Carved pumpkin</title>
  <desc>grinning jack-o-lantern with a stubby stalk</desc>
  <!-- a note to whoever draws the next one -->
  <ellipse cx="50" cy="60" rx="34" ry="28"/>
</svg>
"""

UNTAGGED = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <ellipse cx="50" cy="50" rx="30" ry="20"/>
</svg>
"""


def _write(folder: Path, name: str, body: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(body)
    return path


def _motif_el(kind: MotifKind, description: str, **kw) -> MotifElement:
    return MotifElement(motif=kind, description=description,
                        box=Box(x=0.3, y=0.5, w=0.4, h=0.3), **kw)


def _spec(*elements) -> DesignSpec:
    return DesignSpec(
        source_asset_id="abc",
        dna=DesignDNA(
            category="greeting card", occasion="halloween",
            palette=Palette(swatches=[
                Swatch(role=ColourRole.BACKGROUND, hex="#ffffff", coverage=0.8),
                Swatch(role=ColourRole.ACCENT, hex="#e8622a", coverage=0.2),
            ]),
        ),
        pages=[Page(name="cover", canvas=Canvas(width_mm=127, height_mm=178),
                    elements=list(elements))],
        provenance=Provenance(stock_safe=True),
        confidence=0.9,
    )


# --- reading the library --------------------------------------------------

def test_metadata_is_read_from_the_svg(tmp_path):
    entry = motifs_stage.read(_write(tmp_path, "pumpkin-01.svg", TAGGED))
    assert entry.library_id == "pumpkin-01"
    assert entry.kind == "seasonal"
    assert entry.name == "Carved pumpkin"
    assert "halloween" in entry.tags
    assert {"pumpkin", "carved", "lantern"} <= entry.tokens


def test_an_untagged_drawing_still_matches_on_its_filename(tmp_path):
    entry = motifs_stage.read(_write(tmp_path, "sprig-eucalyptus-01.svg", UNTAGGED))
    assert entry.kind == ""
    assert "sprig" in entry.tokens


def test_a_kind_that_is_not_a_kind_is_ignored_not_trusted(tmp_path):
    bad = UNTAGGED.replace('viewBox="0 0 100 100"',
                           'viewBox="0 0 100 100" data-kind="pumpkinish"')
    assert motifs_stage.read(_write(tmp_path, "x-01.svg", bad)).kind == ""


def test_the_library_is_re_read_when_a_motif_is_added(tmp_path):
    _write(tmp_path, "pumpkin-01.svg", TAGGED)
    assert len(motifs_stage.load(tmp_path)) == 1
    _write(tmp_path, "bat-01.svg", UNTAGGED)
    assert len(motifs_stage.load(tmp_path)) == 2


# --- scoring --------------------------------------------------------------

def test_the_right_drawing_wins_and_the_wrong_one_is_refused(tmp_path):
    _write(tmp_path, "pumpkin-01.svg", TAGGED)
    library = motifs_stage.load(tmp_path)

    entry, score = motifs_stage.match(
        _motif_el(MotifKind.SEASONAL, "grinning carved jack-o-lantern, warm light"),
        library)
    assert entry is not None and entry.library_id == "pumpkin-01"
    assert score > 0.8

    entry, score = motifs_stage.match(
        _motif_el(MotifKind.FRAME, "thin art-deco frame with stepped corners"), library)
    assert entry is None, "a pumpkin is not a frame"
    assert score < motifs_stage.DEFAULT_THRESHOLD


def test_the_right_kind_alone_is_not_enough_to_place_a_motif(tmp_path):
    """Pumpkins and ghosts are both `seasonal`. Picking the wrong one of those
    is exactly the mistake the threshold exists to stop."""
    _write(tmp_path, "pumpkin-01.svg", TAGGED)
    library = motifs_stage.load(tmp_path)
    entry, score = motifs_stage.match(
        _motif_el(MotifKind.SEASONAL, "a friendly floating ghost, sheet draped"), library)
    assert entry is None
    assert score == pytest.approx(motifs_stage.KIND_WEIGHT, abs=0.01)


def test_an_empty_library_matches_nothing_rather_than_erroring(tmp_path):
    assert motifs_stage.match(_motif_el(MotifKind.ICON, "anything"), []) == (None, 0.0)


# --- resolving a spec -----------------------------------------------------

def test_resolve_fills_in_what_it_can_and_reports_what_it_cannot(tmp_path):
    _write(tmp_path, "pumpkin-01.svg", TAGGED)
    spec = _spec(
        _motif_el(MotifKind.SEASONAL, "grinning carved jack-o-lantern"),
        _motif_el(MotifKind.SEASONAL, "a friendly floating ghost, sheet draped"),
    )
    unmatched = motifs_stage.resolve(spec, tmp_path)

    placed, hole = spec.motifs()
    assert placed.library_id == "pumpkin-01"
    assert placed.match_score and placed.match_score > 0.8
    assert hole.library_id is None
    assert unmatched == ["a friendly floating ghost, sheet draped"]
    assert spec.unresolved_motifs() == [hole]


def test_an_id_the_analyser_invented_is_thrown_away(tmp_path):
    """The model is shown this field in the schema, so it will fill it in. Only
    the matcher gets to say what is in our library."""
    _write(tmp_path, "pumpkin-01.svg", TAGGED)
    spec = _spec(_motif_el(MotifKind.SEASONAL, "grinning carved jack-o-lantern",
                           library_id="halloween-pumpkin-vector-free"))
    motifs_stage.resolve(spec, tmp_path)
    assert spec.motifs()[0].library_id == "pumpkin-01"


def test_an_id_that_is_already_in_the_library_is_left_alone(tmp_path):
    """Mixing borrows a donor's motif with the id the matcher gave it. Redoing
    that work would quietly undo the borrow."""
    _write(tmp_path, "pumpkin-01.svg", TAGGED)
    _write(tmp_path, "bat-01.svg", UNTAGGED)
    spec = _spec(_motif_el(MotifKind.SEASONAL, "grinning carved jack-o-lantern",
                           library_id="bat-01"))
    motifs_stage.resolve(spec, tmp_path)
    assert spec.motifs()[0].library_id == "bat-01"


# --- and out the other side, into the artwork -----------------------------

def test_a_resolved_motif_is_drawn_and_an_unresolved_one_is_reported(tmp_path):
    library, fonts = tmp_path / "motifs", tmp_path / "fonts"
    _write(library, "pumpkin-01.svg", TAGGED)
    spec = _spec(
        _motif_el(MotifKind.SEASONAL, "grinning carved jack-o-lantern"),
        _motif_el(MotifKind.SEASONAL, "a friendly floating ghost, sheet draped"),
    )
    motifs_stage.resolve(spec, library)
    result = render(spec, fonts, library)

    assert "<ellipse" in result.svg
    assert result.missing_motifs == ["a friendly floating ghost, sheet draped"]


def test_the_matcher_notes_never_reach_a_delivered_file():
    body = _motif_body(TAGGED)
    assert "<ellipse" in body
    assert not any(t in body for t in ("<title", "<desc", "<!--", "<svg"))


def test_a_library_id_cannot_walk_out_of_the_motif_folder(tmp_path):
    library, fonts = tmp_path / "motifs", tmp_path / "fonts"
    _write(library, "pumpkin-01.svg", TAGGED)
    _write(tmp_path, "elsewhere.svg", UNTAGGED)

    spec = _spec(_motif_el(MotifKind.SEASONAL, "anything at all",
                           library_id="../elsewhere"))
    result = render(spec, fonts, library)
    assert "<ellipse" not in result.svg
    assert result.missing_motifs == ["anything at all"]


# --- what to draw next ----------------------------------------------------

def _spec_with(*motifs, design_id: str) -> DesignSpec:
    spec = _spec(*[_motif_el(k, d) for k, d in motifs])
    spec.design_id = design_id
    return spec


def test_the_same_missing_thing_worded_differently_is_one_job(tmp_path):
    """A catalogue describes one pumpkin a dozen ways. Clustering on the
    subject words is what turns three thousand descriptions into forty
    drawings."""
    _write(tmp_path, "pumpkin-01.svg", TAGGED)
    specs = [
        _spec_with((MotifKind.SEASONAL, "a friendly floating ghost, sheet draped"),
                   design_id="d1"),
        _spec_with((MotifKind.SEASONAL, "a floating ghost under a draped sheet"),
                   design_id="d2"),
        _spec_with((MotifKind.SEASONAL, "small ghost, sheet draped over it"),
                   design_id="d3"),
        _spec_with((MotifKind.FRAME, "a cobweb spanning the upper corner"),
                   design_id="d4"),
    ]
    found = motifs_stage.gaps(specs, tmp_path)

    assert len(found) == 2, [g.description for g in found]
    ghost, web = found
    assert ghost.designs == 3
    assert ghost.seen == 3
    assert len(ghost.variants) == 2
    assert web.designs == 1
    assert ghost.designs > web.designs, "the work list is ranked by what it unblocks"


def test_a_motif_the_library_already_answers_is_not_a_gap(tmp_path):
    _write(tmp_path, "pumpkin-01.svg", TAGGED)
    specs = [_spec_with((MotifKind.SEASONAL, "a grinning carved jack-o-lantern"),
                        design_id="d1"),
             _spec_with((MotifKind.SEASONAL, "a friendly floating ghost"),
                        design_id="d2")]
    found = motifs_stage.gaps(specs, tmp_path)
    assert [g.designs for g in found] == [1]
    assert "ghost" in found[0].description


def test_a_gap_says_what_the_nearest_thing_you_have_is(tmp_path):
    """So you can tell 'draw this' from 'tag what you already have better'."""
    _write(tmp_path, "pumpkin-01.svg", TAGGED)
    found = motifs_stage.gaps(
        [_spec_with((MotifKind.SEASONAL, "a carved pumpkin, but stylised flat"),
                    design_id="d1")], tmp_path, threshold=0.99)
    assert found[0].nearest_id == "pumpkin-01"
    assert found[0].nearest_score > 0.5


def test_a_stub_is_written_where_the_matcher_cannot_reach_it(tmp_path):
    """An empty stub that could be matched and placed would be worse than the
    hole it stands for."""
    _write(tmp_path, "pumpkin-01.svg", TAGGED)
    gap = motifs_stage.gaps(
        [_spec_with((MotifKind.SEASONAL, "a friendly floating ghost, sheet draped"),
                    design_id="d1")], tmp_path)[0]

    stub = motifs_stage.scaffold(gap, tmp_path)
    assert stub.parent.name == motifs_stage.TODO_DIR
    assert 'data-kind="seasonal"' in stub.read_text()
    assert "ghost" in stub.read_text()

    assert [e.library_id for e in motifs_stage.load(tmp_path)] == ["pumpkin-01"]
