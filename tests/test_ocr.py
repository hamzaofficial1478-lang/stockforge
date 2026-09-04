"""Text ground truth.

OCR is one of the two things the README says takes work off the model — "a
model asked to transcribe an address gets it nearly right, and nearly right is
wrong on something someone prints". It had never once run: tesseract was not
installed anywhere this had been developed, and `read` returns an empty list on
any problem, so a broken OCR stage would have looked exactly like a missing one.
"""

import pytest

from stockforge.stages import ocr

HEADER = ("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
          "left\ttop\twidth\theight\tconf\ttext")


def _tsv(*words: tuple) -> str:
    """words: (block, par, line, left, top, w, h, conf, text)"""
    rows = [HEADER]
    for i, (block, par, line, left, top, w, h, conf, text) in enumerate(words):
        rows.append(f"5\t1\t{block}\t{par}\t{line}\t{i}\t{left}\t{top}\t{w}\t{h}"
                    f"\t{conf}\t{text}")
    return "\n".join(rows)


# --- turning tesseract's table into lines ---------------------------------

def test_words_on_one_line_become_one_line():
    tsv = _tsv((1, 1, 1, 100, 50, 60, 20, 96, "Amelia"),
               (1, 1, 1, 170, 50, 30, 20, 95, "and"),
               (1, 1, 1, 210, 50, 70, 20, 97, "Jonah"))
    lines = ocr.group(tsv, width=1000, height=1000)

    assert len(lines) == 1
    assert lines[0].text == "Amelia and Jonah"
    assert lines[0].x == pytest.approx(0.10)
    assert lines[0].y == pytest.approx(0.05)
    assert lines[0].w == pytest.approx(0.18)      # 100 to 280
    assert lines[0].confidence == pytest.approx(0.96, abs=0.01)


def test_words_on_different_lines_stay_apart():
    tsv = _tsv((1, 1, 1, 100, 50, 60, 20, 96, "top"),
               (1, 1, 2, 100, 200, 60, 20, 96, "bottom"))
    assert [l.text for l in ocr.group(tsv, 1000, 1000)] == ["top", "bottom"]


def test_lines_come_back_down_the_page():
    tsv = _tsv((1, 1, 1, 100, 800, 60, 20, 96, "last"),
               (1, 1, 2, 100, 100, 60, 20, 96, "first"))
    assert [l.text for l in ocr.group(tsv, 1000, 1000)] == ["first", "last"]


def test_a_word_it_barely_saw_is_left_out():
    tsv = _tsv((1, 1, 1, 100, 50, 60, 20, 96, "certain"),
               (1, 1, 2, 100, 90, 60, 20, 12, "guessed"))
    assert [l.text for l in ocr.group(tsv, 1000, 1000)] == ["certain"]


def test_rows_that_are_not_words_are_skipped():
    """Tesseract emits page, block and paragraph rows with empty text, and the
    occasional row with a conf of -1."""
    tsv = _tsv((1, 1, 1, 0, 0, 0, 0, -1, " "),
               (1, 1, 1, 100, 50, 60, 20, 96, "real"))
    assert [l.text for l in ocr.group(tsv, 1000, 1000)] == ["real"]


def test_a_table_with_nothing_in_it_is_no_lines():
    assert ocr.group(HEADER, 1000, 1000) == []


# --- what the model is told -----------------------------------------------

def test_the_model_is_told_how_much_to_trust_each_line():
    """The prompt tells it to trust OCR over its own reading. It measured the
    confidence, kept it, and never passed it on."""
    line = ocr.group(_tsv((1, 1, 1, 100, 50, 60, 20, 72, "Salm")), 1000, 1000)[0]
    assert "72% confidence" in line.as_prompt_line()
    assert "Salm" in line.as_prompt_line()


def test_no_engine_and_no_text_found_are_different_things(monkeypatch):
    monkeypatch.setattr(ocr, "available", lambda: False)
    assert "no OCR engine" in ocr.as_prompt([])

    monkeypatch.setattr(ocr, "available", lambda: True)
    assert "found no text at all" in ocr.as_prompt([])


# --- against the real thing -----------------------------------------------

ADDRESS = "5678 Haunted Hollow, Salem, TX 78555"

needs_tesseract = pytest.mark.skipif(not ocr.available(), reason="no tesseract here")


def _card(path, width=420, height=590, scale=0.30):
    """Small, the way a flat recovered from a staged photograph is small."""
    import cv2
    import numpy as np

    img = np.full((height, width, 3), 245, np.uint8)
    for i, text in enumerate(("TOGETHER WITH THEIR FAMILIES", ADDRESS)):
        cv2.putText(img, text, (int(width * 0.06), int(height * (0.30 + i * 0.25))),
                    cv2.FONT_HERSHEY_SIMPLEX, scale, (35, 35, 35), 1, cv2.LINE_AA)
    cv2.imwrite(str(path), img)
    return path


@needs_tesseract
def test_a_small_flat_is_still_read_exactly(tmp_path):
    """At its own size this file gives "THER FAMLES Haw, Sun". Tesseract wants
    something near 300 dpi and a 127mm card at 420 pixels is nowhere near it."""
    lines = ocr.read(_card(tmp_path / "card.png"))
    assert any(ADDRESS in l.text for l in lines), [l.text for l in lines]


@needs_tesseract
def test_the_boxes_are_normalised_to_the_original_not_the_upscaled_copy(tmp_path):
    lines = ocr.read(_card(tmp_path / "card.png"))
    assert lines
    for line in lines:
        assert 0.0 <= line.x <= 1.0 and 0.0 <= line.y <= 1.0
        assert 0.0 < line.w <= 1.0 and 0.0 < line.h <= 1.0
    # the first line sits at roughly 30% down, where it was drawn
    assert lines[0].y == pytest.approx(0.28, abs=0.06)


@needs_tesseract
def test_something_that_is_not_an_image_is_not_a_crash(tmp_path):
    bad = tmp_path / "card.png"
    bad.write_text("not a picture")
    assert ocr.read(bad) == []
