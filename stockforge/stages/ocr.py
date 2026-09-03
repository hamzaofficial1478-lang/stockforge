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
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("stockforge.ocr")


@dataclass
class Line:
    text: str
    x: float          # all normalised 0..1
    y: float
    w: float
    h: float
    confidence: float

    def as_prompt_line(self) -> str:
        return (f'"{self.text}" at x={self.x:.3f} y={self.y:.3f} '
                f'w={self.w:.3f} h={self.h:.3f}')


def available() -> bool:
    return shutil.which("tesseract") is not None


def read(path: Path, min_confidence: float = 45.0) -> list[Line]:
    """Group tesseract words into lines with normalised boxes."""
    if not available():
        return []

    import cv2

    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        return []
    ih, iw = img.shape[:2]

    try:
        proc = subprocess.run(
            ["tesseract", str(path), "stdout", "--psm", "11", "tsv"],
            capture_output=True, text=True, timeout=120,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.debug("ocr failed on %s: %s", path.name, exc)
        return []
    if proc.returncode != 0:
        return []

    rows = [r.split("\t") for r in proc.stdout.splitlines()[1:] if r.strip()]
    grouped: dict[tuple, list] = {}
    for r in rows:
        if len(r) < 12 or not r[11].strip():
            continue
        try:
            conf = float(r[10])
            left, top, width, height = (int(r[i]) for i in (6, 7, 8, 9))
        except ValueError:
            continue
        if conf < min_confidence:
            continue
        grouped.setdefault((r[2], r[3], r[4]), []).append(
            (left, top, width, height, conf, r[11])
        )

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
            x=x0 / iw, y=y0 / ih, w=(x1 - x0) / iw, h=(y1 - y0) / ih,
            confidence=sum(w[4] for w in words) / len(words) / 100,
        ))

    lines.sort(key=lambda l: (l.y, l.x))
    return lines


def as_prompt(lines: list[Line]) -> str:
    if not lines:
        return "(no OCR available — read the text from the image yourself, carefully)"
    return "\n".join(f"  {l.as_prompt_line()}" for l in lines)
