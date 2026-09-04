"""Stage 5 — look at what we built, next to what we were given, and fix it.

This loop is the difference between "roughly similar" and "actually shippable".
One analysis pass gets the structure right and the details wrong: type an eighth
too large, a motif sitting low, an accent colour a shade too cold. Nobody would
notice any one of those; together they read as amateur.

Two signals, deliberately:

  numbers — structural similarity and palette distance, cheap, run every round,
            and they catch gross failures (a blank render, a wrong aspect)
            without spending a token.
  eyes    — a vision call that sees both images and says what is off in words.
            Only runs if the numbers are in a sane band, so a broken render
            never costs model time.

Patches are narrow on purpose. The critic names an element and a field; it never
returns a fresh spec. A critic allowed to rewrite everything oscillates and
never converges.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from ..providers import VisionProvider, vision
from ..schema import Critique, DesignSpec

log = logging.getLogger("stockforge.critique")


# --------------------------------------------------------------------------
# cheap signals
# --------------------------------------------------------------------------

def _grey(path: Path, size: tuple[int, int]) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError(f"unreadable: {path}")
    return cv2.resize(img, size, interpolation=cv2.INTER_AREA).astype(np.float64)


def ssim(a: Path, b: Path, size: tuple[int, int] = (512, 512)) -> float:
    """Plain global SSIM on an 11px gaussian window. Good enough to tell a
    working render from a broken one; not sensitive enough to judge polish,
    which is what the vision pass is for."""
    x, y = _grey(a, size), _grey(b, size)
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    mu_x = cv2.GaussianBlur(x, (11, 11), 1.5)
    mu_y = cv2.GaussianBlur(y, (11, 11), 1.5)
    sx = cv2.GaussianBlur(x * x, (11, 11), 1.5) - mu_x ** 2
    sy = cv2.GaussianBlur(y * y, (11, 11), 1.5) - mu_y ** 2
    sxy = cv2.GaussianBlur(x * y, (11, 11), 1.5) - mu_x * mu_y
    num = (2 * mu_x * mu_y + c1) * (2 * sxy + c2)
    den = (mu_x ** 2 + mu_y ** 2 + c1) * (sx + sy + c2)
    return float(np.mean(num / den))


def palette_distance(a: Path, b: Path, k: int = 5) -> float:
    """Mean Lab distance between the dominant colours of each image, normalised
    to 0..1. Catches an accent that has drifted warm."""
    def dominant(path: Path) -> np.ndarray:
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        img = cv2.resize(img, (128, 128), interpolation=cv2.INTER_AREA)
        lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32)
        crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
        _, _, centres = cv2.kmeans(lab, k, None, crit, 3, cv2.KMEANS_PP_CENTERS)
        return centres[np.argsort(centres[:, 0])]

    da, db = dominant(a), dominant(b)
    return float(np.clip(np.mean(np.linalg.norm(da - db, axis=1)) / 128.0, 0, 1))


# --------------------------------------------------------------------------
# the gate
# --------------------------------------------------------------------------

# A page with fewer edge pixels than this has nothing drawn on it. Deliberately
# far below anything real — a single line of small type clears it easily — so
# it only ever fires on a render that genuinely failed.
BLANK_DETAIL = 0.001

# Two aspect ratios this far apart are not the same piece. A 5x7 and an A5 are
# 1% apart; a square and a 5x7 are 40%. Flattening a mockup is not exact, so
# the line sits well clear of any honest imprecision.
MAX_ASPECT_ERROR = 0.15

# Two images this alike are the same file, not a rebuild of one. Our rebuild is
# drawn from scratch with our own type and our own motifs, so it cannot come
# this close to a photograph of the original by doing its job well.
SAME_SSIM = 0.98
SAME_PALETTE = 0.03


@dataclass
class Signals:
    """The cheap half of the critique.

    Costs nothing, runs every round, and decides whether the expensive half is
    worth running at all. `fault` names what is wrong when something is; an
    empty one means go ahead and spend the model call.
    """

    detail: float = 0.0
    aspect_error: float = 0.0
    ssim: float = 0.0
    palette_distance: float = 0.0
    fault: str = ""

    @property
    def sane(self) -> bool:
        return not self.fault

    def summary(self) -> str:
        return (f"detail={self.detail:.3f} aspect_error={self.aspect_error:.2f} "
                f"ssim={self.ssim:.2f} palette={self.palette_distance:.2f}")


def _detail(grey: np.ndarray, size: tuple[int, int] = (512, 512)) -> float:
    """Fraction of the page that is an edge — is there anything drawn here?

    Edges rather than variance, because a page carrying nothing but a gradient
    background has plenty of variance and no content, and we want to catch that
    as the failure it is.
    """
    small = cv2.resize(grey, size, interpolation=cv2.INTER_AREA)
    edges = cv2.Canny(small, 50, 150)
    return float(np.count_nonzero(edges)) / edges.size


def signals(source: Path, rebuild: Path) -> Signals:
    """Look at both images with arithmetic before looking at them with a model.

    Ordered cheapest first and returns at the first fault, so a blank render
    costs one Canny pass rather than a model call and two minutes of GPU.
    """
    out = Signals()
    src = cv2.imread(str(source), cv2.IMREAD_GRAYSCALE)
    reb = cv2.imread(str(rebuild), cv2.IMREAD_GRAYSCALE)
    if src is None or reb is None:
        out.fault = f"could not read the {'source' if src is None else 'rebuild'} image"
        return out

    out.detail = _detail(reb)
    if out.detail < BLANK_DETAIL:
        out.fault = "the rebuild came out blank — nothing was drawn on the page"
        return out

    src_aspect = src.shape[1] / src.shape[0]
    reb_aspect = reb.shape[1] / reb.shape[0]
    out.aspect_error = abs(reb_aspect - src_aspect) / src_aspect
    if out.aspect_error > MAX_ASPECT_ERROR:
        out.fault = (f"the rebuild is the wrong shape — {reb_aspect:.2f} against the "
                     f"source's {src_aspect:.2f}, so the trim was misread")
        return out

    out.ssim = ssim(source, rebuild)
    out.palette_distance = palette_distance(source, rebuild)
    if out.ssim >= SAME_SSIM and out.palette_distance <= SAME_PALETTE:
        out.fault = ("the rebuild is indistinguishable from the source — it is the "
                     "same image, not a rebuild of it")
    return out


# --------------------------------------------------------------------------
# the vision pass
# --------------------------------------------------------------------------

SYSTEM = """You are art-directing a rebuild.

You will see two images. The FIRST is the source design. The SECOND is our \
rebuild of it, drawn from scratch in vector with our own type and our own \
decorative elements.

The rebuild is deliberately NOT a copy. Different fonts, redrawn motifs and \
cleaner geometry are correct and expected — never ask for them to be changed \
back toward the source. Judge two things instead:

  similarity — does the rebuild carry the same design intent? Same hierarchy, \
    same rhythm, same mood, same weight of colour. Would a buyer recognise it \
    as the same idea, executed again?
  polish — is the rebuild genuinely BETTER? Cleaner alignment, more confident \
    spacing, a type hierarchy that reads at a glance, margins that breathe. If \
    the source had a flaw, the rebuild should not have inherited it.

Then return patches. A patch is one field on one element. Be specific and be \
few — three good patches beat fifteen speculative ones. Common real faults, in \
rough order of how often they matter:

  - type set too large or too small (size_ratio)
  - a block sitting too high or low in its box (box.y)
  - tracking too tight or too loose on a display line (tracking)
  - an accent colour a shade off (dna.palette.swatches.N.hex)
  - a block sitting too near an edge, so the piece feels cramped (box.x, box.y)
  - a motif oversized relative to the type it sits beside (box.w, box.h)

Verdicts: `ship` when it is right, `patch` when the listed patches will make it \
right, `escalate` when something is wrong that a field edit cannot fix — a \
missing element, a misread layout, an effect we cannot draw. Escalating is not \
a failure; it routes the design to a human, which is exactly what should happen."""


def critique(
    source: Path,
    rebuild: Path,
    spec: DesignSpec,
    page_index: int = 0,
    provider: VisionProvider | None = None,
) -> Critique:
    numbers = signals(source, rebuild)
    log.info("critique signals: %s", numbers.summary())
    if numbers.fault:
        # Nothing a field patch can repair, so do not pay a model to say so.
        log.warning("skipping the critic — %s", numbers.fault)
        return Critique(similarity=0.0, polish=0.0, verdict="escalate",
                        commentary=numbers.fault)

    provider = provider or vision()

    elements = spec.pages[page_index].elements if spec.pages else []
    element_index = "\n".join(
        f"{i}: {getattr(el, 'kind', '?')} "
        f"{getattr(el, 'role', getattr(el, 'motif', getattr(el, 'primitive', '')))} "
        f"{repr(getattr(el, 'content', getattr(el, 'description', '')))[:60]}"
        for i, el in enumerate(elements)
    )

    return provider.structured(
        SYSTEM,
        "First image: the source. Second: our rebuild.\n\n"
        f"Elements on page {page_index}, by index:\n{element_index}\n\n"
        "Score it and return patches. Set page_index to "
        f"{page_index} on every patch that targets an element.",
        [source, rebuild],
        Critique,
    )


# --------------------------------------------------------------------------

def apply_patches(spec_dict: dict, crit: Critique) -> tuple[dict, list[str]]:
    """Apply patches to a spec dict in place. Returns the spec and a list of
    patches that could not be applied — those are a bug signal, not noise."""
    failed: list[str] = []

    for p in crit.patches:
        target = spec_dict
        try:
            if p.element_index is None:
                keys = p.path.split(".")
            else:
                target = spec_dict["pages"][p.page_index]["elements"][p.element_index]
                keys = p.path.split(".")
            for k in keys[:-1]:
                target = target[int(k)] if k.isdigit() else target[k]
            last = keys[-1]
            if last.isdigit():
                target[int(last)] = p.value
            else:
                if last not in target:
                    raise KeyError(last)
                target[last] = p.value
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            failed.append(f"{p.path}: {exc}")

    return spec_dict, failed
