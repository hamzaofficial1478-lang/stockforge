"""Fast tests over the parts that need neither a model nor an image."""

import pytest

from stockforge.providers.base import ProviderError, extract_json
from stockforge.schema import (
    Box, Canvas, ColourRole, DesignDNA, DesignSpec, FontClass, Page, Palette,
    Provenance, Swatch, TextElement, TypeRole,
)
from stockforge.sources.base import group_by_stem
from stockforge.stages import derive as derive_stage
from stockforge.stages.cluster import Member, cluster, representative
from stockforge.stages.fonts import FontEntry, match, score


def _spec(**prov) -> DesignSpec:
    return DesignSpec(
        source_asset_id="abc",
        dna=DesignDNA(
            category="greeting card", occasion="halloween",
            palette=Palette(swatches=[
                Swatch(role=ColourRole.BACKGROUND, hex="#FAF6F0", coverage=0.8),
                Swatch(role=ColourRole.INK, hex="#2B2B28", coverage=0.1),
                Swatch(role=ColourRole.ACCENT, hex="#E8622A", coverage=0.1),
            ]),
        ),
        pages=[
            Page(name="cover", canvas=Canvas(width_mm=127, height_mm=178), elements=[
                TextElement(
                    role=TypeRole.TITLE, content="Chloe & Liam",
                    box=Box(x=0.1, y=0.35, w=0.8, h=0.15),
                    font=FontClass(category="serif", weight=400, contrast="high"),
                    size_ratio=0.09, placeholder=True,
                )]),
            Page(name="inside", canvas=Canvas(width_mm=127, height_mm=178)),
        ],
        provenance=Provenance(**prov),
        confidence=0.8,
    )


# --- schema ---------------------------------------------------------------

def test_multi_page_designs_round_trip():
    again = DesignSpec.model_validate(_spec().model_dump(mode="json"))
    assert [p.name for p in again.pages] == ["cover", "inside"]
    assert again.texts()[0].content == "Chloe & Liam"
    assert again.texts(page=1) == []


def test_palette_roles_resolve():
    spec = _spec()
    assert spec.dna.palette.get(ColourRole.INK) == "#2b2b28"
    assert spec.dna.palette.get(ColourRole.METALLIC, "#ffffff") == "#ffffff"


# --- the publishing gate --------------------------------------------------

def test_design_is_not_publishable_until_provenance_clears_it():
    assert _spec().publishable is False                       # None -> held back
    assert _spec(stock_safe=False).publishable is False
    assert _spec(stock_safe=True).publishable is True


def test_third_party_content_blocks_publishing():
    spec = _spec(third_party_suspected=True, stock_safe=False,
                 reason="canva badge on the listing")
    assert spec.publishable is False


# --- derivation -----------------------------------------------------------

def test_palette_shift_moves_colours_but_keeps_their_relationship():
    spec = _spec()
    before = [s.hex for s in spec.dna.palette.swatches]
    gaps_before = _hue_gaps(spec)
    derive_stage.shift_palette(spec, hue_shift=0.15)
    after = [s.hex for s in spec.dna.palette.swatches]
    assert after != before
    assert _hue_gaps(spec) == pytest.approx(gaps_before, abs=0.02)


def _hue_gaps(spec):
    from stockforge.stages.derive import _hex_to_hls
    hues = [_hex_to_hls(s.hex)[0] for s in spec.dna.palette.swatches
            if _hex_to_hls(s.hex)[2] >= 0.08]
    return [round((b - a) % 1.0, 3) for a, b in zip(hues, hues[1:])]


def test_layout_shift_spreads_the_type_hierarchy():
    spec = _spec()
    spec.pages[0].elements.append(TextElement(
        role=TypeRole.DETAIL, content="2026",
        box=Box(x=0.1, y=0.8, w=0.8, h=0.05),
        font=FontClass(category="sans", weight=400), size_ratio=0.02,
    ))
    derive_stage.shift_layout(spec, strength=1.0)
    big, small = spec.texts()[0].size_ratio, spec.texts()[1].size_ratio
    assert big > 0.09 and small < 0.02


# --- providers ------------------------------------------------------------

@pytest.mark.parametrize("raw", [
    '{"a": 1}',
    'Here is the spec:\n```json\n{"a": 1}\n```\nHope that helps!',
    '```\n{"a": 1,}\n```',
    'Sure!\n{"a": 1}\nLet me know if you need changes.',
])
def test_json_survives_however_a_local_model_wraps_it(raw):
    assert extract_json(raw) == {"a": 1}


def test_unparseable_response_raises_rather_than_guessing():
    with pytest.raises(ProviderError):
        extract_json("I am afraid I cannot help with that.")


# --- sources --------------------------------------------------------------

def test_listing_images_group_into_one_design():
    from pathlib import Path
    paths = [Path(f"/l/haunted-invite-{i}.jpg") for i in range(1, 6)]
    paths.append(Path("/l/ghost-card-1.jpg"))
    groups = group_by_stem(paths)
    assert len(groups) == 2
    assert max(len(v) for v in groups.values()) == 5


# --- clustering and fonts -------------------------------------------------

def test_clustering_splits_on_aspect_even_when_hashes_match():
    a = Member("a", "1" * 32 + "0" * 32, 0.714, 2000)
    b = Member("b", "1" * 30 + "01" + "0" * 32, 0.714, 1500)
    c = Member("c", "1" * 32 + "0" * 32, 1.000, 2000)
    groups = cluster([a, b, c], max_distance=12, aspect_tol=0.04)
    assert len(groups) == 2
    assert representative(max(groups.values(), key=len)).asset_id == "a"


def test_font_matching_prefers_category_over_weight():
    want = FontClass(category="serif", weight=400, contrast="high")
    right = FontEntry(path="s.ttf", family="Serif", style="Bold",
                      category="serif", weight=700, embeddable=True)
    wrong = FontEntry(path="c.ttf", family="Script", style="Regular",
                      category="script", weight=400, embeddable=True)
    assert score(right, want) > score(wrong, want)
    assert match(want, [wrong, right])[0] is right


def test_unlicensed_fonts_are_never_matched():
    unchecked = FontEntry(path="x.ttf", family="X", style="R",
                          category="serif", embeddable=False)
    assert match(FontClass(category="serif", weight=400), [unchecked]) == (None, 0.0)
