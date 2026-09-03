"""Door 3 — images already on disk."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

from ..constants import IMAGE_EXTS
from .base import Design, Source, group_by_stem


class FolderSource(Source):
    def _paths(self) -> list[Path]:
        root = Path(self.target).expanduser()
        if not root.exists():
            raise FileNotFoundError(root)
        return sorted(p for p in root.rglob("*") if p.suffix.lower() in IMAGE_EXTS and p.is_file())

    def count(self) -> int:
        return len(group_by_stem(self._paths()))

    def designs(self) -> Iterator[Design]:
        for i, (key, images) in enumerate(group_by_stem(self._paths()).items()):
            if self.limit and i >= self.limit:
                return
            yield Design(design_id=key, images=images, source="folder")
