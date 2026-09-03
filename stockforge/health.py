"""Setup checks — what is working, what is not, and what to do about it.

The UI's first screen. Every check returns one of three states and, when it is
not green, the exact command or setting that fixes it. Nobody should have to
read the source to find out why nothing is happening.
"""

from __future__ import annotations

import json
import os
import shutil
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .config import Settings, settings as default_settings


@dataclass
class Check:
    name: str
    state: str                    # ok | warn | fail
    detail: str = ""
    fix: str = ""
    required: bool = True

    def as_dict(self) -> dict:
        return {"name": self.name, "state": self.state, "detail": self.detail,
                "fix": self.fix, "required": self.required}


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    @property
    def workable(self) -> bool:
        return not any(c.state == "fail" and c.required for c in self.checks)

    def as_dict(self) -> dict:
        return {
            "workable": self.workable,
            "blocking": [c.name for c in self.checks if c.state == "fail" and c.required],
            "checks": [c.as_dict() for c in self.checks],
        }


# --------------------------------------------------------------------------

def _check_vision() -> Check:
    base = os.environ.get("SF_VISION_BASE_URL", "http://localhost:8000/v1")
    model = os.environ.get("SF_VISION_MODEL")
    if not model:
        return Check("Vision model", "fail",
                     "SF_VISION_MODEL is not set",
                     "Set SF_VISION_MODEL to whatever your server is serving, "
                     "e.g. nvidia/llama-3.2-90b-vision-instruct")
    try:
        req = urllib.request.Request(f"{base.rstrip('/')}/models")
        key = os.environ.get("SF_VISION_API_KEY")
        if key:
            req.add_header("Authorization", f"Bearer {key}")
        with urllib.request.urlopen(req, timeout=6) as resp:
            body = json.loads(resp.read())
        served = [m.get("id") for m in body.get("data", [])]
        if served and model not in served:
            return Check("Vision model", "warn",
                         f"server is up but is serving {', '.join(filter(None, served))[:80]}",
                         f"Set SF_VISION_MODEL to one of those, or load {model}")
        return Check("Vision model", "ok", f"{model} at {base}")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
        return Check("Vision model", "fail", f"cannot reach {base} — {exc}",
                     "Start your model server. NIM, vLLM, Ollama and LM Studio all "
                     "expose an OpenAI-compatible /v1 endpoint.")


def _check_fonts(cfg: Settings) -> Check:
    manifest = cfg.fonts_dir / "manifest.json"
    files = [p for p in cfg.fonts_dir.rglob("*") if p.suffix.lower() in {".ttf", ".otf"}]
    if not files:
        return Check("Font library", "fail", f"no font files in {cfg.fonts_dir}",
                     "Drop font families you can use into that folder, then run "
                     "`stockforge fonts scan`. Google Fonts is the easy source.")
    if not manifest.exists():
        return Check("Font library", "fail", f"{len(files)} fonts, no manifest yet",
                     "Run `stockforge fonts scan`")
    try:
        entries = json.loads(manifest.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        return Check("Font library", "fail", f"manifest unreadable: {exc}",
                     "Delete it and run `stockforge fonts scan` again")
    usable = [e for e in entries if e.get("embeddable")]
    if not usable:
        return Check("Font library", "fail",
                     f"{len(entries)} fonts scanned, none marked usable",
                     "Open the manifest and set embeddable:true on the families "
                     "you can use. Nothing is used until you do.")
    categories = {e.get("category") for e in usable}
    if len(categories) < 3:
        return Check("Font library", "warn",
                     f"{len(usable)} usable fonts across {len(categories)} categories",
                     "Add at least a serif, a sans, a script and a display so the "
                     "matcher has something to choose from")
    return Check("Font library", "ok", f"{len(usable)} usable across {len(categories)} categories")


def _check_binary(name: str, what: str, fix: str, required: bool = True) -> Check:
    found = shutil.which(name)
    if found:
        return Check(what, "ok", found, required=required)
    return Check(what, "fail" if required else "warn", f"{name} not on PATH",
                 fix, required=required)


def _check_motifs(cfg: Settings) -> Check:
    files = list(cfg.motifs_dir.glob("*.svg"))
    if len(files) < 10:
        return Check("Motif library", "warn", f"{len(files)} motifs",
                     "Every decorative element with no library match sends its "
                     "design to review. Build this up as you go — it is what "
                     "decides whether output looks professional.",
                     required=False)
    return Check("Motif library", "ok", f"{len(files)} motifs", required=False)


def _check_etsy() -> Check:
    if os.environ.get("SF_ETSY_API_KEY"):
        return Check("Etsy API", "ok", "key set — shop counts and listings are reliable",
                     required=False)
    return Check("Etsy API", "warn", "no key",
                 "Without a key the shop door falls back to parsing public pages, "
                 "which is slower and breaks when Etsy changes their markup. A key "
                 "from etsy.com/developers takes ten minutes.", required=False)


def _check_ftp() -> Check:
    ready = [n for n in ("ADOBE", "SHUTTERSTOCK")
             if all(os.environ.get(f"SF_FTP_{n}_{k}") for k in ("HOST", "USER", "PASS"))]
    if not ready:
        return Check("Delivery", "warn", "no FTP credentials set",
                     "Only needed when you are ready to upload. Set "
                     "SF_FTP_ADOBE_HOST/USER/PASS and the same for SHUTTERSTOCK.",
                     required=False)
    return Check("Delivery", "ok", f"configured: {', '.join(t.lower() for t in ready)}",
                 required=False)


def _check_workspace(cfg: Settings) -> Check:
    try:
        cfg.ensure_dirs()
        probe = cfg.root / ".writable"
        probe.write_text("ok")
        probe.unlink()
    except OSError as exc:
        return Check("Workspace", "fail", f"{cfg.root} is not writable — {exc}",
                     "Point SF_ROOT somewhere you can write")
    usage = shutil.disk_usage(cfg.root)
    free_gb = usage.free / 1e9
    if free_gb < 5:
        return Check("Workspace", "warn", f"{free_gb:.1f} GB free",
                     "Rebuilding a large catalogue needs room for flats, renders "
                     "and exports. Clear some space.", required=False)
    return Check("Workspace", "ok", f"{cfg.root} — {free_gb:.0f} GB free")


def report(cfg: Settings | None = None) -> Report:
    cfg = cfg or default_settings
    return Report(checks=[
        _check_vision(),
        _check_fonts(cfg),
        _check_binary("inkscape", "Vector export",
                      "Install Inkscape. Without it there is no editable-text PDF "
                      "and no EPS — only rasterised output."),
        _check_binary("tesseract", "OCR", "Install tesseract-ocr. Without it the model "
                      "transcribes text itself, which is less accurate.", required=False),
        _check_motifs(cfg),
        _check_workspace(cfg),
        _check_etsy(),
        _check_ftp(),
    ])
