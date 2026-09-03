"""Settings. Everything tunable in one place, read from env with sane defaults."""

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

    # --- pipeline --------------------------------------------------------
    max_critique_rounds: int = _i("SF_CRITIQUE_ROUNDS", 2)
    ship_threshold: float = _f("SF_SHIP_THRESHOLD", 0.85)
    escalate_threshold: float = _f("SF_ESCALATE_THRESHOLD", 0.60)

    # --- making it a new design ------------------------------------------
    # Two levers. `mix` borrows ingredients from your OTHER designs — grid from
    # one, palette from another, decoration from a third — so the result has no
    # single original. `derive_strength` then moves that result further on its
    # own terms. Mixing is the stronger of the two; deriving alone only ever
    # walks away from one starting point.
    mix: float = _f("SF_MIX", 0.5)
    derive_strength: float = _f("SF_DERIVE_STRENGTH", 0.5)
    distinct_threshold: float = _f("SF_DISTINCT_THRESHOLD", 0.70)
    max_derive_rounds: int = _i("SF_DERIVE_ROUNDS", 2)

    # --- output ----------------------------------------------------------
    preview_px: int = _i("SF_PREVIEW_PX", 1400)

    # --- publishing ------------------------------------------------------
    publish_enabled: bool = os.environ.get("SF_PUBLISH", "0") == "1"
    # The provenance check flags designs that lean on third-party library
    # content. By default those stop at an editable master. This sends them
    # through anyway — your catalogue, your call. Either way the flag and its
    # reason stay recorded on every design, so you can always see what went
    # out and what it was marked as.
    publish_all: bool = os.environ.get("SF_PUBLISH_ALL", "0") == "1"

    @property
    def db_path(self) -> Path:
        return self.root / "stockforge.db"

    def ensure_dirs(self) -> None:
        for d in (self.root, self.root / "flats", self.root / "renders",
                  self.root / "out", self.root / "downloads",
                  self.fonts_dir, self.motifs_dir):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
