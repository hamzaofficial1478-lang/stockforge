"""Tests for mixing designs, the paced worker and the setup checks."""

from stockforge.schema import (
    Box, Canvas, ColourRole, DesignDNA, DesignSpec, FontClass, MotifElement,
    MotifKind, Page, Palette, Provenance, Swatch, TextElement, TypeRole,
)
from stockforge.stages.compose import compose, eligible_donors
from stockforge.worker import State, Worker


def _design(
    asset_id: str,
    occasion: str = "halloween",
    accent: str = "#e8622a",
    category: str = "serif",
    motif: str = "grinning carved pumpkin",
    stock_safe: bool | None = True,
) -> DesignSpec:
    return DesignSpec(
        source_asset_id=asset_id,
        dna=DesignDNA(
            category="greeting card", occasion=occasion, style_tags=["playful"],
            palette=Palette(swatches=[
                Swatch(role=ColourRole.BACKGROUND, hex="#ffffff", coverage=0.7),
                Swatch(role=ColourRole.ACCENT, hex=accent, coverage=0.3),
            ]),
            type_pairing=[FontClass(category=category, weight=700)],
            motif_vocabulary=[motif],
        ),
        pages=[Page(name="cover", canvas=Canvas(width_mm=127, height_mm=178), elements=[
            TextElement(
                role=TypeRole.TITLE, content="Happy Halloween",
                box=Box(x=0.1, y=0.2, w=0.8, h=0.15),
                font=FontClass(category="sans", weight=400), size_ratio=0.09,
            ),
            MotifElement(
                motif=MotifKind.SEASONAL, description=motif,
                box=Box(x=0.3, y=0.6, w=0.4, h=0.3), library_id="pumpkin-01",
            ),
        ])],
        provenance=Provenance(stock_safe=stock_safe),
        confidence=0.9,
    )


# --- donor selection ------------------------------------------------------

def test_donors_stay_within_the_same_family():
    target = _design("a", occasion="halloween")
    pool = [_design("b", occasion="halloween"), _design("c", occasion="wedding")]
    donors = eligible_donors(target, pool)
    assert [d.source_asset_id for d in donors] == ["b"]


def test_a_shared_listing_image_does_not_make_two_designs_one():
    """A shop puts the same size chart on every listing, and those are wide, so
    it can end up being the largest image of the design and therefore its asset
    id. Keyed on that, every design in the catalogue looked like the same one
    and nothing was eligible to lend anything — mixing quietly stopped."""
    target = _design("shared-banner")
    target.design_id = "design-one"
    other = _design("shared-banner", accent="#2a6ce8")
    other.design_id = "design-two"

    donors = eligible_donors(target, [other])
    assert [d.design_id for d in donors] == ["design-two"]


def test_a_design_is_still_never_its_own_donor_by_design_id():
    target = _design("a")
    target.design_id = "design-one"
    same = _design("b")
    same.design_id = "design-one"
    assert eligible_donors(target, [same]) == []


def test_target_is_never_its_own_donor():
    target = _design("a")
    assert eligible_donors(target, [target]) == []


# --- mixing ---------------------------------------------------------------

def test_mix_of_zero_returns_the_base_untouched():
    base = _design("a")
    out, recipe = compose(base, [_design("b", accent="#123456")], mix=0.0, seed=1)
    assert out.dna.palette.get(ColourRole.ACCENT) == "#e8622a"
    assert recipe.palette_from is None


def test_full_mix_borrows_ingredients_from_a_donor():
    base = _design("a", accent="#e8622a", category="serif")
    donor = _design("b", accent="#2a6ce8", category="script", motif="bat in flight")
    out, recipe = compose(base, [donor], mix=1.0, seed=7)

    assert out.dna.palette.get(ColourRole.ACCENT) == "#2a6ce8"
    assert recipe.palette_from == "b"
    assert "b" in recipe.summary()


def test_borrowed_motifs_keep_the_base_positions():
    """The composition still works because the boxes are the base's own."""
    base = _design("a", motif="grinning carved pumpkin")
    donor = _design("b", motif="bat in flight")
    donor.pages[0].elements[1].box = Box(x=0.01, y=0.01, w=0.1, h=0.1)
    donor.pages[0].elements[1].library_id = "bat-01"

    out, _ = compose(base, [donor], mix=1.0, seed=3)
    slot = out.motifs()[0]
    assert slot.description == "bat in flight"          # donor's content
    assert slot.library_id == "bat-01"
    assert (slot.box.x, slot.box.w) == (0.3, 0.4)       # base's position


def test_a_mix_inherits_the_most_cautious_provenance():
    base = _design("a", stock_safe=True)
    flagged = _design("b", stock_safe=False, accent="#2a6ce8")
    out, recipe = compose(base, [flagged], mix=1.0, seed=5)
    assert recipe.palette_from == "b"
    assert out.provenance.stock_safe is False
    assert out.publishable is False


def test_mixing_is_reproducible_for_a_given_seed():
    base, pool = _design("a"), [_design("b", accent="#111111"),
                                _design("c", accent="#222222")]
    first, _ = compose(base, pool, mix=0.5, seed=42)
    again, _ = compose(base, pool, mix=0.5, seed=42)
    assert first.dna.palette.get(ColourRole.ACCENT) == again.dna.palette.get(ColourRole.ACCENT)


# --- the worker -----------------------------------------------------------

def test_worker_starts_idle_and_takes_a_pace():
    w = Worker()
    assert w.progress.state is State.IDLE
    assert w.alive is False
    w.set_pace(9.5)
    assert w.pace_seconds == 9.5


def test_pace_is_clamped_to_something_sensible():
    w = Worker()
    w.set_pace(-5)
    assert w.pace_seconds == 0
    w.set_pace(9999)
    assert w.pace_seconds == 120


def test_progress_reports_a_rate_only_once_there_is_something_to_measure():
    w = Worker()
    assert w.progress.as_dict()["per_hour"] == 0
    assert w.progress.as_dict()["eta_s"] is None


# --- setup checks ---------------------------------------------------------

def test_health_report_names_what_is_blocking():
    from stockforge.health import report
    r = report()
    assert isinstance(r.workable, bool)
    names = {c.name for c in r.checks}
    assert {"Vision model", "Font library", "Vector export"} <= names
    # every failing required check must tell you how to fix it
    for c in r.checks:
        if c.state == "fail":
            assert c.fix, f"{c.name} fails with no fix explained"


# --- the two levers that were computed and thrown away --------------------

def _laid_out():
    """A page with type spread down it, so a change in rhythm has something
    to act on."""
    from stockforge.schema import (
        Background, Box, Canvas, ColourRole, DesignDNA, DesignSpec, FontClass,
        Grid, Page, Palette, Provenance, Swatch, TextElement, TypeRole)
    rows = [(TypeRole.EYEBROW, 0.20), (TypeRole.TITLE, 0.42), (TypeRole.BODY, 0.70)]
    return DesignSpec(
        source_asset_id="a", design_id="d", confidence=0.9,
        dna=DesignDNA(category="invitation", occasion="wedding",
                      grid=Grid(margin_x=0.08, margin_y=0.08,
                                symmetry="centred", vertical_rhythm="even"),
                      background=Background(treatment="solid"),
                      palette=Palette(swatches=[
                          Swatch(role=ColourRole.BACKGROUND, hex="#ffffff", coverage=0.8),
                          Swatch(role=ColourRole.INK, hex="#111111", coverage=0.2)])),
        pages=[Page(name="front", canvas=Canvas(width_mm=127, height_mm=178),
                    elements=[TextElement(
                        role=role, content="Amelia and Jonah",
                        box=Box(x=0.15, y=y, w=0.70, h=0.08),
                        font=FontClass(category="serif", weight=400),
                        size_ratio=0.04) for role, y in rows])],
        provenance=Provenance(built_with="unknown"))


def test_changing_the_rhythm_actually_moves_the_page(monkeypatch):
    """Grid.vertical_rhythm was rotated on every mix and read by nothing, so
    the lever that decides whether a piece reads airy or tight did nothing at
    all and two designs differing only in rhythm came out identical."""
    from stockforge.stages.derive import shift_layout

    import stockforge.stages.derive as derive_stage

    def spread_after_mixing(with_rhythm):
        spec = _laid_out()
        real = derive_stage._apply_rhythm
        if not with_rhythm:
            derive_stage._apply_rhythm = lambda *a, **k: None
        try:
            shift_layout(spec, strength=0.5)
        finally:
            derive_stage._apply_rhythm = real
        assert spec.dna.grid.vertical_rhythm == "airy"
        ys = [el.box.y + el.box.h / 2 for el in spec.elements()]
        return max(ys) - min(ys)

    # Mixing also opens the margins, and reflow moves every box when it does,
    # so "something moved" would pass with the rhythm still doing nothing —
    # which is the bug. Compare against the same mix with the rhythm disabled.
    assert spread_after_mixing(True) > spread_after_mixing(False), \
        "the rhythm was rotated to airy and the page did not open up"


def test_airy_pushes_apart_and_tight_pulls_together():
    """And in the right direction, about the middle of the sheet rather than
    sliding the whole piece down the page."""
    from stockforge.stages.derive import _apply_rhythm

    def spread(after):
        spec = _laid_out()
        _apply_rhythm(spec, "even", after)
        ys = [el.box.y + el.box.h / 2 for el in spec.elements()]
        return max(ys) - min(ys), sum(ys) / len(ys)

    even_spread, even_centre = spread("even")
    airy_spread, airy_centre = spread("airy")
    tight_spread, tight_centre = spread("tight")

    assert airy_spread > even_spread, "airy did not open the page up"
    assert tight_spread < even_spread, "tight did not close it in"
    for centre in (airy_centre, tight_centre):
        assert abs(centre - even_centre) < 0.02, "the piece slid down the page"


def test_the_grids_symmetry_reaches_the_type():
    """Grid.symmetry was declared in the schema, never varied and read by
    nothing. The renderer has honoured TextElement.align all along; the two
    were simply never connected."""
    from stockforge.stages.derive import _apply_symmetry

    spec = _laid_out()
    assert all(el.align == "center" for el in spec.texts())

    _apply_symmetry(spec, "left")
    assert all(el.align == "left" for el in spec.texts())
    assert all(abs(el.box.x - spec.dna.grid.margin_x) < 1e-9 for el in spec.texts()), \
        "it labelled the type left-aligned and left it sitting in the middle"

    _apply_symmetry(spec, "right")
    assert all(el.align == "right" for el in spec.texts())
    for el in spec.texts():
        assert abs((el.box.x + el.box.w) - (1 - spec.dna.grid.margin_x)) < 1e-9


def test_mixing_changes_the_symmetry_too(monkeypatch):
    from stockforge.stages.derive import shift_layout

    spec = _laid_out()
    shift_layout(spec, strength=0.5)
    assert spec.dna.grid.symmetry == "left"
    assert all(el.align == "left" for el in spec.texts())


def test_the_type_stays_on_the_page(monkeypatch):
    """Every one of these moves boxes, and a box off the sheet is a line the
    reader never sees."""
    from stockforge.stages.derive import shift_layout

    spec = _laid_out()
    for _ in range(6):
        shift_layout(spec, strength=1.0)
    for el in spec.elements():
        assert -0.2 <= el.box.x <= 1.2, el.box
        assert -0.2 <= el.box.y <= 1.2, el.box
