"""Fast tests over the parts that need neither a model nor an image."""

import pytest

from stockforge.providers.base import ProviderError, extract_json
from stockforge.schema import (
    Box, Canvas, ColourRole, DesignDNA, DesignSpec, FontClass, Page, Palette,
    Provenance, Swatch, TextElement, TypeRole,
)
from stockforge.sources.base import group_by_stem
from stockforge.stages import derive as derive_stage
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


def test_widening_the_margins_actually_moves_the_page():
    """The renderer places elements from their own boxes and never looks at the
    grid, so a margin written into the DNA and nowhere else did nothing at all —
    shift_layout widened the margins every round and the page never moved."""
    from stockforge.schema import Grid
    from stockforge.stages.derive import reflow

    spec = _spec()
    box = spec.texts()[0].box
    before, after = Grid(margin_x=0.08, margin_y=0.08), Grid(margin_x=0.16, margin_y=0.16)
    was_x, was_w = box.x, box.w

    reflow(spec, before, after)
    assert box.x > was_x, "opening the margins should push the block inward"
    assert box.w < was_w, "and narrow what it spans"
    # the block keeps its place within the live area
    assert (box.x - 0.16) / (1 - 0.32) == pytest.approx((was_x - 0.08) / (1 - 0.16), abs=1e-6)


def test_narrowing_the_margins_moves_it_back_out():
    from stockforge.schema import Grid
    from stockforge.stages.derive import reflow

    spec = _spec()
    box = spec.texts()[0].box
    was_x = box.x
    reflow(spec, Grid(margin_x=0.16, margin_y=0.16), Grid(margin_x=0.04, margin_y=0.04))
    assert box.x < was_x


def test_the_layout_lever_changes_where_things_sit_not_only_the_dna():
    spec = _spec()
    before = (spec.texts()[0].box.x, spec.texts()[0].box.y)
    derive_stage.shift_layout(spec, strength=1.0)

    assert spec.dna.grid.margin_x > 0.08
    assert (spec.texts()[0].box.x, spec.texts()[0].box.y) != before


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


def test_a_replacement_too_long_for_its_box_is_refused():
    """A local model is told to keep line lengths close and will not always do
    it. A name half again as long as the one it replaces breaks the layout
    rather than balancing it."""
    from stockforge.providers.base import VisionProvider
    from stockforge.stages.derive import NewCopy, rewrite_placeholders

    class _Copy(VisionProvider):
        name = "copy"

        def __init__(self, *replacements):
            self.replacements = list(replacements)

        def chat(self, *a, **kw):
            raise AssertionError("structured is overridden")

        def structured(self, system, user_text, images, model, **kw):
            return NewCopy(replacements=self.replacements)

    spec = _spec()
    assert spec.texts()[0].content == "Chloe & Liam"

    rewrite_placeholders(spec, _Copy("Alexandra & Christopher-Fairweather"))
    assert spec.texts()[0].content == "Chloe & Liam", "the long one should be refused"

    rewrite_placeholders(spec, _Copy("Rosa & Elliot"))
    assert spec.texts()[0].content == "Rosa & Elliot"


def test_copy_that_the_model_did_not_return_leaves_the_original_alone():
    from stockforge.providers.base import VisionProvider
    from stockforge.stages.derive import NewCopy, rewrite_placeholders

    class _Empty(VisionProvider):
        name = "empty"

        def chat(self, *a, **kw):
            raise AssertionError("structured is overridden")

        def structured(self, system, user_text, images, model, **kw):
            return NewCopy(replacements=[])

    spec = _spec()
    rewrite_placeholders(spec, _Empty())
    assert spec.texts()[0].content == "Chloe & Liam"


def _stub_copy():
    from stockforge.providers.base import VisionProvider
    from stockforge.stages.derive import NewCopy

    class _Copy(VisionProvider):
        name = "copy"

        def chat(self, *a, **kw):
            raise AssertionError("structured is overridden")

        def structured(self, system, user_text, images, model, **kw):
            return NewCopy(replacements=[])

    return _Copy()


def test_two_designs_do_not_get_the_same_derivation():
    """The pipeline used to seed on the round number alone, so round one was
    seed one for every design in the catalogue: five thousand pieces sharing a
    single hue rotation, which is the opposite of what deriving is for."""
    from stockforge.pipeline import _seed
    from stockforge.stages.derive import derive

    accents = {
        derive(_spec(), strength=0.5, seed=_seed("derive", did, 1),
               provider=_stub_copy()).dna.palette.get(ColourRole.ACCENT)
        for did in ("a1b2c3", "d4e5f6", "9a8b7c", "112233")
    }
    assert len(accents) == 4


def test_the_same_design_derives_the_same_way_every_time():
    from stockforge.pipeline import _seed
    from stockforge.stages.derive import derive

    seed = _seed("derive", "a1b2c3", 1)
    first = derive(_spec(), strength=0.5, seed=seed, provider=_stub_copy())
    again = derive(_spec(), strength=0.5, seed=seed, provider=_stub_copy())
    assert (first.dna.palette.get(ColourRole.ACCENT)
            == again.dna.palette.get(ColourRole.ACCENT))


def test_a_seed_survives_a_restart():
    """Python salts str hashing per process. A recipe you want to re-run next
    week cannot be seeded on hash()."""
    import subprocess
    import sys

    code = ("import sys; sys.path.insert(0, '.'); "
            "from stockforge.pipeline import _seed; print(_seed('derive', 'a1b2c3', 1))")
    runs = {subprocess.run([sys.executable, "-c", code], capture_output=True,
                           text=True, env={"PYTHONHASHSEED": str(n)}).stdout.strip()
            for n in (1, 2, 3)}
    assert len(runs) == 1, f"the seed moved between runs: {runs}"


def test_deriving_does_not_disturb_anybody_else_s_randomness():
    import random

    from stockforge.stages.derive import derive

    random.seed(99)
    expected = [random.random() for _ in range(3)][1:]

    random.seed(99)
    random.random()
    derive(_spec(), strength=0.5, seed=1, provider=_stub_copy())
    assert [random.random() for _ in range(2)] == expected


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


# --- fonts ----------------------------------------------------------------

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


def test_every_setting_the_code_reads_is_written_down():
    """A setting readable by the code and documented nowhere is the same as a
    setting that does not exist: nobody can find it, and the Setup screen is
    built from the same list. Four were in that state."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    env = (root / ".env.example").read_text()
    named = set(re.findall(r"^#?\s*(SF_\w+)=", env, re.M))

    read: set[str] = set()
    for py in (root / "stockforge").rglob("*.py"):
        read |= set(re.findall(r'"(SF_[A-Z_]+)"', py.read_text()))

    # These two are prefixes handed to from_env(), not variables in their own
    # right; the settings they build are documented under their full names.
    read -= {"SF_VISION", "SF_REASON", "SF_IMAGE", "SF_FTP_"}

    assert read <= named, (
        f"{sorted(read - named)} can be set but appear nowhere in .env.example")


def test_reloading_settings_keeps_what_the_environment_does_not_set():
    """It used to copy every field off a fresh Settings, which threw away
    anything a caller had passed in. The panel is constructed with an explicit
    workspace and calls reload() every time you save a setting or make a model
    live — so saving anything moved it to ./workspace and the designs appeared
    to vanish."""
    import os
    import tempfile
    from pathlib import Path

    from stockforge.config import Settings

    tmp = Path(tempfile.mkdtemp())
    for key in ("SF_ROOT", "SF_FONTS", "SF_MOTIFS"):
        os.environ.pop(key, None)

    cfg = Settings(root=tmp / "work", fonts_dir=tmp / "fonts", motifs_dir=tmp / "motifs")
    cfg.reload()

    assert cfg.root == tmp / "work", "the workspace moved on its own"
    assert cfg.fonts_dir == tmp / "fonts"
    assert cfg.motifs_dir == tmp / "motifs"


def test_reloading_settings_still_picks_up_what_the_environment_does_set():
    """The other half. Keeping explicit values must not stop a saved setting
    from taking effect, which is the whole point of reload()."""
    import os
    import tempfile
    from pathlib import Path

    from stockforge.config import Settings

    tmp = Path(tempfile.mkdtemp())
    cfg = Settings(root=tmp / "work")
    try:
        os.environ["SF_MIX"] = "0.91"
        os.environ["SF_ROOT"] = str(tmp / "elsewhere")
        cfg.reload()
        assert cfg.mix == 0.91, "a saved slider did nothing"
        assert cfg.root == tmp / "elsewhere", "an explicit SF_ROOT was ignored"
    finally:
        os.environ.pop("SF_MIX", None)
        os.environ.pop("SF_ROOT", None)


def test_every_setting_field_knows_its_environment_variable():
    """reload() decides what to touch by that name. A field added without one
    is a field that silently stops following its setting."""
    from dataclasses import fields

    from stockforge.config import Settings

    for f in fields(Settings):
        assert f.metadata.get("env"), \
            f"Settings.{f.name} has no env name, so reload() will always overwrite it"
