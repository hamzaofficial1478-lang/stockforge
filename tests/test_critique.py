"""Tests for the cheap half of the critique — the arithmetic that decides
whether the expensive half is worth running."""

from pathlib import Path

import cv2
import numpy as np
import pytest

from stockforge.providers.base import VisionProvider
from stockforge.schema import (
    Box, Canvas, ColourRole, DesignDNA, DesignSpec, FontClass, Page, Palette,
    Provenance, Swatch, TextElement, TypeRole,
)
from stockforge.stages import critique as critique_stage


class _NeverCalled(VisionProvider):
    """Spending a model call is the thing the gate exists to prevent, so the
    test asserts it by making one impossible."""

    name = "never-called"

    def chat(self, system, user_text, images, **kw):
        raise AssertionError("the critic was called on a render the gate should "
                             "have caught")


class _Stub(VisionProvider):
    name = "stub"

    def __init__(self):
        self.calls = 0

    def chat(self, system, user_text, images, **kw):
        self.calls += 1
        return ('{"similarity": 0.8, "polish": 0.7, "verdict": "patch", '
                '"patches": [], "commentary": "looked at it"}')


def _page(path: Path, w: int = 500, h: int = 700, marks: int = 6,
          shade: int = 30) -> Path:
    """A page with some ink on it. `marks=0` gives a blank one."""
    img = np.full((h, w, 3), 245, np.uint8)
    for i in range(marks):
        y = 80 + i * 90
        cv2.rectangle(img, (60 + i * 7, y), (w - 60, y + 40),
                      (shade, shade, shade), -1)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), img)
    return path


def _spec() -> DesignSpec:
    return DesignSpec(
        source_asset_id="abc",
        dna=DesignDNA(category="invitation", occasion="wedding",
                      palette=Palette(swatches=[
                          Swatch(role=ColourRole.INK, hex="#000000", coverage=1.0)])),
        pages=[Page(name="cover", canvas=Canvas(width_mm=127, height_mm=178), elements=[
            TextElement(role=TypeRole.TITLE, content="A",
                        box=Box(x=0.1, y=0.4, w=0.8, h=0.1),
                        font=FontClass(category="serif", weight=400),
                        size_ratio=0.05)])],
        provenance=Provenance(stock_safe=True), confidence=0.9)


# --- the raw measures -----------------------------------------------------

def test_an_image_is_identical_to_itself(tmp_path):
    page = _page(tmp_path / "a.png")
    assert critique_stage.ssim(page, page) == pytest.approx(1.0, abs=1e-6)
    assert critique_stage.palette_distance(page, page) == pytest.approx(0.0, abs=1e-3)


def test_a_different_page_scores_lower_than_an_identical_one(tmp_path):
    a = _page(tmp_path / "a.png", marks=6)
    b = _page(tmp_path / "b.png", marks=2, shade=200)
    assert critique_stage.ssim(a, b) < critique_stage.ssim(a, a)


# --- what the gate catches ------------------------------------------------

def test_a_blank_render_is_caught_before_a_model_is_asked_about_it(tmp_path):
    source = _page(tmp_path / "source.png")
    blank = _page(tmp_path / "blank.png", marks=0)

    numbers = critique_stage.signals(source, blank)
    assert not numbers.sane
    assert "blank" in numbers.fault
    assert numbers.detail < critique_stage.BLANK_DETAIL

    crit = critique_stage.critique(source, blank, _spec(), provider=_NeverCalled())
    assert crit.verdict == "escalate"
    assert crit.similarity == 0.0


def test_a_rebuild_of_the_wrong_shape_is_caught(tmp_path):
    source = _page(tmp_path / "source.png", w=500, h=700)
    square = _page(tmp_path / "square.png", w=700, h=700)

    numbers = critique_stage.signals(source, square)
    assert "wrong shape" in numbers.fault
    assert numbers.aspect_error > critique_stage.MAX_ASPECT_ERROR

    crit = critique_stage.critique(source, square, _spec(), provider=_NeverCalled())
    assert crit.verdict == "escalate"


def test_a_rebuild_that_is_actually_the_source_is_caught(tmp_path):
    """The rebuild is drawn from scratch with our own type and motifs, so it
    cannot land this close to the original by doing its job well."""
    source = _page(tmp_path / "source.png")
    numbers = critique_stage.signals(source, source)
    assert "indistinguishable" in numbers.fault
    assert numbers.ssim >= critique_stage.SAME_SSIM

    crit = critique_stage.critique(source, source, _spec(), provider=_NeverCalled())
    assert crit.verdict == "escalate"


def test_an_unreadable_image_is_a_fault_not_an_exception(tmp_path):
    source = _page(tmp_path / "source.png")
    numbers = critique_stage.signals(source, tmp_path / "does-not-exist.png")
    assert "could not read" in numbers.fault


# --- and what it lets through --------------------------------------------

def test_a_sane_pair_goes_on_to_the_critic(tmp_path):
    source = _page(tmp_path / "source.png", marks=6, shade=30)
    rebuild = _page(tmp_path / "rebuild.png", marks=4, shade=120)

    numbers = critique_stage.signals(source, rebuild)
    assert numbers.sane, numbers.summary()

    stub = _Stub()
    crit = critique_stage.critique(source, rebuild, _spec(), provider=stub)
    assert stub.calls == 1
    assert crit.verdict == "patch"
    assert crit.similarity == pytest.approx(0.8)
