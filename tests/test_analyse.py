"""Reading a design, and being able to see what was read.

The next phase of this project is judging the analysis and tuning the prompts
against real output. Two things stood in the way: the read was destroyed by the
build that followed it, and there was no way to look at one.
"""

from pathlib import Path

import pytest

from stockforge import providers
from stockforge.cli import main
from stockforge.config import Settings
from stockforge.pipeline import Pipeline
from stockforge.sources import open_source
from stockforge.stages.analyse import _snap, analyse

from conftest import build_font_library, build_motif_library
from test_pipeline import ScriptedProvider, _listing


# --- colours the model was told not to change -----------------------------

MEASURED = [("#faf6f0", 0.7), ("#2b2b28", 0.2), ("#7d8f6e", 0.1)]


def test_a_colour_that_was_measured_comes_back_untouched():
    assert _snap("#faf6f0", MEASURED) == ("#faf6f0", 0.7)
    assert _snap("#FAF6F0", MEASURED) == ("#faf6f0", 0.7)


def test_a_colour_the_model_drifted_is_pulled_back_to_one_that_exists():
    """The prompt says the hex values are measured and must not be changed. A
    local model changes them, and the coverage was looked up by exact string
    match — so a drifted hex landed with a coverage of zero and a colour that
    is not in the artwork."""
    hex_, coverage = _snap("#fbf5f1", MEASURED)
    assert (hex_, coverage) == ("#faf6f0", 0.7)


def test_a_colour_invented_outright_still_lands_on_something_real():
    hex_, coverage = _snap("#010203", MEASURED)
    assert hex_ == "#2b2b28"
    assert coverage == 0.2


def test_no_measurements_is_not_a_crash():
    assert _snap("#123456", []) == ("#123456", 0.0)


# --- reading through a photograph -----------------------------------------

class _Watching(ScriptedProvider):
    """Remembers which image each pass was handed, and has no opinion of its
    own about which are staged — this is about what ingest measured."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.given: dict[str, list[Path]] = {}

    def structured(self, system, user_text, images, model, **kw):
        self.given[model.__name__] = list(images)
        return super().structured(system, user_text, images, model, **kw)

    def _survey(self):
        survey = super()._survey()
        survey.mockup_indices = []
        return survey


def test_the_palette_is_measured_off_a_flat_not_a_photograph(tmp_path):
    """A colour sampled through tungsten light and a linen tablecloth is the
    wrong colour however carefully the roles are then assigned."""
    provider = _Watching()
    staged, flat = tmp_path / "staged.png", tmp_path / "flat.png"
    for p in (staged, flat):
        _listing(tmp_path, p.stem)
        p.write_bytes((tmp_path / f"{p.stem}-1.png").read_bytes())

    analyse([staged, flat], asset_id="a", provider=provider, mockups={0})
    assert provider.given["PaletteRead"] == [flat]


def test_a_surface_read_from_a_photograph_says_so(tmp_path):
    provider = ScriptedProvider()
    _listing(tmp_path, "one")
    image = tmp_path / "one-1.png"

    spec = analyse([image], asset_id="a", provider=provider, mockups={0})
    assert any("staged photograph" in w for w in spec.warnings)


def test_a_flat_surface_carries_no_such_warning(tmp_path):
    provider = ScriptedProvider()
    _listing(tmp_path, "one")
    spec = analyse([tmp_path / "one-1.png"], asset_id="a", provider=provider)
    assert not any("staged photograph" in w for w in spec.warnings)


# --- the read outliving the build -----------------------------------------

@pytest.fixture
def built(tmp_path, monkeypatch):
    monkeypatch.delenv("FONTCONFIG_FILE", raising=False)
    build_font_library(tmp_path / "fonts")
    build_motif_library(tmp_path / "motifs")
    cfg = Settings(root=tmp_path / "work", fonts_dir=tmp_path / "fonts", preserve_original=False,
                   motifs_dir=tmp_path / "motifs")
    provider = ScriptedProvider()
    providers.set_provider("vision", provider)
    providers.set_provider("reason", provider)

    _listing(tmp_path / "exports", "wedding-invite")
    pipe = Pipeline(cfg)
    pipe.pull(open_source("folder", str(tmp_path / "exports")))
    design_id = pipe.store.designs()[0]["id"]
    pipe.build(design_id)
    return cfg, pipe, design_id


def test_the_analyser_s_own_read_survives_the_build(built):
    """Everything after analysis replaces the spec, and the read is the thing
    the prompts are judged on."""
    _, pipe, design_id = built
    read = pipe.store.get_read(design_id)
    built_spec = pipe.store.get_spec(design_id)

    assert read is not None
    assert read != built_spec
    # the copy is rewritten by derivation; the read still has what was on the card
    titles = [t["content"] for t in read["pages"][0]["elements"] if t.get("kind") == "text"]
    assert "Amelia & Jonah" in titles


def test_the_read_carries_the_colours_that_were_measured(built):
    _, pipe, design_id = built
    swatches = pipe.store.get_read(design_id)["dna"]["palette"]["swatches"]
    assert swatches
    assert all(s["coverage"] > 0 for s in swatches), \
        "a coverage of zero means the hex never matched anything measured"


# --- looking at one -------------------------------------------------------

def test_the_spec_command_lists_what_there_is_to_read(built, capsys, monkeypatch):
    cfg, _, design_id = built
    monkeypatch.setattr("stockforge.cli.settings", cfg)
    assert main(["spec"]) == 0
    out = capsys.readouterr().out
    assert design_id[:16] in out
    assert "1 designs" in out


def test_the_spec_command_shows_what_the_analyser_understood(built, capsys, monkeypatch):
    cfg, _, design_id = built
    monkeypatch.setattr("stockforge.cli.settings", cfg)
    assert main(["spec", design_id[:8]]) == 0
    out = capsys.readouterr().out

    assert "[as read]" in out
    assert "wedding invitation" in out
    assert "Amelia & Jonah" in out          # the read, not the rewritten copy
    assert "wants serif" in out             # the letterform description
    assert "Testserif" in out               # and what it matched to
    assert "sprig-eucalyptus-01" in out


def test_the_built_spec_is_there_too_when_asked_for(built, capsys, monkeypatch):
    cfg, _, design_id = built
    monkeypatch.setattr("stockforge.cli.settings", cfg)
    assert main(["spec", design_id[:8], "--built"]) == 0
    out = capsys.readouterr().out
    assert "[as built]" in out
    assert "Rosa & Elliot" in out           # derivation rewrote the placeholder


def test_an_id_that_matches_nothing_says_so(built, capsys, monkeypatch):
    cfg, _, _ = built
    monkeypatch.setattr("stockforge.cli.settings", cfg)
    assert main(["spec", "zzzzzz"]) == 2
    assert "no design starts with" in capsys.readouterr().out
