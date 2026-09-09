"""Regression checks for recovery, fonts, Windows setup, and output access."""

import base64
import hashlib
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pytest

from conftest import build_font
from test_core import _spec
from test_pipeline import ScriptedProvider, _listing, workspace
from test_ui import panel, get, post
from stockforge import providers
from stockforge.config import Settings
from stockforge.pipeline import Pipeline
from stockforge.schema import FontClass
from stockforge.sources import open_source
from stockforge.stages import fonts, font_download, ocr, derive
from stockforge.stages.render import render


def test_recovery_preserves_the_read_and_produces_editable_files(workspace, tmp_path):
    workspace.preserve_original = True
    provider = ScriptedProvider()
    providers.set_provider("vision", provider)
    providers.set_provider("reason", provider)
    _listing(tmp_path / "input", "original")
    steps = []
    pipe = Pipeline(workspace, on_progress=steps.append)
    assert pipe.pull(open_source("folder", str(tmp_path / "input"))) == 1
    did = pipe.store.designs()[0]["id"]
    assert pipe.build(did) == "master_only"
    assert any("vision model" in step for step in steps)
    assert any("OCR" in step for step in steps)
    assert any("Exporting editable PDF" in step for step in steps)
    assert pipe.store.get_spec(did) == pipe.store.get_read(did)
    assert not {"NewCopy", "Distinctiveness", "Critique"}.intersection(provider.seen)
    assert list((workspace.root / "out").rglob("*-master.pdf"))
    assert "<text" in next((workspace.root / "renders").glob("*.svg")).read_text(encoding="utf-8")


def test_zero_derivation_is_an_independent_unchanged_copy():
    original = _spec()
    result = derive.derive(original, strength=0)
    assert result == original and result is not original
    result.texts()[0].content = "edited"
    assert original.texts()[0].content != "edited"


def test_font_rescan_keeps_approval_and_tags(tmp_path):
    build_font(tmp_path / "nested/font.ttf")
    entries = fonts.scan(tmp_path)
    entries[0].embeddable = True
    entries[0].licence = "OFL-1.1"
    entries[0].category = "script"
    entries[0].mood = ["wedding"]
    fonts.write_manifest(tmp_path, entries)
    build_font(tmp_path / "new.ttf", family="New")
    scanned = fonts.scan(tmp_path)
    assert next(e for e in scanned if e.path == "nested/font.ttf") == entries[0]
    assert not next(e for e in scanned if e.path == "new.ttf").embeddable


def test_italic_selection_matches_the_rendered_style(tmp_path):
    build_font(tmp_path / "regular.ttf")
    build_font(tmp_path / "italic.ttf")
    regular = fonts.FontEntry("regular.ttf", "Testface", "Regular", category="serif", embeddable=True)
    italic = fonts.FontEntry("italic.ttf", "Testface", "Italic", category="serif", embeddable=True)
    fonts.write_manifest(tmp_path, [italic, regular])
    assert fonts.match(FontClass(category="serif", weight=400), [italic, regular])[0] == regular
    spec = _spec()
    spec.texts()[0].font.italic = True
    assert 'font-style="italic"' in render(spec, tmp_path, tmp_path).svg


def test_starter_download_checks_hashes_and_can_resume(tmp_path, monkeypatch):
    data = build_font(tmp_path / "source.ttf").read_bytes()
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps([{
        "path": "starter/font.ttf", "url": "https://example.invalid/font",
        "sha256": hashlib.sha256(data).hexdigest(), "font": {"category": "serif"}
    }]))
    monkeypatch.setattr(font_download, "CATALOG", catalog)
    monkeypatch.setattr(font_download, "get", lambda *a, **k: json.dumps({"content": base64.b64encode(data).decode()}).encode())
    library = tmp_path / "library"
    assert font_download.download_starter(library)[0].embeddable
    monkeypatch.setattr(font_download, "get", lambda *a, **k: pytest.fail("should use the verified local file"))
    assert font_download.download_starter(library)[0].licence == "OFL-1.1"
    (library / "starter/font.ttf").write_bytes(b"damaged")
    monkeypatch.setattr(font_download, "get", lambda *a, **k: b'{"content":"YmFk"}')
    with pytest.raises(ValueError, match="Checksum mismatch"):
        font_download.download_starter(library)


@pytest.mark.parametrize("name", ["models.json", "stockforge.db", ".env"])
def test_download_endpoint_never_serves_credentials(panel, name):
    base, cfg = panel
    path = cfg.root / name
    path.write_text("private")
    with pytest.raises(HTTPError) as exc:
        get(base, "/file?path=" + quote(str(path)))
    assert exc.value.code == 403


def test_switching_to_keyless_model_clears_previous_credentials(panel, monkeypatch):
    base, cfg = panel
    monkeypatch.setenv("SF_VISION_API_KEY", "previous-server-key")
    _, saved = post(base, "/api/models/save", {"model": "local", "base_url": "http://localhost:1234/v1"})
    post(base, "/api/models/activate", {"id": saved["model"]["id"]})
    import os
    assert os.environ["SF_VISION_API_KEY"] == ""


def test_cross_origin_cannot_change_settings(panel):
    base, _ = panel
    request = Request(base + "/api/config", data=b'{"SF_MIX":"1"}',
                      headers={"Origin": "https://example.invalid", "Content-Type": "application/json"})
    with pytest.raises(HTTPError) as exc:
        urlopen(request)
    assert exc.value.code == 403


def test_ocr_finds_an_explicit_windows_path(tmp_path, monkeypatch):
    executable = tmp_path / "Tesseract OCR/tesseract.exe"
    executable.parent.mkdir()
    executable.touch()
    monkeypatch.setenv("SF_TESSERACT", str(executable))
    monkeypatch.setattr(ocr.shutil, "which", lambda _: None)
    assert ocr.executable() == str(executable)


def test_recovery_is_the_default(monkeypatch):
    monkeypatch.delenv("SF_PRESERVE_ORIGINAL", raising=False)
    assert Settings().preserve_original


def test_palette_keeps_thin_ink_and_accent_colors(tmp_path):
    import cv2
    import numpy as np
    from stockforge.stages.analyse import dominant_colours
    img = np.full((1260, 900, 3), (239, 247, 250), dtype=np.uint8)
    cv2.putText(img, "AMELIA AND JONAH", (80, 440), cv2.FONT_HERSHEY_SIMPLEX,
                1.7, (66, 75, 40), 2, cv2.LINE_AA)
    cv2.line(img, (365, 550), (535, 550), (89, 154, 182), 3)
    path = tmp_path / "design.png"
    cv2.imwrite(str(path), img)
    colors = {c for c, _ in dominant_colours(path)}
    assert {"#faf7ef", "#284b42", "#b69a59"} <= colors


def test_model_test_rejects_empty_answers(monkeypatch):
    from stockforge.ui import models
    monkeypatch.setattr(models, "_request", lambda *a: {"choices": [{"message": {"content": None}}]})
    assert not models.test("http://localhost:1234/v1", "model")["ok"]


def test_page_names_cannot_escape_or_overwrite_other_pages(workspace, monkeypatch):
    from stockforge.stages.export import Exported
    spec = _spec(stock_safe=True)
    spec.pages[0].name = "../../cover:front"
    spec.pages[1] = spec.pages[0].model_copy(deep=True)
    exported = []
    def fake_export(svg, out_dir, stem, **kw):
        assert svg.parent == workspace.root / "renders"
        exported.append(stem)
        return Exported(master_pdf=out_dir / f"{stem}-master.pdf")
    monkeypatch.setattr("stockforge.stages.export.export_all", fake_export)
    pipe = Pipeline(workspace)
    pipe.store.add_design(id="safe-id", design_key="safe-id", state="pending")
    pipe._export(spec, "safe-id", 0, master_only=True)
    assert len(set(exported)) == 2
    assert all("/" not in stem and ":" not in stem for stem in exported)
