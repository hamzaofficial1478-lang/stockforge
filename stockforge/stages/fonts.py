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
import logging
import os
import shutil
import hashlib
from xml.sax.saxutils import escape
from dataclasses import dataclass, asdict, field
from pathlib import Path

from ..schema import FontClass

log = logging.getLogger("stockforge.fonts")

MANIFEST = "manifest.json"
FONTCONFIG = "fontconfig.conf"


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
            italic=any(s in self.style.lower() for s in ("italic", "oblique")),
        )


# --------------------------------------------------------------------------

_CATEGORY_HINTS = {
    "script": ("script", "hand", "signature", "calligr", "brush"),
    "slab": ("slab", "rockwell", "roboto slab"),
    "serif": ("serif", "garamond", "playfair", "libre", "lora", "crimson", "cormorant"),
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
    for w, needles in sorted(_WEIGHT_HINTS.items(), key=lambda item: -max(map(len, item[1]))):
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

    previous = {e.path.replace("\\", "/"): e for e in load_manifest(fonts_dir)}
    entries: list[FontEntry] = []
    for path in sorted(fonts_dir.rglob("*")):
        if path.suffix.lower() not in {".ttf", ".otf"}:
            continue
        try:
            with TTFont(str(path), lazy=True, fontNumber=0) as tt:
                names = {r.nameID: r.toUnicode() for r in tt["name"].names if r.nameID in (1, 2)}
                actual_weight = int(tt["OS/2"].usWeightClass) if "OS/2" in tt else None
        except Exception:
            continue
        family, style = names.get(1, path.stem), names.get(2, "Regular")
        category, weight, width = _guess(family, style)
        relative = path.relative_to(fonts_dir).as_posix()
        if relative in previous:
            entries.append(previous[relative])
            continue
        entries.append(FontEntry(
            path=relative, family=family, style=style,
            category=category, weight=actual_weight or weight, width=width, mood=[],
        ))
    return entries


def write_manifest(fonts_dir: Path, entries: list[FontEntry]) -> Path:
    out = fonts_dir / MANIFEST
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([asdict(e) for e in entries], indent=2), encoding="utf-8")
    return out


def load_manifest(fonts_dir: Path) -> list[FontEntry]:
    path = fonts_dir / MANIFEST
    if not path.exists():
        return []
    return [FontEntry(**d) for d in json.loads(path.read_text(encoding="utf-8"))]


# --------------------------------------------------------------------------
# the real file behind an entry
# --------------------------------------------------------------------------

@dataclass
class Face:
    """An opened font, with the numbers the renderer needs to set type.

    Without this the renderer was guessing: cap height was assumed to be 0.70
    of the em for every face, and line widths were never measured at all, so
    any title longer than the canvas simply ran off the page.
    """

    entry: FontEntry
    path: Path
    units_per_em: int = 1000
    cap_ratio: float = 0.70
    widths: dict[int, int] = field(default_factory=dict, repr=False)
    fallback: int = 500

    def advance(self, ch: str) -> float:
        """One character's advance, in em."""
        return self.widths.get(ord(ch), self.fallback) / self.units_per_em

    def missing(self, text: str) -> list[str]:
        """The characters this face has no glyph for.

        A font without them does not fail — it draws the empty box every design
        app shows for a missing glyph, and `measure` quietly charges the
        fallback width for it, so the line measures as though it fitted
        perfectly. A page of boxes is indistinguishable from a page of type
        until somebody opens the file.

        Latin is not the interesting case. Accented names are: a display face
        with no e-acute in it turns Renée into Ren[]e on a wedding invitation.
        """
        return sorted({ch for ch in text
                       if not ch.isspace() and ord(ch) not in self.widths})

    def measure(self, text: str, size: float, tracking: float = 0.0) -> float:
        """How wide this line will actually be, in the same units as `size`.

        `tracking` is in em, matching the schema and what the renderer emits as
        letter-spacing: one gap between each pair of characters.
        """
        if not text:
            return 0.0
        return (sum(self.advance(c) for c in text) * size
                + tracking * size * max(0, len(text) - 1))


_faces: dict[tuple[str, float], Face | None] = {}


def open_face(entry: FontEntry, fonts_dir: Path) -> Face | None:
    """Open the file behind a manifest entry. None when it cannot be read.

    Cached, because 5,000 designs reuse the same dozen faces and parsing a font
    for every line of text would be the slowest thing in the pipeline.
    """
    path = fonts_dir / entry.path
    try:
        key = (str(path), path.stat().st_mtime)
    except OSError:
        log.warning("%s is in the manifest but not on disk", entry.path)
        return None
    if key in _faces:
        return _faces[key]

    face: Face | None = None
    tt = None
    try:
        from fontTools.ttLib import TTFont

        tt = TTFont(str(path), lazy=True, fontNumber=0)
        upem = int(tt["head"].unitsPerEm) or 1000
        metrics = tt["hmtx"].metrics
        widths = {cp: metrics[name][0]
                  for cp, name in tt.getBestCmap().items() if name in metrics}

        # Real cap height where the font declares a sane one; the old 0.70
        # guess otherwise. It decides how big the type is, so it is worth
        # taking from the file rather than assuming.
        cap = int(getattr(tt["OS/2"], "sCapHeight", 0) or 0) if "OS/2" in tt else 0
        ratio = cap / upem if cap else 0.0
        if not 0.4 < ratio < 1.0:
            ratio = 0.70

        face = Face(
            entry=entry, path=path, units_per_em=upem, cap_ratio=ratio, widths=widths,
            fallback=widths.get(ord("n")) or (sum(widths.values()) // len(widths)
                                              if widths else upem // 2),
        )
    except Exception as exc:
        log.warning("could not read %s: %s", path.name, exc)
    finally:
        if tt is not None:
            tt.close()

    _faces[key] = face
    return face


# --------------------------------------------------------------------------
# making the family name resolve to our own file
# --------------------------------------------------------------------------

_FONTCONFIG_XML = """<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
<!-- Written by stockforge. `stockforge fonts scan` overwrites it. -->
<fontconfig>
  <dir>{fonts}</dir>
  <cachedir>{cache}</cachedir>
{system}
</fontconfig>
"""

# Where the system keeps its own config, so ordinary fonts still resolve.
_SYSTEM_CONFIGS = (
    "/etc/fonts/fonts.conf",
    "/usr/local/etc/fonts/fonts.conf",
    "/opt/homebrew/etc/fonts/fonts.conf",
)


def write_fontconfig(fonts_dir: Path) -> Path:
    system = "\n".join(f'  <include ignore_missing="yes">{p}</include>'
                       for p in _SYSTEM_CONFIGS)
    body = _FONTCONFIG_XML.format(fonts=escape(str(fonts_dir.resolve())),
                                 cache=escape(str(fonts_dir.resolve() / ".cache")), system=system)
    path = fonts_dir / FONTCONFIG
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_text(encoding="utf-8") != body:
        path.write_text(body, encoding="utf-8")
    return path


def activate(fonts_dir: Path) -> Path | None:
    """Make our own font folder visible to Inkscape and cairo.

    Neither takes a font file. Both look a family name up through fontconfig,
    which is why matching a face in our library was until now only a
    suggestion: the name went into the SVG and whatever the system happened to
    have got drawn instead. Pointing FONTCONFIG_FILE at a config that includes
    our folder closes that, and it installs nothing into the system.

    Returns None when there is nothing to do — no fonts yet, or the owner has
    set FONTCONFIG_FILE themselves, in which case it is not ours to overwrite.
    """
    if os.environ.get("FONTCONFIG_FILE"):
        return None
    try:
        if not any(p.suffix.lower() in {".ttf", ".otf"} for p in fonts_dir.rglob("*")):
            return None
    except OSError:
        return None
    try:
        path = write_fontconfig(fonts_dir)
    except OSError as exc:
        # Runs on every pipeline start, so a read-only font folder must not be
        # the thing that stops a batch. Type will fall back to system fonts.
        log.warning("could not write the fontconfig in %s: %s", fonts_dir, exc)
        return None
    os.environ["FONTCONFIG_FILE"] = str(path.resolve())
    log.debug("fontconfig: %s", path)
    return path


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

    style_penalty = 0.15 if entry.to_class().italic != want.italic else 0.0
    return max(0.0, 0.50 * cat + 0.18 * weight + 0.12 * contrast + 0.10 * width + 0.10 * mood - style_penalty)


def match(want: FontClass, library: list[FontEntry], require_embeddable: bool = True) -> tuple[FontEntry | None, float]:
    pool = [e for e in library if e.embeddable] if require_embeddable else library
    if not pool:
        return None, 0.0
    best = max(pool, key=lambda e: score(e, want))
    return best, score(best, want)


# --------------------------------------------------------------------------
# making the family resolve on Windows
# --------------------------------------------------------------------------

ON_WINDOWS = os.name == "nt"


def _face_name(path: Path) -> str:
    """The name Windows lists a font under, from the file itself."""
    try:
        from fontTools.ttLib import TTFont

        tt = TTFont(str(path), lazy=True, fontNumber=0)
        family = style = ""
        for record in tt["name"].names:
            if record.nameID == 1 and not family:
                family = str(record)
            elif record.nameID == 2 and not style:
                style = str(record)
        if family:
            return f"{family} {style}".strip()
    except Exception:
        pass
    return path.stem


def install_for_windows(fonts_dir: Path) -> list[tuple[str, str]]:
    """Install the library's fonts for the current user.

    fontconfig is how Inkscape and cairo find a family by name, and Windows has
    no fontconfig — so `fonts scan` writing a config there achieves nothing and
    the family named in the SVG resolves to whatever the machine happens to
    have. The files have to be installed.

    Per-user, into LOCALAPPDATA, with a registry entry under HKCU: that needs
    no administrator, which "right-click, Install for all users" does.

    Returns (font file name, what happened) for every file, including the ones
    already there. Raises on any platform but Windows, because it would be
    doing nothing while looking like it worked.
    """
    if not ON_WINDOWS:
        raise RuntimeError(
            "this is a Windows-only step — on Linux and macOS the generated "
            "fontconfig.conf already makes the folder visible")

    import winreg                                    # noqa: PLC0415  (Windows only)

    target = Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "Windows" / "Fonts"
    target.mkdir(parents=True, exist_ok=True)
    key_path = r"Software\Microsoft\Windows NT\CurrentVersion\Fonts"

    done: list[tuple[str, str]] = []
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
        for file in sorted(fonts_dir.rglob("*")):
            if file.suffix.lower() not in {".ttf", ".otf", ".ttc"}:
                continue
            # Families commonly ship as separate folders with the same filenames.
            relative = file.relative_to(fonts_dir).as_posix()
            dest = target / (file.name if file.parent == fonts_dir else
                             f"stockforge-{hashlib.sha256(relative.encode()).hexdigest()[:12]}-{file.name}")
            registered = f"{_face_name(file)} ({'OpenType' if file.suffix.lower() == '.otf' else 'TrueType'})"
            try:
                if not dest.exists() or dest.read_bytes() != file.read_bytes():
                    shutil.copy2(file, dest)
                winreg.SetValueEx(key, registered, 0, winreg.REG_SZ, str(dest))
                _register_font(dest)
                done.append((file.name, f"installed as {registered}"))
            except OSError as exc:
                done.append((file.name, f"could not install: {exc}"))

    _broadcast_font_change()
    return done


def _register_font(path: Path) -> None:
    """Make newly installed fonts available in this Windows session."""
    import ctypes

    ctypes.windll.gdi32.AddFontResourceW(str(path))


def _broadcast_font_change() -> None:
    """Tell running programs a font was added, so Inkscape sees it without a
    reboot. Best effort — failing to tell them costs a restart, not the fonts."""
    try:
        import ctypes

        HWND_BROADCAST, WM_FONTCHANGE = 0xFFFF, 0x001D
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST, WM_FONTCHANGE, 0, 0, 0, 1000, None)
    except Exception as exc:
        log.debug("could not broadcast the font change: %s", exc)
