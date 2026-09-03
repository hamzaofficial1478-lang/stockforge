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
