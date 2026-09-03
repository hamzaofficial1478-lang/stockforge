"""Font matching against our own library.

The analyser describes letterforms; it never names a font. This module turns
that description into a real file we are allowed to embed and outline.

That constraint is the point. Stock sites require you to hold redistribution
rights for every glyph in a submitted vector, and most fonts used on Etsy
designs — Canva's library especially — do not grant that. Matching to our own
OFL/purchased library is what keeps every output submittable.

Populate assets/fonts/ with families you own, run `stockforge fonts scan` to
bootstrap the manifest, then correct the tags by hand once. It is a one-off
afternoon that pays back across all 5,000 files.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path

from ..schema import FontClass

MANIFEST = "manifest.json"


@dataclass
class FontEntry:
    path: str
    family: str
    style: str
    category: str = "sans"
    weight: int = 400
    contrast: str = "medium"
    width: str = "normal"
    mood: list[str] | None = None
    licence: str = "unknown"          # OFL, purchased-extended, ...
    embeddable: bool = False          # set true only when you have checked

    def to_class(self) -> FontClass:
        return FontClass(
            category=self.category, weight=self.weight,
            contrast=self.contrast, width=self.width, mood=self.mood or [],
        )


# --------------------------------------------------------------------------

_CATEGORY_HINTS = {
    "script": ("script", "hand", "signature", "calligr", "brush"),
    "serif": ("serif", "garamond", "playfair", "libre", "lora", "crimson", "cormorant"),
    "slab": ("slab", "rockwell", "roboto slab"),
    "mono": ("mono", "code", "courier"),
    "display": ("display", "poster", "deco", "titling"),
    "blackletter": ("black letter", "blackletter", "gothic", "fraktur"),
}

_WEIGHT_HINTS = {
    100: ("thin", "hairline"), 200: ("extralight", "ultralight"), 300: ("light",),
    500: ("medium",), 600: ("semibold", "demibold"), 700: ("bold",),
    800: ("extrabold", "ultrabold"), 900: ("black", "heavy"),
}


def _guess(family: str, style: str) -> tuple[str, int, str]:
    blob = f"{family} {style}".lower()
    category = "sans"
    for cat, needles in _CATEGORY_HINTS.items():
        if any(n in blob for n in needles):
            category = cat
            break
    weight = 400
    for w, needles in _WEIGHT_HINTS.items():
        if any(n in blob for n in needles):
            weight = w
            break
    width = "condensed" if "condens" in blob or "narrow" in blob else (
        "extended" if "extend" in blob or "expand" in blob else "normal")
    return category, weight, width


def scan(fonts_dir: Path) -> list[FontEntry]:
    """Bootstrap a manifest from whatever is in the fonts folder.

    The guesses below are naive on purpose — they get you 80% of the way and
    you fix the rest by hand. Trying to infer stroke contrast from outlines is
    a rabbit hole that does not pay for itself.
    """
    from fontTools.ttLib import TTFont         # imported here so `scan` is optional

    entries: list[FontEntry] = []
    for path in sorted(fonts_dir.rglob("*")):
        if path.suffix.lower() not in {".ttf", ".otf"}:
            continue
        try:
            tt = TTFont(str(path), lazy=True, fontNumber=0)
            names = {r.nameID: r.toUnicode() for r in tt["name"].names if r.nameID in (1, 2)}
        except Exception:
            continue
        family, style = names.get(1, path.stem), names.get(2, "Regular")
        category, weight, width = _guess(family, style)
        entries.append(FontEntry(
            path=str(path.relative_to(fonts_dir)), family=family, style=style,
            category=category, weight=weight, width=width, mood=[],
        ))
    return entries


def write_manifest(fonts_dir: Path, entries: list[FontEntry]) -> Path:
    out = fonts_dir / MANIFEST
    out.write_text(json.dumps([asdict(e) for e in entries], indent=2))
    return out


def load_manifest(fonts_dir: Path) -> list[FontEntry]:
    path = fonts_dir / MANIFEST
    if not path.exists():
        return []
    return [FontEntry(**d) for d in json.loads(path.read_text())]


# --------------------------------------------------------------------------

_CATEGORY_NEIGHBOURS = {
    ("serif", "slab"): 0.6, ("slab", "serif"): 0.6,
    ("serif", "display"): 0.4, ("display", "serif"): 0.4,
    ("sans", "display"): 0.4, ("display", "sans"): 0.4,
    ("script", "blackletter"): 0.3, ("blackletter", "script"): 0.3,
}


def score(entry: FontEntry, want: FontClass) -> float:
    """0..1. Category dominates — a script where a serif was wanted ruins a
    design in a way that a slightly wrong weight never does."""
    if entry.category == want.category:
        cat = 1.0
    else:
        cat = _CATEGORY_NEIGHBOURS.get((entry.category, want.category), 0.0)

    weight = max(0.0, 1 - abs(entry.weight - want.weight) / 600)
    contrast = 1.0 if entry.contrast == want.contrast else 0.5
    width = 1.0 if entry.width == want.width else 0.4
    mood = 0.5
    if want.mood and entry.mood:
        overlap = len(set(m.lower() for m in want.mood) & set(m.lower() for m in entry.mood))
        mood = min(1.0, 0.5 + 0.25 * overlap)

    return 0.50 * cat + 0.18 * weight + 0.12 * contrast + 0.10 * width + 0.10 * mood


def match(want: FontClass, library: list[FontEntry], require_embeddable: bool = True) -> tuple[FontEntry | None, float]:
    pool = [e for e in library if e.embeddable] if require_embeddable else library
    if not pool:
        return None, 0.0
    best = max(pool, key=lambda e: score(e, want))
    return best, score(best, want)
