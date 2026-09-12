"""Settings. Everything tunable in one place, read from env with sane defaults.

Two things about how this file behaves, both of which used to be wrong.

`.env` is read here, at import, before anything else looks at the environment.
Nothing read it before: the control panel wrote your model URL, your Etsy key
and every slider into it and set them on the running process, so they worked
until you restarted and then went quietly back to defaults, with the Setup
screen red and nothing to explain why.

It came back twice after that, both silent, both looking exactly like "the
panel does not save". Finding the file is one: a bare `.env` is relative to
wherever you happened to be standing, so launching from a shortcut instead of
the folder read a different file and showed you defaults. Being allowed to keep
it is the other: the usual dotenv rule is that an exported variable wins, and a
single stray `SF_MIX` in a system environment then beat the saved file on every
start, for good, with nothing on screen to say so. Here the saved file wins for
anything the panel can write, because the panel is where you set it. Everything
else keeps the usual rule, and `SF_ENV_OVERRIDE=1` puts it back.

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

_ENV_FILE: Path | None = None

# What the control panel is allowed to write, and therefore what it is trusted
# to own. Kept here rather than imported from the panel so that loading
# settings never drags the server module in.
PANEL_OWNED = ("SF_VISION_", "SF_REASON_", "SF_IMAGE_", "SF_ETSY_", "SF_FTP_",
               "SF_MIX", "SF_DERIVE_", "SF_DISTINCT_", "SF_MOTIF_",
               "SF_CRITIQUE_", "SF_PRESERVE_", "SF_PUBLISH", "SF_WORKERS",
               "SF_ROOT", "SF_FONTS", "SF_MOTIFS")


def _panel_owned(key: str) -> bool:
    return key.startswith(PANEL_OWNED)


def _home() -> Path:
    """Somewhere stable to keep settings when there is no project folder.

    Only reached for an installed copy with no `.env` anywhere above it.
    """
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "stockforge"


def _project() -> Path | None:
    """The folder holding the stockforge package, when that is a checkout.

    Site-packages is not a place to keep your Etsy key, so an installed copy
    gets None and falls back to the per-user folder.
    """
    here = Path(__file__).resolve().parent.parent
    if any(part in {"site-packages", "dist-packages"} for part in here.parts):
        return None
    return here


def find_env_file() -> Path:
    """Where settings are read from and written back to.

    One path for both, so the panel cannot save to a file nothing loads —
    which is exactly what it did. In order:

    1. `SF_ENV_FILE`, if you want to say outright.
    2. An existing `.env` in this folder or any folder above it. This is what
       makes running from a sub-folder, a shortcut or an IDE find the same
       settings as running from the checkout.
    3. The checkout itself, or a per-user config folder for an installed copy.

    Only step 2 looks for an existing file; the rest name where a new one goes.
    """
    named = os.environ.get("SF_ENV_FILE")
    if named:
        return Path(named).expanduser().resolve()

    here = Path.cwd().resolve()
    for folder in (here, *here.parents):
        candidate = folder / ".env"
        if candidate.is_file():
            return candidate

    project = _project()
    if project is not None:
        return project / ".env"
    return _home() / ".env"


def env_file() -> Path:
    """`find_env_file`, resolved once and remembered.

    Pinned because anything that changes directory later — a launcher, a
    packaged build, the panel serving files — would otherwise start reading and
    writing somewhere else mid-run. Being told outright is read every time and
    never pinned, so a caller that sets it gets what it asked for.
    """
    named = os.environ.get("SF_ENV_FILE")
    if named:
        return Path(named).expanduser().resolve()
    global _ENV_FILE
    if _ENV_FILE is None:
        _ENV_FILE = find_env_file()
    return _ENV_FILE


def load_env(path: Path | None = None) -> dict[str, str]:
    """Read `.env` into the environment. Returns what it set.

    A variable already exported wins over the file, as usual — except for
    settings the control panel writes, where the file wins instead. Saving a
    slider and having a forgotten system variable quietly beat it on the next
    start is indistinguishable from the panel not saving at all, and it is not
    a thing anyone would guess. `SF_ENV_OVERRIDE=1` restores the usual rule.
    """
    path = path or env_file()
    loaded: dict[str, str] = {}
    shadowed = os.environ.get("SF_ENV_OVERRIDE", "").strip().lower() in {"1", "true", "yes", "on"}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return loaded

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        if key in os.environ:
            if shadowed or not _panel_owned(key):
                continue
            if os.environ[key] != value:
                log.info("%s: using the saved %r, not the exported %r",
                         key, value, os.environ[key])
        os.environ[key] = value
        loaded[key] = value
    return loaded


load_env()


@dataclass
class Settings:
    # --- paths -----------------------------------------------------------
    root: Path = field(default_factory=lambda: _p("SF_ROOT", "./workspace"), metadata={"env": "SF_ROOT"})
    fonts_dir: Path = field(default_factory=lambda: _p("SF_FONTS", "./assets/fonts"), metadata={"env": "SF_FONTS"})
    motifs_dir: Path = field(default_factory=lambda: _p("SF_MOTIFS", "./assets/motifs"), metadata={"env": "SF_MOTIFS"})

    # --- pipeline --------------------------------------------------------
    preserve_original: bool = field(default_factory=lambda: _b("SF_PRESERVE_ORIGINAL", True), metadata={"env": "SF_PRESERVE_ORIGINAL"})
    max_critique_rounds: int = field(default_factory=lambda: _i("SF_CRITIQUE_ROUNDS", 2), metadata={"env": "SF_CRITIQUE_ROUNDS"})
    ship_threshold: float = field(default_factory=lambda: _f("SF_SHIP_THRESHOLD", 0.85), metadata={"env": "SF_SHIP_THRESHOLD"})
    escalate_threshold: float = field(default_factory=lambda: _f("SF_ESCALATE_THRESHOLD", 0.60), metadata={"env": "SF_ESCALATE_THRESHOLD"})

    # --- making it a new design ------------------------------------------
    # Two levers. `mix` borrows ingredients from your OTHER designs — grid from
    # one, palette from another, decoration from a third — so the result has no
    # single original. `derive_strength` then moves that result further on its
    # own terms. Mixing is the stronger of the two; deriving alone only ever
    # walks away from one starting point.
    mix: float = field(default_factory=lambda: _f("SF_MIX", 0.5), metadata={"env": "SF_MIX"})
    derive_strength: float = field(default_factory=lambda: _f("SF_DERIVE_STRENGTH", 0.5), metadata={"env": "SF_DERIVE_STRENGTH"})
    distinct_threshold: float = field(default_factory=lambda: _f("SF_DISTINCT_THRESHOLD", 0.70), metadata={"env": "SF_DISTINCT_THRESHOLD"})
    max_derive_rounds: int = field(default_factory=lambda: _i("SF_DERIVE_ROUNDS", 2), metadata={"env": "SF_DERIVE_ROUNDS"})

    # --- decoration ------------------------------------------------------
    # How close a library motif has to be before we will place it. Raise it and
    # more designs go to review with a description of what to draw; lower it
    # and you start shipping approximate decoration. A hole is the cheaper
    # mistake, so the default leans towards refusing.
    motif_threshold: float = field(default_factory=lambda: _f("SF_MOTIF_THRESHOLD", 0.45), metadata={"env": "SF_MOTIF_THRESHOLD"})

    # --- fetching ---------------------------------------------------------
    # A long crawl meets a rate limit and a bad gateway whatever time of day it
    # starts. These decide how patient it is before it gives up on one request.
    http_retries: int = field(default_factory=lambda: _i("SF_HTTP_RETRIES", 4), metadata={"env": "SF_HTTP_RETRIES"})
    http_backoff: float = field(default_factory=lambda: _f("SF_HTTP_BACKOFF", 2.0), metadata={"env": "SF_HTTP_BACKOFF"})

    # --- not shipping the same thing twice --------------------------------
    # How alike two finished pages may be, in bits of a 256-bit perceptual hash.
    # Measured over real finished pages: the same file re-encoded is 0, the same
    # page recoloured is 4, and four genuinely different designs sat 40 to 74
    # apart. Twenty is comfortably between the two, with a wide margin either
    # side — raise it to catch more and send more to review, lower it to catch
    # only the obvious.
    duplicate_distance: int = field(default_factory=lambda: _i("SF_DUPLICATE_DISTANCE", 20), metadata={"env": "SF_DUPLICATE_DISTANCE"})

    # --- how many designs at once -----------------------------------------
    # Lanes. One is the original behaviour exactly: a design at a time with a
    # breather between. More than one runs that same loop side by side, each on
    # its own vision model from the Models list. Two lanes against one server
    # just queue behind each other and buy nothing, so raise this only when you
    # have a second model for them to use.
    workers: int = field(default_factory=lambda: _i("SF_WORKERS", 1), metadata={"env": "SF_WORKERS"})

    # --- did we actually rebuild it ---------------------------------------
    # How much of a surface may be placed photograph before we stop calling it
    # a rebuild. A design the model reads as one big photographic area comes
    # back as the original picture with the text set beside it — which passes
    # every other check, because nothing else asks whether anything was
    # actually redrawn.
    max_raster: float = field(default_factory=lambda: _f("SF_MAX_RASTER", 0.40), metadata={"env": "SF_MAX_RASTER"})

    # --- output ----------------------------------------------------------
    preview_px: int = field(default_factory=lambda: _i("SF_PREVIEW_PX", 1400), metadata={"env": "SF_PREVIEW_PX"})

    # --- publishing ------------------------------------------------------
    publish_enabled: bool = field(default_factory=lambda: _b("SF_PUBLISH"), metadata={"env": "SF_PUBLISH"})
    # The provenance check flags designs that lean on third-party library
    # content. By default those stop at an editable master. This sends them
    # through anyway — your catalogue, your call. Either way the flag and its
    # reason stay recorded on every design, so you can always see what went
    # out and what it was marked as.
    publish_all: bool = field(default_factory=lambda: _b("SF_PUBLISH_ALL"), metadata={"env": "SF_PUBLISH_ALL"})

    def reload(self) -> None:
        """Re-read from the environment what the environment actually sets.

        In place because the pipeline, the worker and the panel all hold a
        reference to one Settings. Handing back a new object would leave every
        one of them on the old values, which is indistinguishable from the
        setting having done nothing.

        Only fields the environment names are touched. It used to copy every
        field off a fresh Settings, which threw away anything a caller had
        passed in: a panel started on one workspace moved itself to ./workspace
        the moment you saved a setting, and the designs appeared to vanish.
        """
        fresh = Settings()
        for f in fields(self):
            name = f.metadata.get("env")
            if name and name not in os.environ:
                continue
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
