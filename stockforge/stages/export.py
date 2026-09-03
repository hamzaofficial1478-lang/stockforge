"""Stage 6 — get the artwork out in the formats that matter.

Two audiences, and they want different things:

  You      — a layered PDF with LIVE text, so you can open it, change the names
             on a wedding invite and re-export. This is the file you lost, and
             the one this whole project exists to give back.

  Stock    — Adobe Stock and Shutterstock take vectors as EPS (and Adobe takes
             .ai); neither takes PDF as a vector submission. Text must be
             converted to outlines, and a JPEG preview goes alongside.

So we export twice from the same SVG: once keeping type live, once outlined.
Do not conflate them — shipping the live-text file to a stock site gets it
rejected for missing fonts, and outlining your master defeats the point.

Formats and requirements do change; check each site's current contributor
guidelines before a big upload rather than trusting these comments.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class ExportError(RuntimeError):
    pass


@dataclass
class Exported:
    master_pdf: Path | None = None      # live text, layered — yours
    outlined_svg: Path | None = None
    stock_eps: Path | None = None       # outlined — theirs
    preview_jpg: Path | None = None


def _inkscape() -> str | None:
    return shutil.which("inkscape")


def _run(args: list[str]) -> None:
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ExportError(f"{args[0]} failed: {proc.stderr.strip()[:400]}")


def svg_to_pdf(svg: Path, pdf: Path, outline_text: bool = False) -> Path:
    """Inkscape is the only reliable way to outline text on the command line,
    so it is the preferred path. cairosvg is the fallback, and it always
    outlines — it has no concept of live text — which is why a live-text master
    genuinely requires Inkscape."""
    pdf.parent.mkdir(parents=True, exist_ok=True)
    ink = _inkscape()

    if ink:
        args = [ink, str(svg), f"--export-filename={pdf}", "--export-type=pdf"]
        args.append("--export-text-to-path" if outline_text else "--export-pdf-version=1.5")
        _run(args)
        return pdf

    if not outline_text:
        raise ExportError(
            "Inkscape is not installed. A live-text PDF master needs it; "
            "cairosvg converts all type to paths."
        )
    import cairosvg
    cairosvg.svg2pdf(url=str(svg), write_to=str(pdf))
    return pdf


def svg_outline_text(svg: Path, out: Path) -> Path:
    ink = _inkscape()
    if not ink:
        raise ExportError("Inkscape is required to outline text for stock submission.")
    out.parent.mkdir(parents=True, exist_ok=True)
    _run([ink, str(svg), f"--export-filename={out}", "--export-type=svg", "--export-text-to-path"])
    return out


def svg_to_eps(svg: Path, eps: Path) -> Path:
    """EPS is what the stock sites actually ingest. Inkscape writes EPS at a
    level both accept; text is outlined first so no font is referenced."""
    ink = _inkscape()
    if not ink:
        raise ExportError("Inkscape is required for EPS export.")
    eps.parent.mkdir(parents=True, exist_ok=True)
    _run([ink, str(svg), f"--export-filename={eps}", "--export-type=eps", "--export-text-to-path"])
    return eps


def svg_to_png(svg: Path, png: Path, width: int = 1400) -> Path:
    png.parent.mkdir(parents=True, exist_ok=True)
    ink = _inkscape()
    if ink:
        _run([ink, str(svg), f"--export-filename={png}", "--export-type=png", f"--export-width={width}"])
        return png
    import cairosvg
    cairosvg.svg2png(url=str(svg), write_to=str(png), output_width=width)
    return png


def png_to_jpg(png: Path, jpg: Path, quality: int = 92) -> Path:
    import cv2
    img = cv2.imread(str(png), cv2.IMREAD_COLOR)
    if img is None:
        raise ExportError(f"unreadable png: {png}")
    jpg.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(jpg), img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    return jpg


def export_all(svg: Path, out_dir: Path, stem: str, preview_px: int = 1400) -> Exported:
    result = Exported()
    out_dir.mkdir(parents=True, exist_ok=True)

    # yours: live text, layered
    try:
        result.master_pdf = svg_to_pdf(svg, out_dir / f"{stem}-master.pdf", outline_text=False)
    except ExportError:
        result.master_pdf = None

    # theirs: outlined
    try:
        result.outlined_svg = svg_outline_text(svg, out_dir / f"{stem}-outlined.svg")
        result.stock_eps = svg_to_eps(svg, out_dir / f"{stem}.eps")
    except ExportError:
        pass

    png = svg_to_png(svg, out_dir / f"{stem}-preview.png", width=preview_px)
    result.preview_jpg = png_to_jpg(png, out_dir / f"{stem}-preview.jpg")
    return result
