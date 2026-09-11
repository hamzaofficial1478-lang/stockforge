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
import logging
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from ..constants import IMAGE_EXTS, VECTOR_EXTS  # noqa: F401  (re-exported)



log = logging.getLogger("stockforge.ingest")

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
    # What we decided about the trim, and how sure we were:
    #   cropped  found the card in a photo and straightened it
    #   flat     this image is the artwork already
    #   unsure   it looks like a photo of a card and we could not find the card
    #
    # The third one used to be silently reported as the second, which is how a
    # square photo of a 5x7 card became the thing every later stage measured
    # itself against. A doubt has to survive to somewhere a person will see it.
    trim: str = "flat"
    trim_confidence: float = 1.0
    note: str = ""


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

# How the card is looked for. Each route turns the photo into a mask that might
# have the card's outline in it; they are tried together because no single one
# survives a real listing photo.
#
# The original was one Canny at fixed thresholds, which works beautifully on a
# white card photographed on dark wood and fails completely on a white card
# photographed on a pale background with candy corn scattered over its edges —
# which is most of a seasonal Etsy shop. Worse, failing to find the card was
# read as "this must already be flat", so the whole photograph went downstream
# as though it were the artwork. The design was then analysed, laid out and
# judged against a square photo of a 5x7 card, and every stage after it was
# working from the wrong rectangle.

DETECT_EDGE = 900          # detect on a smaller copy: faster, and less noise


def _masks(img: np.ndarray) -> list[np.ndarray]:
    """Several views of the same photo, each of which might show the card."""
    grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    grey = cv2.bilateralFilter(grey, 7, 50, 50)
    out: list[np.ndarray] = []

    # Edges at three sensitivities, tuned off the image's own median rather
    # than fixed numbers, so a pale photo and a dark one both get useful ones.
    med = float(np.median(grey))
    for sigma in (0.20, 0.33, 0.55):
        lo = int(max(0, (1.0 - sigma) * med))
        hi = int(min(255, (1.0 + sigma) * med))
        out.append(cv2.Canny(grey, lo, hi))

    # The card as a bright block. A white card on a pale background has almost
    # no edge to find, but it is still the largest evenly-lit region.
    _, bright = cv2.threshold(grey, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    out.append(cv2.morphologyEx(bright, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)))

    # The same edges again, but with the local contrast pulled up first. A
    # white card on a pale backdrop can sit six or seven grey levels off its
    # background — invisible to a global threshold, and the single commonest
    # shape of listing photo in a seasonal shop. CLAHE works on small tiles, so
    # it amplifies that step without blowing out the rest of the picture.
    boosted = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(grey)
    bmed = float(np.median(boosted))
    for sigma in (0.33, 0.60):
        lo = int(max(0, (1.0 - sigma) * bmed))
        hi = int(min(255, (1.0 + sigma) * bmed))
        out.append(cv2.Canny(boosted, lo, hi))

    # The card as the *unsaturated* region: props and backdrops are usually
    # coloured, print stock is not.
    sat = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)[:, :, 1]
    _, plain = cv2.threshold(sat, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    out.append(cv2.morphologyEx(plain, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)))

    # Only the *step* edges, by keeping the strongest gradients and throwing
    # the rest away. This is what separates the card from its own drop shadow:
    # the card's edge is a step from backdrop to paper in one pixel, the shadow
    # is a soft ramp over fifty. Every other route here sees both and traces
    # the outside of the shadow, which comes back several percent too tall and
    # the wrong shape. Three cut-offs, because how much of the picture is edge
    # depends entirely on how much clutter was styled into it.
    smoothed = cv2.bilateralFilter(grey, 7, 50, 50)
    gx = cv2.Scharr(smoothed, cv2.CV_32F, 1, 0)
    gy = cv2.Scharr(smoothed, cv2.CV_32F, 0, 1)
    mag = cv2.magnitude(gx, gy)
    for keep in (0.02, 0.04, 0.08):
        cut = max(float(np.quantile(mag, 1.0 - keep)), 12.0)
        out.append(((mag >= cut) * 255).astype(np.uint8))

    # The one that finds a white card on a pale backdrop when there is no
    # usable edge at all. Two ideas, in order:
    #
    #   A heavy median wipes the props. Candy corn, spiders, sprigs and confetti
    #   are all small; the card is not. Blurring at a radius bigger than a prop
    #   removes them and leaves the card standing, which turns a scene cluttered
    #   with edges into two flat regions with one boundary between them.
    #
    #   Then a local threshold, which compares each pixel with its neighbourhood
    #   rather than with the whole picture. Six grey levels is invisible to
    #   anything global and perfectly clear against the local mean.
    #
    # The opening and closing afterwards drop anything prop-sized that survived
    # and fill the card back in solid.
    side = max(3, (min(img.shape[:2]) // 22) | 1)
    wiped = cv2.medianBlur(grey, min(side, 99) | 1)
    local = cv2.adaptiveThreshold(wiped, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                  cv2.THRESH_BINARY, max(3, (side * 5) | 1), -2)
    k = np.ones((max(3, side // 2), max(3, side // 2)), np.uint8)
    local = cv2.morphologyEx(local, cv2.MORPH_OPEN, k)
    out.append(cv2.morphologyEx(local, cv2.MORPH_CLOSE, k))

    return out


def _quads_in(mask: np.ndarray, min_area: float) -> list[np.ndarray]:
    """Every plausible rectangle in one mask.

    Closing first, with a kernel scaled to the image, bridges the gaps where a
    prop sits across the card's edge — the thing that broke the outline into
    pieces none of which was a quadrilateral.
    """
    h, w = mask.shape[:2]
    k = max(3, (min(h, w) // 60) | 1)
    closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    # RETR_LIST, not RETR_EXTERNAL: the card's outline is often nested inside
    # the photo's own border or a prop's, and EXTERNAL throws those away.
    contours, _ = cv2.findContours(closed, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    found: list[np.ndarray] = []
    for c in contours:
        if len(c) < 4:
            continue
        # Size is judged on the bounding rectangle, not the contour's own area.
        # An edge mask traces an outline as a thin loop out and back, whose
        # signed area is near zero however big the rectangle it encloses — so
        # filtering on contourArea here silently threw away every candidate the
        # edge routes produced, which is most of them.
        (_, _), (rw, rh), _ = cv2.minAreaRect(c)
        if rw * rh < min_area:
            continue
        approx = cv2.approxPolyDP(c, 0.02 * cv2.arcLength(c, True), True)
        if len(approx) == 4 and cv2.isContourConvex(approx):
            found.append(approx.reshape(4, 2).astype(np.float32))
        # A rotated bounding box as well: a card whose corners are rounded, or
        # whose edge is interrupted, never approximates to exactly four points
        # but is still a rectangle worth scoring.
        found.append(cv2.boxPoints(cv2.minAreaRect(c)).astype(np.float32))
    return found


def _score_quad(grey: np.ndarray, quad: np.ndarray) -> float:
    """How much this rectangle looks like a card lying in a photo, 0..1.

    Three things, and the middle one is what actually separates a card from a
    shadow or a prop: how rectangular it is, how different the artwork inside
    is from the surface outside, and whether it is a plausible size.
    """
    h, w = grey.shape[:2]
    area = cv2.contourArea(quad.reshape(-1, 1, 2))
    if area <= 0:
        return 0.0
    frac = area / float(h * w)
    # A card in a photo takes up a real share of the frame. Below a fifth it is
    # a prop, a shadow, or — the way this went wrong first — a coloured banner
    # inside a flat artwork that is not a separate object at all.
    if not 0.20 <= frac <= 0.97:
        return 0.0

    (_, _), (rw, rh), _ = cv2.minAreaRect(quad)
    if rw < 1 or rh < 1:
        return 0.0
    # Printed things are not slivers. Every common trim sits well above this:
    # 5x7 is 0.71, A4 0.71, 4x6 0.67, square 1.0. Measured against the flat
    # fixture that first went wrong, its banner was 0.37 and the real cards
    # 0.71 — so this is the line that tells an element inside a design from a
    # design lying on a table. A genuinely wide product (a banner) is the cost,
    # and it falls to the "could not find it" warning rather than being cropped
    # wrongly, which is the right way round.
    aspect = min(rw, rh) / max(rw, rh)
    if aspect < 0.45:
        return 0.0
    rect = 1.0 if rw * rh <= 0 else min(1.0, area / (rw * rh))

    filled = np.zeros((h, w), np.uint8)
    cv2.fillConvexPoly(filled, quad.astype(np.int32), 255)
    k = np.ones((max(3, min(h, w) // 50) | 1,) * 2, np.uint8)
    inside = cv2.erode(filled, k)
    outside = cv2.subtract(cv2.dilate(filled, k), filled)
    ring_in = cv2.subtract(filled, inside)
    if not ring_in.any() or not outside.any():
        return 0.0
    contrast = abs(float(grey[ring_in > 0].mean()) - float(grey[outside > 0].mean())) / 255.0

    # Something filling most of the frame is probably the photo's own border,
    # not a card in it; something tiny is a prop.
    size = 1.0 if 0.20 <= frac <= 0.85 else 0.45
    return 0.45 * rect + 0.35 * min(1.0, contrast * 4) + 0.20 * size


def find_card(img: np.ndarray, min_score: float = 0.55) -> tuple[np.ndarray | None, float]:
    """Find the artwork sitting in a mockup photo.

    Returns (corners in the original image's pixels, confidence). None means we
    could not find it — which is deliberately not the same answer as "there was
    nothing to find", and the caller has to tell the two apart.
    """
    h, w = img.shape[:2]
    scale = min(1.0, DETECT_EDGE / max(h, w))
    small = cv2.resize(img, (int(w * scale), int(h * scale)),
                       interpolation=cv2.INTER_AREA) if scale < 1.0 else img
    sh, sw = small.shape[:2]
    grey = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

    best, best_score = None, 0.0
    for mask in _masks(small):
        for quad in _quads_in(mask, 0.10 * sh * sw):
            score = _score_quad(grey, quad)
            if score > best_score:
                best, best_score = quad, score

    if best is None or best_score < min_score:
        return None, best_score
    return best / scale, best_score


def _largest_quad(img: np.ndarray, min_area_frac: float = 0.18) -> np.ndarray | None:
    """Kept for callers that only want the corners."""
    return find_card(img)[0]


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
    """Does this quad mean the image was already the artwork?"""
    if quad is None:
        return True
    h, w = img.shape[:2]
    # Four fifths, not the old 0.92. A rectangle filling most of the frame is
    # the artwork's own bounds inside a flat export, not a card lying on a
    # table — and cropping to it only shaves the margins off a file that was
    # already correct. Every real mockup measured here put the card at under a
    # third of the photo, so the gap between the two cases is wide.
    return cv2.contourArea(quad.reshape(-1, 1, 2)) > 0.80 * w * h


def _border_is_busy(img: np.ndarray, band: float = 0.10) -> bool:
    """Does the outside of this image look like a surface rather than a margin?

    This is what separates "this is the artwork already" from "this is a photo
    of the artwork and I failed to find it". A print export's outer edge is its
    own quiet margin. A listing photo's outer edge is a table, a cloth, a pile
    of props — busy, and busier than the middle.

    Only used when the card was not found, and deliberately quick to believe a
    photo: being wrong here costs a warning, and being wrong the other way is
    what sent a whole design through measured against the wrong rectangle.
    """
    h, w = img.shape[:2]
    grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    edges = cv2.Canny(cv2.GaussianBlur(grey, (5, 5), 0), 50, 150)

    by, bx = max(1, int(h * band)), max(1, int(w * band))
    ring = np.ones((h, w), bool)
    ring[by:h - by, bx:w - bx] = False
    middle = ~ring
    if not ring.any() or not middle.any():
        return False

    ring_density = float(edges[ring].mean())
    middle_density = float(edges[middle].mean())
    # Absolute busyness, not busyness relative to the middle. Measured across
    # real cases the two are miles apart on the first and indistinguishable on
    # the second: an export's margin scores 0.0 and every photograph 4.5 to
    # 5.0, while the ring-versus-middle ratio sits at 1.0 to 1.15 for photos
    # because the props are scattered over the card as well as around it. A
    # ratio test there rules out nothing.
    return ring_density > 1.5 or ring_density > middle_density * 2.0


def _same_shape_inset(img: np.ndarray, quad: np.ndarray,
                      tolerance: float = 0.06, least: float = 0.55) -> bool:
    """Is this rectangle just the image again, with the margins taken off?

    A flat export's artwork sits concentric inside its own white space, so what
    gets found is the same shape as the file, only smaller — and cropping to it
    throws away margin that is part of the design. A card lying on a table is
    not that: a 5x7 photographed on a square backdrop is a different shape from
    the photograph, which is exactly what tells the two apart without having to
    pick a lucky size threshold.
    """
    h, w = img.shape[:2]
    area = cv2.contourArea(quad.reshape(-1, 1, 2))
    if area < least * h * w:
        return False
    (_, _), (rw, rh), _ = cv2.minAreaRect(quad)
    if min(rw, rh) <= 0:
        return False
    found = min(rw, rh) / max(rw, rh)
    whole = min(w, h) / max(w, h)
    return abs(found - whole) <= tolerance * max(found, whole)


def _sits_on_something(img: np.ndarray, quad: np.ndarray, least: float = 3.0) -> bool:
    """Is there a surface around this rectangle, or is it more of the same paper?

    The question that separates a card lying on a table from a rectangle the
    detector found inside a flat export. Measured across the fixtures and the
    photographs: every flat file scores 0.0, because what is outside the
    rectangle is the same paper as inside it, and every genuine mockup scores
    50 or 160 — a grey backdrop, a wooden table. Only a white card on a white
    surface comes in low, at 6, and that one is unmistakably a photograph by
    the clutter in it, which is the other half of the test in read_trim.
    """
    h, w = img.shape[:2]
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    filled = np.zeros((h, w), np.uint8)
    cv2.fillConvexPoly(filled, quad.astype(np.int32), 255)
    inside, outside = filled > 0, filled == 0
    if not inside.any() or not outside.any():
        return False
    here = np.median(lab[inside].reshape(-1, 3), axis=0)
    there = np.median(lab[outside].reshape(-1, 3), axis=0)
    return float(np.linalg.norm(here - there)) > least


def read_trim(img: np.ndarray) -> tuple[np.ndarray | None, str, float, str]:
    """Decide what this image is. Returns (quad, state, confidence, note)."""
    quad, confidence = find_card(img)
    if quad is not None and (_looks_flat(img, quad) or _same_shape_inset(img, quad)):
        return None, "flat", confidence, ""
    # Found a rectangle, but a rectangle is not a card. Crop only when there is
    # something around it: either a surface of a different colour, or a scene
    # busy enough to be obviously a photograph. Without this the detector
    # happily crops a flat export down to some rectangle inside its own
    # artwork, which throws away margin that is part of the design.
    if quad is not None and not (_sits_on_something(img, quad) or _border_is_busy(img)):
        return None, "flat", confidence, ""
    if quad is not None:
        return quad, "cropped", confidence, ""
    if _border_is_busy(img):
        return None, "unsure", confidence, (
            "this looks like a photo of the design rather than the design "
            "itself, and the artwork's edges could not be found in it — so it "
            "has been read whole, surroundings and all. Crop it to the artwork "
            "and pull it again, or use the flat file if you have one."
        )
    return None, "flat", confidence, ""


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


def _read_any(src: Path) -> "cv2.typing.MatLike | None":
    """Open an image file, rasterising vector ones on the way.

    cv2 has no idea what an SVG is and returns None for one, which read as
    "unreadable" and lost the file. A shop's own exports are frequently vector,
    so they are drawn at a size worth analysing rather than refused.
    """
    if src.suffix.lower() in VECTOR_EXTS:
        import tempfile

        from .export import svg_to_png
        with tempfile.TemporaryDirectory() as tmp:
            png = Path(tmp) / "vector.png"
            try:
                svg_to_png(src, png, width=1600)
            except Exception as exc:
                raise ValueError(f"could not draw {src.name}: {exc}") from exc
            return cv2.imread(str(png), cv2.IMREAD_COLOR)
    return cv2.imread(str(src), cv2.IMREAD_COLOR)


def flatten_image(src: Path, out_dir: Path, max_edge: int = 2000) -> Flattened:
    img = _read_any(src)
    if img is None:
        raise ValueError(f"unreadable image: {src}")

    quad, trim, confidence, note = read_trim(img)
    is_mockup = trim != "flat"
    if quad is not None:
        img = _neutralise(_warp(img, quad))
    if note:
        log.warning("%s: %s", src.name, note)

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
        trim=trim,
        trim_confidence=round(confidence, 3),
        note=note,
    )


def walk(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.suffix.lower() in IMAGE_EXTS and p.is_file())
