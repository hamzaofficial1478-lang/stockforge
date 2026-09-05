"""The setup check and the double-click launcher.

Both exist for the same reason: the first thing anyone does on a new machine is
find out whether it will run at all, and until now that meant opening a browser
to look at a screen that was red for reasons it could not fully verify.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from stockforge.cli import main
from stockforge.config import Settings
from stockforge.health import report

from conftest import build_font_library, build_motif_library

ROOT = Path(__file__).resolve().parent.parent


def _launcher():
    spec = importlib.util.spec_from_file_location("launch", ROOT / "tools" / "launch.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("FONTCONFIG_FILE", raising=False)
    build_font_library(tmp_path / "fonts")
    build_motif_library(tmp_path / "motifs")
    cfg = Settings(root=tmp_path / "work", fonts_dir=tmp_path / "fonts",
                   motifs_dir=tmp_path / "motifs")
    cfg.ensure_dirs()
    return cfg


# --- does the font we chose actually get drawn --------------------------

def test_a_font_that_resolves_is_reported_as_working(workspace):
    """Everything upstream can be right and this still be wrong: the renderer
    writes a family name and the system resolves it however it likes."""
    from stockforge.health import _check_font_rendering
    from stockforge.stages.fonts import activate

    activate(workspace.fonts_dir)
    check = _check_font_rendering(workspace)
    assert check.state == "ok", check.detail


def test_a_font_the_renderer_cannot_see_is_caught(workspace, monkeypatch):
    """Which is what Windows looks like when the fonts are not installed —
    the family goes into the SVG and something else gets drawn."""
    from stockforge.health import _check_font_rendering

    monkeypatch.delenv("FONTCONFIG_FILE", raising=False)
    check = _check_font_rendering(workspace)

    assert check.state == "fail"
    assert "something else was drawn" in check.detail
    assert "install the font files" in check.fix


def test_no_fonts_yet_is_not_a_failure(tmp_path):
    from stockforge.health import _check_font_rendering

    cfg = Settings(root=tmp_path / "w", fonts_dir=tmp_path / "none",
                   motifs_dir=tmp_path / "m")
    check = _check_font_rendering(cfg)
    assert check.state == "warn" and not check.required


# --- the setup check ------------------------------------------------------

def test_blocking_names_only_what_is_required(workspace):
    r = report(workspace)
    assert all(c.required for c in r.checks if c.name in r.blocking)
    assert r.workable == (not r.blocking)


def test_check_prints_the_fix_for_anything_not_ready(workspace, capsys, monkeypatch):
    monkeypatch.setattr("stockforge.cli.settings", workspace)
    monkeypatch.delenv("SF_VISION_MODEL", raising=False)

    code = main(["check"])
    out = capsys.readouterr().out

    assert code == 1, "a missing model server is not a ready machine"
    assert "Vision model" in out
    assert "SF_VISION_MODEL" in out, "it has to say what to do, not just what is wrong"
    assert "Not ready yet" in out


def test_check_says_so_when_everything_is_ready(workspace, capsys, monkeypatch):
    monkeypatch.setattr("stockforge.cli.settings", workspace)

    class _Green:
        checks = []
        blocking = []
        workable = True

    monkeypatch.setattr("stockforge.health.report", lambda cfg=None: _Green())
    assert main(["check"]) == 0
    assert "Ready to run" in capsys.readouterr().out


# --- the launcher ---------------------------------------------------------

def test_the_launcher_is_valid_and_needs_nothing_installed():
    """It runs before the package exists, on a machine with a bare Python, so
    it may import nothing but the standard library."""
    source = (ROOT / "tools" / "launch.py").read_text()
    imports = {line.split()[1].split(".")[0]
               for line in source.splitlines()
               if line.startswith("import ") or line.startswith("from ")}
    assert imports <= {"__future__", "os", "subprocess", "sys", "pathlib"}, imports


def test_a_pull_that_fails_does_not_stop_the_program(monkeypatch, capsys):
    """No network, no git, or local edits you want to keep are all reasons to
    carry on with the code already there."""
    launch = _launcher()
    monkeypatch.setattr(launch.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no git")))
    launch.update()
    assert "could not check for updates" in capsys.readouterr().out


def test_installing_is_skipped_when_the_code_has_not_moved(monkeypatch, tmp_path):
    launch = _launcher()
    monkeypatch.setattr(launch, "PYTHON", Path(sys.executable))
    monkeypatch.setattr(launch, "STAMP", tmp_path / "stamp")
    monkeypatch.setattr(launch, "head", lambda: "abc123")
    (tmp_path / "stamp").write_text("abc123")

    def _fail(*a, **k):
        raise AssertionError("it reinstalled when nothing had changed")

    monkeypatch.setattr(launch.subprocess, "run", _fail)
    assert launch.install_if_needed() is True


def test_the_batch_file_stays_thin(monkeypatch):
    """It cannot update itself safely while cmd.exe is reading it, so the logic
    lives in launch.py and this stays still."""
    bat = (ROOT / "run.bat").read_text()
    assert "tools\\launch.py" in bat
    assert "cd /d \"%~dp0\"" in bat, "it must work when double-clicked from anywhere"
    assert len(bat.splitlines()) < 45
