"""Stage 1 — turn a folder of Etsy listing images into clean flat artwork.

Etsy uploads are a mess: some are true flat exports, plenty are mockups (a card
propped on a table, a banner on a wall, an invite in someone's hand). We cannot
read a design reliably through perspective, shadow and a linen tablecloth, so
the first job is to find the artwork rectangle and flatten it.

Nothing here calls a model. It is cheap, deterministic and runs over all 5,000
files in a few minutes.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from ..constants import IMAGE_EXTS  # noqa: F401  (re-exported)


@dataclass
class Flattened:
    asset_id: str
    src_path: Path
    flat_path: Path
    width: int
    height: int
    aspect: float
    phash: str
    is_mockup: bool


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def phash(img: np.ndarray, size: int = 32, keep: int = 8) -> str:
    """Perceptual hash via DCT. Used only for grouping near-identical designs,
    so 64 bits is plenty."""
    grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    small = cv2.resize(grey, (size, size), interpolation=cv2.INTER_AREA).astype(np.float32)
    dct = cv2.dct(small)[:keep, :keep]
    med = np.median(dct[1:].flatten())        # skip DC term, it dominates
    bits = (dct > med).flatten()
    return "".join("1" if b else "0" for b in bits)


# --------------------------------------------------------------------------
# mockup detection & flattening
# --------------------------------------------------------------------------

def _largest_quad(img: np.ndarray, min_area_frac: float = 0.18) -> np.ndarray | None:
    """Find the biggest convex four-sided contour — the artwork sitting in a
    mockup. Returns corner points, or None if nothing convincing is found."""
    h, w = img.shape[:2]
    grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    grey = cv2.bilateralFilter(grey, 9, 60, 60)
    edges = cv2.Canny(grey, 40, 130)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best, best_area = None, 0.0
    for c in contours:
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue
        area = cv2.contourArea(approx)
        if area < min_area_frac * w * h or area <= best_area:
            continue
        best, best_area = approx.reshape(4, 2).astype(np.float32), area
    return best


def _order_corners(pts: np.ndarray) -> np.ndarray:
    """tl, tr, br, bl"""
    s, d = pts.sum(axis=1), np.diff(pts, axis=1).ravel()
    return np.array(
        [pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]],
        dtype=np.float32,
    )


def _warp(img: np.ndarray, quad: np.ndarray) -> np.ndarray:
    tl, tr, br, bl = _order_corners(quad)
    w = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
    h = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
    dst = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float32)
    m = cv2.getPerspectiveTransform(_order_corners(quad), dst)
    return cv2.warpPerspective(img, m, (w, h), flags=cv2.INTER_CUBIC)


def _looks_flat(img: np.ndarray, quad: np.ndarray | None) -> bool:
    """A true flat export fills the frame and has square corners. If the quad we
    found is basically the whole image, it was already flat."""
    if quad is None:
        return True
    h, w = img.shape[:2]
    return cv2.contourArea(quad.reshape(-1, 1, 2)) > 0.92 * w * h


def _neutralise(img: np.ndarray) -> np.ndarray:
    """Grey-world white balance. Mockup photos are almost always warm from
    tungsten light, which would poison the palette we extract."""
    result = img.astype(np.float32)
    means = result.reshape(-1, 3).mean(axis=0)
    grey = means.mean()
    for c in range(3):
        if means[c] > 1:
            result[:, :, c] *= grey / means[c]
    return np.clip(result, 0, 255).astype(np.uint8)


def flatten_image(src: Path, out_dir: Path, max_edge: int = 2000) -> Flattened:
    img = cv2.imread(str(src), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"unreadable image: {src}")

    quad = _largest_quad(img)
    is_mockup = not _looks_flat(img, quad)
    if is_mockup and quad is not None:
        img = _neutralise(_warp(img, quad))

    h, w = img.shape[:2]
    if max(h, w) > max_edge:
        scale = max_edge / max(h, w)
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

    asset_id = sha256_file(src)
    out_dir.mkdir(parents=True, exist_ok=True)
    flat_path = out_dir / f"{asset_id[:16]}.png"
    cv2.imwrite(str(flat_path), img)

    h, w = img.shape[:2]
    return Flattened(
        asset_id=asset_id,
        src_path=src,
        flat_path=flat_path,
        width=w,
        height=h,
        aspect=round(w / h, 4),
        phash=phash(img),
        is_mockup=is_mockup,
    )


def walk(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.suffix.lower() in IMAGE_EXTS and p.is_file())
