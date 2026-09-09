"""The two things that stop a Windows machine before it starts.

Inkscape's installer does not add itself to PATH unless you tick a box most
people miss, so "install Inkscape" was followed by "inkscape not on PATH" and
that was the whole first run. And fontconfig — how Inkscape and cairo find a
family by name — is Unix machinery with no Windows equivalent, so a font
sitting in assets/fonts was never findable under the name the matcher chose.
"""

import os
import sys
from pathlib import Path

import pytest

from stockforge.stages import export as export_stage


# --- finding Inkscape ------------------------------------------------------

def test_the_setting_wins_over_everything(tmp_path, monkeypatch):
    """So a copy anywhere at all can be pointed at."""
    fake = tmp_path / "inkscape.exe"
    fake.write_text("")
    monkeypatch.setenv("SF_INKSCAPE", str(fake))
    monkeypatch.setattr(export_stage.shutil, "which", lambda n: "/usr/bin/inkscape")
    assert export_stage._inkscape() == str(fake)


def test_a_setting_pointing_at_nothing_falls_back_rather_than_failing(monkeypatch):
    """A stale path in .env must not hide a working installation."""
    monkeypatch.setenv("SF_INKSCAPE", "/gone/inkscape.exe")
    monkeypatch.setattr(export_stage.shutil, "which",
                        lambda n: "/usr/bin/inkscape" if n == "inkscape" else None)
    assert export_stage._inkscape() == "/usr/bin/inkscape"


def test_path_is_used_when_there_is_no_setting(monkeypatch):
    monkeypatch.delenv("SF_INKSCAPE", raising=False)
    monkeypatch.setattr(export_stage.shutil, "which",
                        lambda n: "/usr/bin/inkscape" if n == "inkscape" else None)
    assert export_stage._inkscape() == "/usr/bin/inkscape"


def test_the_windows_install_folder_is_searched_when_path_is_empty(monkeypatch, tmp_path):
    """The actual first-run failure: installed correctly, invisible anyway."""
    installed = tmp_path / "Inkscape" / "bin" / "inkscape.exe"
    installed.parent.mkdir(parents=True)
    installed.write_text("")

    monkeypatch.delenv("SF_INKSCAPE", raising=False)
    monkeypatch.setattr(export_stage.shutil, "which", lambda n: None)
    monkeypatch.setattr(export_stage, "ON_WINDOWS", True)
    monkeypatch.setattr(export_stage, "_WINDOWS_GUESSES",
                        (r"C:\nope\inkscape.exe", str(installed)))
    assert export_stage._inkscape() == str(installed)


def test_nothing_installed_still_reports_nothing(monkeypatch):
    monkeypatch.delenv("SF_INKSCAPE", raising=False)
    monkeypatch.setattr(export_stage.shutil, "which", lambda n: None)
    monkeypatch.setattr(export_stage, "ON_WINDOWS", True)
    monkeypatch.setattr(export_stage, "_WINDOWS_GUESSES", (r"C:\nope\inkscape.exe",))
    assert export_stage._inkscape() is None


def test_the_setup_screen_looks_where_the_exporter_looks(monkeypatch, tmp_path):
    """Checking PATH while the exporter also searches the install folders would
    report a working Inkscape as missing, which is worse than not checking.

    Through report(), not through _check_binary — the wiring between the two is
    the thing that can come apart.
    """
    from stockforge.config import Settings
    from stockforge.health import report

    installed = tmp_path / "inkscape.exe"
    installed.write_text("")
    monkeypatch.setenv("SF_INKSCAPE", str(installed))
    monkeypatch.setattr(export_stage.shutil, "which", lambda n: None)

    cfg = Settings(root=tmp_path / "w", fonts_dir=tmp_path / "f",
                   motifs_dir=tmp_path / "m")
    check = next(c for c in report(cfg).checks if c.name == "Vector export")
    assert check.state == "ok", f"a working Inkscape was reported as {check.detail}"
    assert str(installed) in check.detail


def test_the_fix_text_says_how_to_install_it(tmp_path, monkeypatch):
    """"Install Inkscape" is not a fix when installing it does not help."""
    from stockforge.config import Settings
    from stockforge.health import report

    monkeypatch.delenv("SF_INKSCAPE", raising=False)
    monkeypatch.setattr(export_stage.shutil, "which", lambda n: None)
    monkeypatch.setattr(export_stage, "ON_WINDOWS", True)
    monkeypatch.setattr(export_stage, "_WINDOWS_GUESSES", ())

    cfg = Settings(root=tmp_path / "w", fonts_dir=tmp_path / "f",
                   motifs_dir=tmp_path / "m")
    vector = next(c for c in report(cfg).checks if c.name == "Vector export")
    assert vector.state == "fail"
    assert "winget" in vector.fix
    assert "SF_INKSCAPE" in vector.fix


# --- installing the fonts on Windows ---------------------------------------

def test_it_refuses_to_pretend_on_linux(tmp_path, monkeypatch):
    """Doing nothing while looking like it worked is how a catalogue gets set
    in the wrong face."""
    from stockforge.stages.fonts import install_for_windows
    monkeypatch.setattr("stockforge.stages.fonts.ON_WINDOWS", False)

    with pytest.raises(RuntimeError) as exc:
        install_for_windows(tmp_path)
    assert "Windows-only" in str(exc.value)


def test_a_font_is_copied_and_registered(tmp_path, monkeypatch):
    """Per-user, into LOCALAPPDATA with an HKCU entry, because that needs no
    administrator — which "right-click, Install for all users" does."""
    sys.modules.pop("winreg", None)
    import types

    written = {}

    class _Key:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    fake = types.ModuleType("winreg")
    fake.HKEY_CURRENT_USER = 1
    fake.REG_SZ = 1
    fake.CreateKey = lambda root, path: _Key()
    fake.SetValueEx = lambda key, name, r, t, value: written.__setitem__(name, value)
    monkeypatch.setitem(sys.modules, "winreg", fake)

    from conftest import build_font
    from stockforge.stages import fonts as fonts_stage

    library = tmp_path / "fonts"
    # Deliberately not named after the family: registering under the filename
    # leaves the font findable by a name nothing ever asks for.
    build_font(library / "download-01.ttf", family="Testface")
    (library / "notes.txt").write_text("not a font")

    local = tmp_path / "AppData"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(fonts_stage, "ON_WINDOWS", True)
    monkeypatch.setattr(fonts_stage, "_broadcast_font_change", lambda: None)
    monkeypatch.setattr(fonts_stage, "_register_font", lambda p: None)

    done = fonts_stage.install_for_windows(library)

    installed = local / "Microsoft" / "Windows" / "Fonts" / "download-01.ttf"
    assert installed.is_file(), "the file was never copied"
    assert [n for n, _ in done] == ["download-01.ttf"], "it tried to install a .txt"
    assert "Testface Regular (TrueType)" in written, (
        f"registered as {list(written)} — Windows lists a font by its own "
        f"family and style, not by what the file is called")
    assert str(installed) in written.values()


def test_the_registered_name_comes_from_the_font_not_the_filename(tmp_path):
    """Windows lists a font by its own family and style. Registering it under
    the filename leaves it findable by a name nothing asks for."""
    from conftest import build_font
    from stockforge.stages.fonts import _face_name

    path = tmp_path / "whatever-the-file-is-called.ttf"
    build_font(path, family="Testface")
    assert _face_name(path) == "Testface Regular"


def test_a_file_that_will_not_open_falls_back_to_its_name(tmp_path):
    from stockforge.stages.fonts import _face_name

    broken = tmp_path / "Broken.ttf"
    broken.write_bytes(b"not a font at all")
    assert _face_name(broken) == "Broken"
