"""Provider registry.

Two roles, because they want different models:

  vision     reads images. Needs a multimodal model.
  reason     writes titles, keywords, and judges whether a rebuild is
             sufficiently its own design. Text only, so a smaller and faster
             model is usually the right call.

Point them at the same server if you only run one.
"""

from __future__ import annotations

import os

from .base import ProviderError, VisionProvider, extract_json
from .registry import Backend, Preset, all_backends, get, names, register, watched
# Importing these registers them. A third-party backend registers itself when
# its module path is imported on demand — see registry.get().
from .openai_compat import OpenAICompatProvider  # noqa: F401  (registers "openai")
from . import claude as _claude  # noqa: F401     (registers "claude")

_cache: dict[str, VisionProvider] = {}
# What the environment said when each cached provider was built. A provider is
# built once and reused, which is right — but nothing re-read the environment,
# so changing the model or the base URL on the Setup screen changed nothing at
# all. The panel saved it, the health check read os.environ directly and
# reported the new model as reachable, and every design carried on going to the
# old one. Silently, which is the part that matters.
_built_from: dict[str, tuple] = {}
# Everything any registered backend reads. Derived from what each one declares
# rather than written out here, so a backend that adds a setting and forgets to
# declare it is the thing that breaks — not something you find out months later
# when changing it silently does nothing.
_WATCHED = watched()

# Roles a caller installed by hand. set_provider is how tests and embedders
# supply their own, and re-reading the environment must never throw those away.
_pinned: set[str] = set()


# A model id that names its own backend. Someone who typed "claude-opus-5" into
# Setup has already said what they meant and should not also have to find a
# switch. Kept deliberately short: a guess that is wrong is worse than no guess,
# and SF_*_BACKEND always wins over it.
_IMPLIED = (("claude-", "claude"),)


def backend(prefix: str = "SF_VISION") -> str:
    """The name of the backend a role is pointed at."""
    named = (os.environ.get(f"{prefix}_BACKEND") or "").strip()
    if named:
        return named.lower() if "." not in named else named
    model = (os.environ.get(f"{prefix}_MODEL") or "").strip().lower()
    for start, name in _IMPLIED:
        if model.startswith(start):
            return name
    return "openai"


def resolve(prefix: str = "SF_VISION") -> Backend:
    """The backend object for a role, importing a third-party one if needed."""
    return get(backend(prefix))


def from_env(prefix: str = "SF_VISION") -> VisionProvider:
    """Build whichever backend this role is configured for."""
    return resolve(prefix).build(prefix)


def _fingerprint(prefix: str, watch: tuple[str, ...]) -> tuple:
    return tuple(os.environ.get(f"{prefix}_{k}") for k in watch)


def _cached(role: str, prefix: str) -> VisionProvider:
    if role in _pinned:
        return _cache[role]
    # Resolve first: loading a third-party backend is what tells us which extra
    # settings to watch, so it has to happen before the fingerprint is taken.
    chosen = resolve(prefix)
    # BACKEND is itself watched, so switching backend changes the fingerprint
    # on its own — no need to fold the resolved name in as well.
    watch = tuple(sorted(set(_WATCHED) | set(chosen.settings)))
    now = _fingerprint(prefix, watch)
    if role not in _cache or _built_from.get(role) != now:
        _cache[role] = chosen.build(prefix)
        _built_from[role] = now
    return _cache[role]


def vision() -> VisionProvider:
    return _cached("vision", "SF_VISION")


def reason() -> VisionProvider:
    """Falls back to the vision model if no separate text model is configured."""
    if not os.environ.get("SF_REASON_MODEL"):
        # No separate text model: follow the vision one, including its changes.
        return vision() if "reason" not in _pinned else _cache["reason"]
    return _cached("reason", "SF_REASON")


def set_provider(role: str, provider: VisionProvider) -> None:
    """For tests, and for anyone wiring this into something bigger."""
    _cache[role] = provider
    _pinned.add(role)


def reset() -> None:
    """Forget everything, pinned providers included. For test isolation."""
    _cache.clear()
    _built_from.clear()
    _pinned.clear()


__all__ = [
    "ProviderError", "VisionProvider", "OpenAICompatProvider",
    "Backend", "Preset", "register", "get", "names", "all_backends",
    "backend", "resolve", "extract_json", "from_env", "vision", "reason",
    "set_provider", "reset",
]
