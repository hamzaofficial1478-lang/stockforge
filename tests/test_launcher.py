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

    def substituted_font(svg, png, width):
        import cv2
        import numpy as np
        image = np.full((200, width), 255, dtype=np.uint8)
        image[20:120, 10:width // 4] = 0
        cv2.imwrite(str(png), image)
        return png

    monkeypatch.setattr("stockforge.stages.export.svg_to_png", substituted_font)

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

def test_python_dash_m_stockforge_runs_the_program():
    """The `stockforge` command only exists after a pip install, so on a plain
    checkout `python -m stockforge` is the obvious thing to reach for. Without
    a __main__.py it fails with "'stockforge' is a package and cannot be
    directly executed", which says nothing about what to type instead — and
    that is exactly the wall someone hits following the README on Windows."""
    root = Path(__file__).resolve().parent.parent
    proc = subprocess.run(
        [sys.executable, "-m", "stockforge", "--help"],
        cwd=root, capture_output=True, text=True, timeout=120,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(root),
             "SF_ENV_FILE": "/nonexistent/.env"})
    assert proc.returncode == 0, proc.stderr
    assert "fonts" in proc.stdout and "motifs" in proc.stdout


def test_python_dash_m_passes_the_exit_code_back():
    """A launcher that always exits 0 makes every script that calls it think
    the run succeeded."""
    root = Path(__file__).resolve().parent.parent
    proc = subprocess.run(
        [sys.executable, "-m", "stockforge", "not-a-command"],
        cwd=root, capture_output=True, text=True, timeout=120,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(root),
             "SF_ENV_FILE": "/nonexistent/.env"})
    assert proc.returncode != 0


def test_the_launcher_is_valid_and_needs_nothing_installed():
    """It runs before the package exists, on a machine with a bare Python, so
    it may import nothing but the standard library."""
    source = (ROOT / "tools" / "launch.py").read_text()
    imports = {line.split()[1].split(".")[0]
               for line in source.splitlines()
               if line.startswith("import ") or line.startswith("from ")}
    assert imports <= {"__future__", "os", "shutil", "subprocess", "sys",
                       "pathlib"}, imports


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
    monkeypatch.setattr(launch, "venv_runs", lambda: True)
    monkeypatch.setattr(launch, "imports_this_copy", lambda: True)
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


# --- moving the folder ---------------------------------------------------

def test_a_python_that_is_there_but_does_not_run_is_not_healthy(monkeypatch, tmp_path):
    """What moving the folder leaves behind: the file exists, running it fails."""
    launch = _launcher()
    dead = tmp_path / "python"
    dead.write_text("")
    monkeypatch.setattr(launch, "PYTHON", dead)
    monkeypatch.setattr(launch.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 1))
    assert launch.venv_runs() is False


def test_a_missing_python_is_not_healthy(monkeypatch, tmp_path):
    launch = _launcher()
    monkeypatch.setattr(launch, "PYTHON", tmp_path / "nothing" / "python")
    assert launch.venv_runs() is False


def test_a_broken_environment_is_thrown_away_and_rebuilt(monkeypatch, tmp_path):
    """Rather than reported, because there is nothing in it worth keeping and
    the alternative is a program that will not start."""
    launch = _launcher()
    venv = tmp_path / ".venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text("")
    (venv / ".stockforge-installed").write_text("abc123")

    monkeypatch.setattr(launch, "VENV", venv)
    monkeypatch.setattr(launch, "PYTHON", venv / "bin" / "python")
    monkeypatch.setattr(launch, "STAMP", venv / ".stockforge-installed")
    monkeypatch.setattr(launch, "venv_runs", lambda: False)
    monkeypatch.setattr(launch, "imports_this_copy", lambda: True)
    monkeypatch.setattr(launch, "head", lambda: "abc123")

    calls = []

    def _record(cmd, *a, **k):
        calls.append(cmd)
        if "venv" in " ".join(map(str, cmd)):      # behave like a real build
            (venv / "bin").mkdir(parents=True, exist_ok=True)
            (venv / "bin" / "python").write_text("")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(launch.subprocess, "run", _record)
    launch.install_if_needed()

    assert any("venv" in " ".join(map(str, c)) for c in calls), "it did not rebuild"
    # The stamp said this exact commit was already installed. Going ahead
    # anyway is the point: the environment it was installed into is gone.
    assert any("pip" in " ".join(map(str, c)) for c in calls), \
        "the stale stamp short-circuited the reinstall into the new environment"


def test_a_healthy_environment_is_left_alone(monkeypatch, tmp_path):
    """The rebuild must not fire on every start."""
    launch = _launcher()
    venv = tmp_path / ".venv"
    venv.mkdir()
    monkeypatch.setattr(launch, "VENV", venv)
    monkeypatch.setattr(launch, "STAMP", venv / "stamp")
    monkeypatch.setattr(launch, "venv_runs", lambda: True)
    monkeypatch.setattr(launch, "imports_this_copy", lambda: True)
    monkeypatch.setattr(launch, "head", lambda: "abc123")
    (venv / "stamp").write_text("abc123")

    def _fail(*a, **k):
        raise AssertionError("it rebuilt a working environment")

    monkeypatch.setattr(launch.subprocess, "run", _fail)
    assert launch.install_if_needed() is True
    assert venv.exists()


def test_an_environment_pointing_at_the_old_folder_is_caught(monkeypatch, tmp_path):
    """The failure a move actually causes. An editable install records an
    absolute path, so the environment keeps importing the code from where the
    folder used to be — starting fine and ignoring every update pulled here."""
    launch = _launcher()
    monkeypatch.setattr(launch, "ROOT", tmp_path / "Desktop" / "stockforge")
    monkeypatch.setattr(
        launch.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(
            a, 0, stdout="/somewhere/else/stockforge/__init__.py"))
    assert launch.imports_this_copy() is False


def test_an_environment_pointing_here_is_accepted(monkeypatch, tmp_path):
    launch = _launcher()
    root = tmp_path / "Desktop" / "stockforge"
    (root / "stockforge").mkdir(parents=True)
    monkeypatch.setattr(launch, "ROOT", root)
    monkeypatch.setattr(
        launch.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(
            a, 0, stdout=str(root / "stockforge" / "__init__.py")))
    assert launch.imports_this_copy() is True


def test_a_matching_stamp_does_not_excuse_the_wrong_folder(monkeypatch, tmp_path):
    """Moving the folder does not change the commit, so the stamp still
    matches. Trusting it alone would skip the reinstall the move requires."""
    launch = _launcher()
    venv = tmp_path / ".venv"
    venv.mkdir()
    monkeypatch.setattr(launch, "VENV", venv)
    monkeypatch.setattr(launch, "STAMP", venv / "stamp")
    monkeypatch.setattr(launch, "venv_runs", lambda: True)
    monkeypatch.setattr(launch, "imports_this_copy", lambda: False)
    monkeypatch.setattr(launch, "head", lambda: "abc123")
    (venv / "stamp").write_text("abc123")

    calls = []
    monkeypatch.setattr(launch.subprocess, "run",
                        lambda cmd, *a, **k: (calls.append(cmd),
                                              subprocess.CompletedProcess(cmd, 0))[1])
    launch.install_if_needed()
    assert any("pip" in " ".join(map(str, c)) for c in calls), \
        "it trusted the stamp and left the environment pointing elsewhere"
