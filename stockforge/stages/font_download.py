"""A small, pinned Google Fonts library with the upstream OFL licenses."""

import hashlib
import base64
import json
from pathlib import Path

from ..sources.http import get
from . import fonts

CATALOG = Path(__file__).resolve().parents[1] / "data" / "starter-fonts.json"


def download_starter(fonts_dir: Path) -> list[fonts.FontEntry]:
    resources = json.loads(CATALOG.read_text(encoding="utf-8"))
    for resource in resources:
        path = fonts_dir / resource["path"]
        expected = resource["sha256"]
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            data = base64.b64decode(json.loads(get(resource["url"], timeout=60))["content"])
            if hashlib.sha256(data).hexdigest() != expected:
                raise ValueError(f"Checksum mismatch: {resource['path']}; download was not installed")
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(path.suffix + ".download")
            temp.write_bytes(data)
            temp.replace(path)

    # Only the verified starter fonts receive these license/category tags.
    metadata = {r["path"]: r["font"] for r in resources if "font" in r}
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
