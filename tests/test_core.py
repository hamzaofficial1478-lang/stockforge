"""Small, fast tests over the bits that do not need a model or an image."""

from stockforge.schema import (
    Box, Canvas, ColourRole, DesignDNA, DesignSpec, FontClass, Palette,
    Swatch, TextElement, TypeRole,
)
from stockforge.stages.cluster import Member, cluster, representative
from stockforge.stages.fonts import FontEntry, match, score
from stockforge.pricing import cost_usd


def _spec() -> DesignSpec:
    return DesignSpec(
        source_asset_id="abc",
        canvas=Canvas(width_mm=127, height_mm=178),
        dna=DesignDNA(
            category="invitation",
            occasion="wedding",
            palette=Palette(swatches=[
                Swatch(role=ColourRole.BACKGROUND, hex="#FAF6F0", coverage=0.8),
                Swatch(role=ColourRole.INK, hex="#2B2B28", coverage=0.1),
            ]),
        ),
        elements=[TextElement(
            role=TypeRole.TITLE, content="Ava & Noor",
            box=Box(x=0.1, y=0.35, w=0.8, h=0.15),
            font=FontClass(category="serif", weight=400, contrast="high"),
            size_ratio=0.09,
        )],
        confidence=0.8,
    )


def test_palette_roles_resolve():
    spec = _spec()
    assert spec.dna.palette.get(ColourRole.INK) == "#2b2b28"
    assert spec.dna.palette.get(ColourRole.ACCENT, "#ffffff") == "#ffffff"


def test_spec_roundtrips_through_json():
    spec = _spec()
    again = DesignSpec.model_validate(spec.model_dump(mode="json"))
    assert again.texts()[0].content == "Ava & Noor"


def test_clustering_groups_near_identical_and_splits_on_aspect():
    a = Member("a", "1" * 32 + "0" * 32, 0.714, 2000)
    b = Member("b", "1" * 30 + "01" + "0" * 32, 0.714, 1500)   # 2 bits apart
    c = Member("c", "1" * 32 + "0" * 32, 1.000, 2000)          # same hash, square
    groups = cluster([a, b, c], max_distance=12, aspect_tol=0.04)
    assert len(groups) == 2
    big = max(groups.values(), key=len)
    assert {m.asset_id for m in big} == {"a", "b"}
    assert representative(big).asset_id == "a"                 # widest wins


def test_font_matching_prefers_category_over_weight():
    want = FontClass(category="serif", weight=400, contrast="high")
    right = FontEntry(path="s.ttf", family="Serif", style="Bold",
                      category="serif", weight=700, embeddable=True)
    wrong = FontEntry(path="c.ttf", family="Script", style="Regular",
                      category="script", weight=400, embeddable=True)
    assert score(right, want) > score(wrong, want)
    picked, _ = match(want, [wrong, right])
    assert picked is right


def test_unlicensed_fonts_are_never_matched():
    want = FontClass(category="serif", weight=400)
    unchecked = FontEntry(path="x.ttf", family="X", style="R",
                          category="serif", embeddable=False)
    assert match(want, [unchecked]) == (None, 0.0)


def test_cost_accounting_counts_cached_reads_cheaply():
    dear = cost_usd("claude-opus-5", {"input_tokens": 10_000, "output_tokens": 0})
    cheap = cost_usd("claude-opus-5", {"input_tokens": 0, "cache_read_input_tokens": 10_000})
    assert dear > cheap * 5
