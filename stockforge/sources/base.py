from __future__ import annotations

import hashlib
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator


@dataclass
class Design:
    """One product. Several images, one design."""

    design_id: str
    images: list[Path] = field(default_factory=list)
    title: str | None = None
    tags: list[str] = field(default_factory=list)
    listing_url: str | None = None
    source: str = "folder"

    @property
    def stable_id(self) -> str:
        return hashlib.sha256(self.design_id.encode()).hexdigest()[:24]


class Source(ABC):
    """Yields designs. Nothing more."""

    def __init__(self, target: str, cache_dir: Path | None = None, limit: int | None = None):
        self.target = target
        self.cache_dir = cache_dir
        self.limit = limit

    @abstractmethod
    def designs(self) -> Iterator[Design]:
        ...

    def count(self) -> int | None:
        """How many designs are there? None when the source cannot say without
        walking the whole thing."""
        return None


_TRAILING_NUMBER = re.compile(r"^(.*?)[._\-\s]*(\d{1,3})$")


def group_by_stem(paths: list[Path]) -> dict[str, list[Path]]:
    """Group `haunted-invite-1.jpg ... haunted-invite-5.jpg` into one design.

    Falls back to the parent folder name when filenames give nothing away, which
    is the common shape of an Etsy export: one folder per listing.
    """
    groups: dict[str, list[Path]] = {}
    for path in sorted(paths):
        m = _TRAILING_NUMBER.match(path.stem)
        stem = m.group(1) if m else path.stem
        key = stem.strip("-_ .") or path.parent.name
        groups.setdefault(f"{path.parent.name}/{key}", []).append(path)
    return groups
