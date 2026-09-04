"""Motif matching against our own library.

The analyser describes a decorative element in plain English — "grinning carved
jack-o-lantern, three-quarter view, warm light from within". This module turns
that description into one of the SVG files in `assets/motifs/`, or refuses and
leaves the element unresolved.

Refusing matters as much as matching. An unresolved motif sends its design to
review and puts its description on the list of what to draw next, which is the
only way the library ever gets built. A wrong match is worse than a hole: a
hole you can see, a wrong pumpkin ships.

Same constraint as fonts, for the same reason — we only ever place artwork we
drew ourselves, so everything that comes out is ours to redistribute.

Motifs are plain SVG on a 0..100 unit square. What the matcher needs to know
rides inside the file, so a motif stays one file with nothing to keep in step:

    <svg viewBox="0 0 100 100" data-kind="botanical"
         data-tags="eucalyptus, sprig, leaves, greenery, wedding">
      <title>Eucalyptus sprig</title>
      <desc>slender stem with paired oval leaves</desc>

None of it is required. With no metadata the filename is used, so
`sprig-eucalyptus-01.svg` still answers to "eucalyptus sprig" — a folder of
untagged drawings works the moment you drop it in, and tagging it later only
sharpens the matching.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..schema import DesignSpec, MotifElement, MotifKind

log = logging.getLogger("stockforge.motifs")

# Below this a match is not trusted and the element is left as a hole. Tuned so
# that a shared subject word plus the right kind gets through, and the right
# kind on its own does not.
DEFAULT_THRESHOLD = 0.45

# A library id is a bare filename stem, because that is what the renderer joins
# onto the motifs folder. Anything else — a path, a traversal — is not ours.
_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$", re.I)


# --------------------------------------------------------------------------
# reading the library
# --------------------------------------------------------------------------

_SVG_TAG = re.compile(r"<svg\b[^>]*>", re.I)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_DESC = re.compile(r"<desc[^>]*>(.*?)</desc>", re.S | re.I)

_KINDS = {k.value for k in MotifKind}

# Words that describe where a thing sits or how it is framed, rather than what
# it is. They are the bulk of a good description and none of its subject.
_STOP = {
    "and", "the", "for", "with", "from", "into", "onto", "over", "under",
    "its", "this", "that", "these", "those", "are", "was", "were", "has",
    "have", "very", "quite", "some", "each", "all", "any", "one", "two",
    "three", "four", "five", "half", "full",
    "left", "right", "top", "bottom", "upper", "lower", "centre", "center",
    "middle", "side", "corner", "above", "below", "view", "facing", "angle",
    "quarter", "profile", "front", "back", "large", "small", "tiny", "big",
}

_WORD = re.compile(r"[a-z]+")


def _tokens(*texts: str) -> set[str]:
    """Subject words, crudely singularised. Crude is the right amount of clever
    here — the tags are there for anything a stemmer would get wrong."""
    out: set[str] = set()
    for text in texts:
        for word in _WORD.findall(text.lower()):
            if len(word) < 3 or word in _STOP:
                continue
            if len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
                word = word[:-1]
            out.add(word)
    return out


@dataclass
class MotifEntry:
    """One drawing in the library."""

    library_id: str                       # the filename stem, and what a spec stores
    kind: str = ""                        # a MotifKind value, or "" for untagged
    name: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    tokens: set[str] = field(default_factory=set, repr=False)

    def __post_init__(self) -> None:
        if not self.tokens:
            self.tokens = _tokens(
                self.library_id.replace("-", " ").replace("_", " "),
                self.name, self.description, " ".join(self.tags),
            )


def _attr(tag: str, name: str) -> str:
    m = re.search(rf'\b{name}\s*=\s*"([^"]*)"', tag, re.I)
    return m.group(1).strip() if m else ""


def read(path: Path) -> MotifEntry:
    """Read one motif file. Never raises on odd content — a motif we cannot
    understand still gets its filename, which is usually enough."""
    raw = path.read_text(errors="replace")
    tag = m.group(0) if (m := _SVG_TAG.search(raw)) else ""

    kind = _attr(tag, "data-kind").lower()
    if kind and kind not in _KINDS:
        log.warning("%s: data-kind %r is not a motif kind, ignoring", path.name, kind)
        kind = ""

    tags = [t.strip().lower() for t in _attr(tag, "data-tags").split(",") if t.strip()]
    title = m.group(1).strip() if (m := _TITLE.search(raw)) else ""
    desc = m.group(1).strip() if (m := _DESC.search(raw)) else ""

    return MotifEntry(library_id=path.stem, kind=kind, name=title,
                      description=desc, tags=tags)


def scan(motifs_dir: Path) -> list[MotifEntry]:
    entries: list[MotifEntry] = []
    for path in sorted(motifs_dir.glob("*.svg")):
        if not _SAFE_ID.match(path.stem):
            log.warning("skipping %s — a motif filename must be plain", path.name)
            continue
        try:
            entries.append(read(path))
        except OSError as exc:
            log.warning("could not read motif %s: %s", path.name, exc)
    return entries


_cache: dict[Path, tuple[tuple, list[MotifEntry]]] = {}


def load(motifs_dir: Path) -> list[MotifEntry]:
    """The library, re-read whenever it changes on disk.

    Cached on the name and mtime of every file, so drawing a new motif or
    retagging an old one takes effect on the next design rather than the next
    restart. Motif files are small and there are hundreds, not millions.
    """
    try:
        stamp = tuple(sorted((p.name, p.stat().st_mtime)
                             for p in motifs_dir.glob("*.svg")))
    except OSError:
        return []
    hit = _cache.get(motifs_dir)
    if hit and hit[0] == stamp:
        return hit[1]
    entries = scan(motifs_dir)
    _cache[motifs_dir] = (stamp, entries)
    return entries


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------

# Kinds that can stand in for one another. A sprig is close enough to a floral
# slot; a border is not close to a pumpkin.
_NEIGHBOURS: dict[frozenset[str], float] = {}
for _a, _b, _w in [
    ("botanical", "floral", 0.85),
    ("botanical", "seasonal", 0.45),
    ("floral", "seasonal", 0.45),
    ("frame", "border", 0.75),
    ("border", "rule", 0.55),
    ("rule", "flourish", 0.50),
    ("flourish", "border", 0.45),
    ("flourish", "floral", 0.40),
    ("geometric", "frame", 0.40),
    ("geometric", "rule", 0.35),
    ("icon", "seasonal", 0.55),
    ("icon", "geometric", 0.30),
    ("texture", "geometric", 0.25),
]:
    _NEIGHBOURS[frozenset((_a, _b))] = _w

KIND_WEIGHT = 0.35
TEXT_WEIGHT = 0.65


def _kind_score(entry_kind: str, wanted: str) -> float:
    if not entry_kind:
        # Untagged. Don't reward it and don't punish it — let the words decide.
        return 0.6
    if entry_kind == wanted:
        return 1.0
    return _NEIGHBOURS.get(frozenset((entry_kind, wanted)), 0.0)


def score(entry: MotifEntry, el: MotifElement) -> float:
    """0..1. The words carry most of it, because kind barely separates anything
    — a pumpkin and a ghost are both `seasonal`, and picking the wrong one of
    those is exactly the mistake that matters."""
    kind = _kind_score(entry.kind, el.motif.value)
    wanted = _tokens(el.description)
    if not wanted:
        return KIND_WEIGHT * kind

    shared = wanted & entry.tokens
    # Two subject words in common is already a strong signal. A long, careful
    # description should not be marked down for the adjectives no library entry
    # has any reason to carry.
    denominator = max(2, min(len(wanted), 4))
    text = min(1.0, len(shared) / denominator)
    return KIND_WEIGHT * kind + TEXT_WEIGHT * text


def rank(el: MotifElement, library: list[MotifEntry]) -> list[tuple[MotifEntry, float]]:
    """Every candidate, best first. The CLI shows this so you can see why a
    motif did or did not match before drawing another one."""
    return sorted(((e, score(e, el)) for e in library), key=lambda p: -p[1])


def match(el: MotifElement, library: list[MotifEntry],
          threshold: float = DEFAULT_THRESHOLD) -> tuple[MotifEntry | None, float]:
    ordered = rank(el, library)
    if not ordered:
        return None, 0.0
    best, best_score = ordered[0]
    return (best, best_score) if best_score >= threshold else (None, best_score)


# --------------------------------------------------------------------------

def resolve(spec: DesignSpec, motifs_dir: Path,
            threshold: float = DEFAULT_THRESHOLD) -> list[str]:
    """Point every motif in a spec at a file in the library.

    Returns the descriptions nothing matched — the queue of what to draw next.

    An id already on an element is honoured only if it names a file that is
    actually there. That covers a motif since deleted, and it covers the
    analyser filling the field in itself: the model is shown this part of the
    schema, so it can and will invent an id, and only the matcher is entitled
    to set one.
    """
    library = load(motifs_dir)
    have = {e.library_id: e for e in library}
    unmatched: list[str] = []

    for el in spec.motifs():
        if el.library_id and el.library_id in have:
            continue
        if el.library_id:
            log.debug("motif id %r is not in the library, re-matching", el.library_id)
            el.library_id, el.match_score = None, None

        entry, best = match(el, library, threshold)
        if entry is None:
            unmatched.append(el.description)
            continue
        el.library_id = entry.library_id
        el.match_score = round(best, 3)

    return unmatched
