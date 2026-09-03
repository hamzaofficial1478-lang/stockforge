"""Settings. Everything tunable lives here, read from env with sane defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _p(env: str, default: str) -> Path:
    return Path(os.environ.get(env, default)).expanduser()


def _f(env: str, default: float) -> float:
    return float(os.environ.get(env, default))


def _i(env: str, default: int) -> int:
    return int(os.environ.get(env, default))


@dataclass
class Settings:
    # --- paths -----------------------------------------------------------
    root: Path = field(default_factory=lambda: _p("SF_ROOT", "./workspace"))
    fonts_dir: Path = field(default_factory=lambda: _p("SF_FONTS", "./assets/fonts"))
    motifs_dir: Path = field(default_factory=lambda: _p("SF_MOTIFS", "./assets/motifs"))

    # --- model -----------------------------------------------------------
    # Opus 5 is the default deliberately. This pipeline lives or dies on how
    # well one call reads a layout; a weaker read costs more in critique
    # rounds and human review than it saves per token.
    model: str = os.environ.get("SF_MODEL", "claude-opus-5")
    effort: str = os.environ.get("SF_EFFORT", "high")
    max_tokens: int = _i("SF_MAX_TOKENS", 16000)

    # --- pipeline --------------------------------------------------------
    max_critique_rounds: int = _i("SF_CRITIQUE_ROUNDS", 3)
    ship_threshold: float = _f("SF_SHIP_THRESHOLD", 0.88)
    escalate_threshold: float = _f("SF_ESCALATE_THRESHOLD", 0.62)
    concurrency: int = _i("SF_CONCURRENCY", 4)

    # --- clustering ------------------------------------------------------
    phash_distance: int = _i("SF_PHASH_DISTANCE", 12)
    aspect_tolerance: float = _f("SF_ASPECT_TOLERANCE", 0.04)

    # --- spend -----------------------------------------------------------
    # A hard stop. 5,000 assets is enough volume that a bug in the critique
    # loop could burn real money before anyone notices.
    daily_usd_cap: float = _f("SF_DAILY_USD_CAP", 40.0)

    # --- output ----------------------------------------------------------
    target_dpi: int = _i("SF_TARGET_DPI", 300)
    preview_px: int = _i("SF_PREVIEW_PX", 1400)

    @property
    def db_path(self) -> Path:
        return self.root / "stockforge.db"

    def ensure_dirs(self) -> None:
        for d in (
            self.root,
            self.root / "flats",
            self.root / "renders",
            self.root / "out",
            self.fonts_dir,
            self.motifs_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
