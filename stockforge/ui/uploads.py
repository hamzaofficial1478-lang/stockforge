"""Files handed to the panel from a browser.

The folder door assumed the images were already somewhere you could type the
path of. On Windows, with a shop's exports sitting in a download folder or
still zipped, that is the wrong assumption — and the panel offered no way to
put them anywhere, so the door could not be used at all from the browser it
was built for.

Everything here is deliberately dull: parse a multipart body without a
dependency, put the files somewhere inside the workspace, unpack an archive if
that is what arrived, and say exactly what happened to each one. The saying is
the point. A pull that quietly drops nine files out of fifty is the failure
this whole project keeps running into.
"""

from __future__ import annotations

import logging
import shutil
import zipfile
from dataclasses import asdict, dataclass, field
from email.parser import BytesParser
from email.policy import HTTP
from pathlib import Path

from ..constants import ARCHIVE_EXTS, IMAGE_EXTS

log = logging.getLogger("stockforge.ui.uploads")

# Windows and macOS both scatter these through a zip. They are not designs.
JUNK = {"__MACOSX", ".DS_Store", "Thumbs.db", "desktop.ini"}


@dataclass
class Landed:
    """One file, and what became of it."""

    name: str
    kind: str                      # image | archive | rejected
    ok: bool
    bytes: int = 0
    reason: str = ""
    extracted: int = 0             # for an archive, how many images came out


@dataclass
class Batch:
    folder: str = ""
    files: list[Landed] = field(default_factory=list)

    @property
    def images(self) -> int:
        return sum(1 for f in self.files if f.ok and f.kind == "image")

    def as_dict(self) -> dict:
        by_ext: dict[str, int] = {}
        for f in self.files:
            if f.ok and f.kind == "image":
                by_ext[Path(f.name).suffix.lower()] = \
                    by_ext.get(Path(f.name).suffix.lower(), 0) + 1
        return {
            "folder": self.folder,
            "accepted": self.images,
            "rejected": sum(1 for f in self.files if not f.ok),
            "by_extension": dict(sorted(by_ext.items())),
            "files": [asdict(f) for f in self.files],
        }


def _safe_name(raw: str) -> str:
    """A filename and nothing else — no directories, no traversal.

    A browser sends whatever the file was called, and an archive can hold
    anything at all, including ../../.
    """
    name = Path(raw.replace("\\", "/")).name.strip()
    return name or "unnamed"


def parse_multipart(body: bytes, content_type: str) -> list[tuple[str, bytes]]:
    """(filename, content) for each file part. Standard library only."""
    header = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode()
    message = BytesParser(policy=HTTP).parsebytes(header + body)
    out: list[tuple[str, bytes]] = []
    for part in message.iter_parts() if message.is_multipart() else []:
        name = part.get_filename()
        if not name:
            continue
        out.append((_safe_name(name), part.get_payload(decode=True) or b""))
    return out


def _unpack(archive: Path, into: Path) -> tuple[list[tuple[str, int]], list[str]]:
    """Images out of a zip, flat.

    Returns ([(name, bytes)], [names that were not images]). Each extracted
    file is reported in its own right rather than as a count, because "37
    images" does not tell you which four of your forty-one are missing.
    """
    kept: list[tuple[str, int]] = []
    skipped: list[str] = []
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = _safe_name(info.filename)
            parts = Path(info.filename.replace("\\", "/")).parts
            if any(p in JUNK for p in parts) or name.startswith("._"):
                continue
            if Path(name).suffix.lower() not in IMAGE_EXTS:
                skipped.append(name)
                continue
            dest = into / name
            n = 1
            while dest.exists():                     # two folders, one filename
                dest = into / f"{Path(name).stem}-{n}{Path(name).suffix}"
                n += 1
            with zf.open(info) as src, dest.open("wb") as out:
                shutil.copyfileobj(src, out)
            kept.append((dest.name, dest.stat().st_size))
    return kept, skipped


def receive(files: list[tuple[str, bytes]], into: Path) -> Batch:
    """Put the files somewhere and report on every one of them."""
    into.mkdir(parents=True, exist_ok=True)
    batch = Batch(folder=str(into))

    for name, content in files:
        suffix = Path(name).suffix.lower()

        if suffix in ARCHIVE_EXTS:
            tmp = into / f".{name}"
            tmp.write_bytes(content)
            try:
                kept, skipped = _unpack(tmp, into)
            except zipfile.BadZipFile:
                batch.files.append(Landed(name, "archive", False, len(content),
                                          "not a readable zip"))
                tmp.unlink(missing_ok=True)
                continue
            finally:
                tmp.unlink(missing_ok=True)
            note = "" if not skipped else \
                f"{len(skipped)} file(s) inside were not images and were left out"
            batch.files.append(Landed(name, "archive", bool(kept), len(content),
                                      note or "", extracted=len(kept)))
            if not kept:
                batch.files[-1].reason = "no images inside"
            # Each one on its own row. "37 images" does not tell you which four
            # of your forty-one are missing.
            for inner, size in kept:
                batch.files.append(Landed(inner, "image", True, size,
                                          f"from {name}"))
            continue

        if suffix not in IMAGE_EXTS:
            batch.files.append(Landed(
                name, "rejected", False, len(content),
                f"{suffix or 'no extension'} is not an image this reads — "
                f"{', '.join(sorted(IMAGE_EXTS))}"))
            continue

        if not content:
            batch.files.append(Landed(name, "image", False, 0, "the file was empty"))
            continue

        dest = into / name
        n = 1
        while dest.exists():
            dest = into / f"{Path(name).stem}-{n}{Path(name).suffix}"
            n += 1
        dest.write_bytes(content)
        batch.files.append(Landed(dest.name, "image", True, len(content)))

    return batch
