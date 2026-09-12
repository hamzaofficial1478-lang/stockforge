"""The starter font library: pinned Google Fonts with their upstream licences.

Two things here are worth knowing.

The files come from raw.githubusercontent at a pinned commit rather than the
GitHub API. The API answers base64 in JSON and allows sixty unauthenticated
requests an hour, which was survivable at eight families and is not at
forty-two — you would get part of a library and a 403.

And most of the good families are now published as one variable file rather
than a file per weight. A variable font scans as a single 400-weight face, so a
library of them would have no bold in it at all. Each one is cut into the named
weights the catalogue asks for, locally, from bytes whose checksum has already
been verified. The variable originals are kept in `_variable/` — `fonts.scan`
skips folders starting with an underscore, so they do not show up twice.
"""

import base64
import hashlib
import io
import json
import logging
from pathlib import Path

from ..sources.http import get
from . import fonts

log = logging.getLogger("stockforge.fonts")

CATALOG = Path(__file__).resolve().parents[1] / "data" / "starter-fonts.json"
VARIABLE_DIR = "_variable"


def _body(url: str) -> bytes:
    """The file's bytes, whichever of the two shapes the URL is.

    Old catalogues point at api.github.com/contents, which answers JSON with
    the file base64'd inside it. New ones point at raw. Both are read here so
    that a catalogue written before this change still installs.
    """
    raw = get(url, timeout=60)
    if "api.github.com" in url:
        return base64.b64decode(json.loads(raw)["content"])
    return raw


def _instantiate(data: bytes, wght: int) -> bytes:
    """One static weight cut out of a variable font.

    `updateFontNames` is what makes this worth doing: without it every cut
    keeps the variable font's own name records and the whole family scans as
    four files all called Regular.
    """
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer

    font = TTFont(io.BytesIO(data))
    instancer.instantiateVariableFont(font, {"wght": wght}, inplace=True,
                                      updateFontNames=True)
    buffer = io.BytesIO()
    font.save(buffer)
    return buffer.getvalue()


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".download")
    temp.write_bytes(data)
    temp.replace(path)


def _cut_names(resource: dict) -> dict[str, int]:
    """Where each static weight of a variable font goes, and at what weight.

    `Playfair Display[wght].ttf` becomes `PlayfairDisplay-Regular.ttf` and
    `PlayfairDisplay-Bold.ttf`; the italic file becomes `-Italic` and
    `-BoldItalic`, because a bold cut of an italic font is still italic and
    calling it `Italic-Bold` would scan as neither.
    """
    stem = Path(resource["path"]).name.split("[")[0]
    italic = stem.endswith("-Italic")
    base = stem[: -len("-Italic")] if italic else stem
    folder = Path(resource["path"]).parent
    out = {}
    for wanted in resource["instances"]:
        style = wanted["style"]
        if italic:
            style = "Italic" if style == "Regular" else f"{style}Italic"
        out[str(folder / f"{base}-{style}.ttf")] = int(wanted["wght"])
    return out


def download_starter(fonts_dir: Path, on_progress=None) -> list[fonts.FontEntry]:
    """Install the starter library, reporting as it goes.

    `on_progress(done, total, name)` is called per resource. Forty-two families
    is a minute or so of downloading and a silent minute looks like a hang, so
    the caller is given something to print.
    """
    resources = json.loads(CATALOG.read_text(encoding="utf-8"))
    metadata: dict[str, dict] = {}

    for number, resource in enumerate(resources, 1):
        if on_progress:
            on_progress(number, len(resources), resource["path"])
        instances = resource.get("instances")
        # A variable font is kept out of the way; everything else lands where
        # the catalogue says it does.
        stored = fonts_dir / (VARIABLE_DIR if instances else ".") / resource["path"]
        expected = resource["sha256"]
        have = stored.is_file() and hashlib.sha256(stored.read_bytes()).hexdigest() == expected

        cuts = _cut_names(resource) if instances else {}
        missing_cuts = [c for c in cuts if not (fonts_dir / c).is_file()]

        if have and not missing_cuts:
            data = None
        else:
            data = _body(resource["url"])
            if hashlib.sha256(data).hexdigest() != expected:
                raise ValueError(
                    f"Checksum mismatch: {resource['path']}; download was not installed")
            _write(stored, data)

        if cuts:
            if data is None:
                data = stored.read_bytes()
            for relative, wght in cuts.items():
                target = fonts_dir / relative
                if target.is_file():
                    continue
                try:
                    _write(target, _instantiate(data, wght))
                except Exception as exc:
                    # A family that will not cut is one family missing, not a
                    # failed install. Say which, and carry on with the rest.
                    log.warning("could not cut %s from %s: %s", relative, resource["path"], exc)
            for relative in cuts:
                if "font" in resource:
                    metadata[Path(relative).as_posix()] = resource["font"]
        elif "font" in resource:
            metadata[Path(resource["path"]).as_posix()] = resource["font"]

    # Only the verified starter fonts receive these license/category tags.
    entries = fonts.scan(fonts_dir)
    for entry in entries:
        tags = metadata.get(entry.path.replace("\\", "/"))
        if tags:
            for key, value in tags.items():
                setattr(entry, key, value)
            entry.licence = "OFL-1.1"
            entry.embeddable = True
    fonts.write_manifest(fonts_dir, entries)
    fonts.write_fontconfig(fonts_dir)
    return entries
