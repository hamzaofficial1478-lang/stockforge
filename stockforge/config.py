"""Settings. Everything tunable in one place, read from env with sane defaults.

Two things about how this file behaves, both of which used to be wrong.

`.env` is read here, at import, before anything else looks at the environment.
Nothing read it before: the control panel wrote your model URL, your Etsy key
and every slider into it and set them on the running process, so they worked
until you restarted and then went quietly back to defaults, with the Setup
screen red and nothing to explain why.

And every setting is read when a Settings is built, not when this module is
first imported. They used to be plain dataclass defaults, which Python
evaluates once at class-definition time, so a value put into the environment
after the first `import stockforge.config` could never take effect — which is
the other half of why the sliders did nothing.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, fields
from pathlib import Path

log = logging.getLogger("stockforge.config")


def _p(env: str, default: str) -> Path:
    return Path(os.environ.get(env, default)).expanduser()


def _f(env: str, default: float) -> float:
    try:
        return float(os.environ[env])
    except (KeyError, ValueError):
        return default


def _i(env: str, default: int) -> int:
    try:
        return int(os.environ[env])
    except (KeyError, ValueError):
        return default


def _b(env: str, default: bool = False) -> bool:
    return os.environ.get(env, "1" if default else "0").strip().lower() in {"1", "true", "yes", "on"}


# --------------------------------------------------------------------------
# .env
# --------------------------------------------------------------------------

def env_file() -> Path:
    """Where settings are read from and written back to.

    One path for both, so the panel cannot save to a file nothing loads —
    which is exactly what it did.
    """
    return Path(os.environ.get("SF_ENV_FILE", ".env")).expanduser()


def load_env(path: Path | None = None) -> dict[str, str]:
    """Read `.env` into the environment. Returns what it set.

    A variable already exported wins over the file. That is the usual contract
    and it is what lets you override one setting for a single run without
    editing anything.
    """
    path = path or env_file()
    loaded: dict[str, str] = {}
    try:
        text = path.read_text()
    except OSError:
        return loaded

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded[key] = value
    return loaded


load_env()


@dataclass
class Settings:
    # --- paths -----------------------------------------------------------
    root: Path = field(default_factory=lambda: _p("SF_ROOT", "./workspace"))
    fonts_dir: Path = field(default_factory=lambda: _p("SF_FONTS", "./assets/fonts"))
    motifs_dir: Path = field(default_factory=lambda: _p("SF_MOTIFS", "./assets/motifs"))

    # --- pipeline --------------------------------------------------------
    max_critique_rounds: int = field(default_factory=lambda: _i("SF_CRITIQUE_ROUNDS", 2))
    ship_threshold: float = field(default_factory=lambda: _f("SF_SHIP_THRESHOLD", 0.85))
    escalate_threshold: float = field(default_factory=lambda: _f("SF_ESCALATE_THRESHOLD", 0.60))

    # --- making it a new design ------------------------------------------
    # Two levers. `mix` borrows ingredients from your OTHER designs — grid from
    # one, palette from another, decoration from a third — so the result has no
    # single original. `derive_strength` then moves that result further on its
    # own terms. Mixing is the stronger of the two; deriving alone only ever
    # walks away from one starting point.
    mix: float = field(default_factory=lambda: _f("SF_MIX", 0.5))
    derive_strength: float = field(default_factory=lambda: _f("SF_DERIVE_STRENGTH", 0.5))
    distinct_threshold: float = field(default_factory=lambda: _f("SF_DISTINCT_THRESHOLD", 0.70))
    max_derive_rounds: int = field(default_factory=lambda: _i("SF_DERIVE_ROUNDS", 2))

    # --- decoration ------------------------------------------------------
    # How close a library motif has to be before we will place it. Raise it and
    # more designs go to review with a description of what to draw; lower it
    # and you start shipping approximate decoration. A hole is the cheaper
    # mistake, so the default leans towards refusing.
    motif_threshold: float = field(default_factory=lambda: _f("SF_MOTIF_THRESHOLD", 0.45))

    # --- fetching ---------------------------------------------------------
    # A long crawl meets a rate limit and a bad gateway whatever time of day it
    # starts. These decide how patient it is before it gives up on one request.
    http_retries: int = field(default_factory=lambda: _i("SF_HTTP_RETRIES", 4))
    http_backoff: float = field(default_factory=lambda: _f("SF_HTTP_BACKOFF", 2.0))

    # --- not shipping the same thing twice --------------------------------
    # How alike two finished pages may be, in bits of a 256-bit perceptual hash.
    # Measured over real finished pages: the same file re-encoded is 0, the same
    # page recoloured is 4, and four genuinely different designs sat 40 to 74
    # apart. Twenty is comfortably between the two, with a wide margin either
    # side — raise it to catch more and send more to review, lower it to catch
    # only the obvious.
    duplicate_distance: int = field(default_factory=lambda: _i("SF_DUPLICATE_DISTANCE", 20))

    # --- output ----------------------------------------------------------
    preview_px: int = field(default_factory=lambda: _i("SF_PREVIEW_PX", 1400))

    # --- publishing ------------------------------------------------------
    publish_enabled: bool = field(default_factory=lambda: _b("SF_PUBLISH"))
    # The provenance check flags designs that lean on third-party library
    # content. By default those stop at an editable master. This sends them
    # through anyway — your catalogue, your call. Either way the flag and its
    # reason stay recorded on every design, so you can always see what went
    # out and what it was marked as.
    publish_all: bool = field(default_factory=lambda: _b("SF_PUBLISH_ALL"))

    def reload(self) -> None:
        """Re-read everything from the environment, in place.

        In place because the pipeline, the worker and the panel all hold a
        reference to one Settings. Handing back a new object would leave every
        one of them on the old values, which is indistinguishable from the
        setting having done nothing.
        """
        fresh = Settings()
        for f in fields(self):
            setattr(self, f.name, getattr(fresh, f.name))

    @property
    def db_path(self) -> Path:
        return self.root / "stockforge.db"

    def ensure_dirs(self) -> None:
        for d in (self.root, self.root / "flats", self.root / "renders",
                  self.root / "out", self.root / "downloads",
                  self.fonts_dir, self.motifs_dir):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
