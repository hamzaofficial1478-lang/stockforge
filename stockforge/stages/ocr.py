"""Text ground truth.

A vision model asked to transcribe "5678 HAUNTED HOLLOW / SALEM, TEXAS 78555"
will get it nearly right, and nearly right is wrong when the output is a file
someone prints. OCR gets the characters; the model judges the styling. Each
does the job it is actually good at.

Optional. With no OCR engine installed the analyser falls back to reading the
text itself and the pipeline carries on — a little less accurate, not broken.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("stockforge.ocr")

# Tesseract wants something near 300 dpi. A 127mm card at 500 pixels across is
# nearer 100, and at that size it reads "5678 Haunted Hollow, Salem" as "3678
# Haunted Hollow, Salm" — measured, on this project's own render. Doubled, the
# same file comes back exact and the mean confidence goes from 85 to 96. Flats
# recovered from a staged photograph are often this small, because they are
# only the part of the frame the artwork occupied.
MIN_EDGE = 1600
MAX_SCALE = 4


@dataclass
class Line:
    text: str
    x: float          # all normalised 0..1
    y: float
    w: float
    h: float
    confidence: float

    def as_prompt_line(self) -> str:
        # The confidence goes to the model too. It was measured and kept and
        # never passed on, while the prompt told the model to trust OCR over
        # its own reading — with no way to tell which lines deserved it.
        return (f'"{self.text}" at x={self.x:.3f} y={self.y:.3f} '
                f'w={self.w:.3f} h={self.h:.3f}, read with '
                f'{self.confidence:.0%} confidence')


ON_WINDOWS = os.name == "nt"


def executable() -> str | None:
    named = os.environ.get("SF_TESSERACT", "").strip()
    if named and Path(named).is_file():
        return named
    found = shutil.which(named or "tesseract")
    if found:
        return found
    if ON_WINDOWS:
        bundled = Path(__file__).resolve().parents[2] / "tools" / "tesseract" / "tesseract.exe"
        if bundled.is_file():
            return str(bundled)
        for root in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("LOCALAPPDATA", "")):
            for relative in ("Tesseract-OCR/tesseract.exe", "Programs/Tesseract-OCR/tesseract.exe"):
                candidate = Path(root) / relative
                if candidate.is_file():
                    return str(candidate)
    return None


def available() -> bool:
    return executable() is not None


@contextmanager
def _at_a_readable_size(path: Path):
    """Yield the image to hand tesseract, and the size it ends up.

    Small artwork is upscaled first. It is the cheapest accuracy there is, and
    the alternative is an address that is nearly right — which is wrong, on
    something somebody prints.
    """
    import cv2

    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        yield None, (0, 0)
        return

    ih, iw = img.shape[:2]
    scale = min(MAX_SCALE, max(1.0, MIN_EDGE / max(iw, ih)))
    if scale <= 1.0:
        yield path, (iw, ih)
        return

    with tempfile.TemporaryDirectory() as tmp:
        bigger = cv2.resize(img, None, fx=scale, fy=scale,
                            interpolation=cv2.INTER_CUBIC)
        target = Path(tmp) / f"{path.stem}-x{scale:.1f}.png"
        cv2.imwrite(str(target), bigger)
        log.debug("ocr: %s upscaled %.1fx to %dpx", path.name, scale, bigger.shape[1])
        yield target, (bigger.shape[1], bigger.shape[0])


def read(path: Path, min_confidence: float = 45.0) -> list[Line]:
    """Group tesseract words into lines with normalised boxes."""
    if not available():
        return []

    with _at_a_readable_size(path) as (target, (iw, ih)):
        if target is None or not iw or not ih:
            return []
        try:
            proc = subprocess.run(
                [executable(), str(target), "stdout", "--psm", "11", "tsv"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            log.debug("ocr failed on %s: %s", path.name, exc)
            return []
        if proc.returncode != 0:
            log.warning("tesseract gave up on %s: %s", path.name,
                        proc.stderr.strip()[:200])
            return []

        tsv = proc.stdout

    # Outside the block: the upscaled copy has served its purpose, and the
    # boxes are normalised against the size tesseract actually saw.
    return group(tsv, iw, ih, min_confidence)


def group(tsv: str, width: int, height: int, min_confidence: float = 45.0) -> list[Line]:
    """Turn tesseract's TSV into lines with boxes normalised 0..1.

    Words are gathered by tesseract's own paragraph, block and line numbers,
    which is what makes a line a line rather than a bag of words.
    """
    grouped: dict[tuple, list] = {}
    for row in (r.split("\t") for r in tsv.splitlines()[1:] if r.strip()):
        if len(row) < 12 or not row[11].strip():
            continue
        try:
            conf = float(row[10])
            left, top, w, h = (int(row[i]) for i in (6, 7, 8, 9))
        except ValueError:
            continue
        if conf < min_confidence:
            continue
        grouped.setdefault((row[2], row[3], row[4]), []).append(
            (left, top, w, h, conf, row[11]))

    lines: list[Line] = []
    for words in grouped.values():
        if not words:
            continue
        x0 = min(w[0] for w in words)
        y0 = min(w[1] for w in words)
        x1 = max(w[0] + w[2] for w in words)
        y1 = max(w[1] + w[3] for w in words)
        lines.append(Line(
            text=" ".join(w[5] for w in words),
            x=x0 / width, y=y0 / height,
            w=(x1 - x0) / width, h=(y1 - y0) / height,
            confidence=sum(w[4] for w in words) / len(words) / 100,
        ))

    lines.sort(key=lambda l: (l.y, l.x))
    return lines


def as_prompt(lines: list[Line]) -> str:
    """What the typography pass is told. Nothing found is not one situation but
    two, and the model should be told which it is."""
    if not lines:
        if not available():
            return ("(no OCR engine on this machine — read the text from the image "
                    "yourself, carefully)")
        return ("(OCR ran and found no text at all. Either this surface carries "
                "none, or the type is set in a way OCR cannot follow — read it "
                "yourself, carefully)")
    return "\n".join(f"  {l.as_prompt_line()}" for l in lines)
