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
from .openai_compat import OpenAICompatProvider, from_env

_cache: dict[str, VisionProvider] = {}
# What the environment said when each cached provider was built. A provider is
# built once and reused, which is right — but nothing re-read the environment,
# so changing the model or the base URL on the Setup screen changed nothing at
# all. The panel saved it, the health check read os.environ directly and
# reported the new model as reachable, and every design carried on going to the
# old one. Silently, which is the part that matters.
_built_from: dict[str, tuple] = {}
# Everything from_env() looks at. Pinned rather than derived so that adding a
# knob to from_env without adding it here is the thing that breaks the test.
_WATCHED = ("BASE_URL", "MODEL", "API_KEY", "TIMEOUT", "MAX_EDGE")

# Roles a caller installed by hand. set_provider is how tests and embedders
# supply their own, and re-reading the environment must never throw those away.
_pinned: set[str] = set()


def _fingerprint(prefix: str) -> tuple:
    return tuple(os.environ.get(f"{prefix}_{k}") for k in _WATCHED)


def _cached(role: str, prefix: str, build) -> VisionProvider:
    if role in _pinned:
        return _cache[role]
    now = _fingerprint(prefix)
    if role not in _cache or _built_from.get(role) != now:
        _cache[role] = build()
        _built_from[role] = now
    return _cache[role]


def vision() -> VisionProvider:
    return _cached("vision", "SF_VISION", lambda: from_env("SF_VISION"))


def reason() -> VisionProvider:
    """Falls back to the vision model if no separate text model is configured."""
    if not os.environ.get("SF_REASON_MODEL"):
        # No separate text model: follow the vision one, including its changes.
        return vision() if "reason" not in _pinned else _cache["reason"]
    return _cached("reason", "SF_REASON", lambda: from_env("SF_REASON"))


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
    "extract_json", "from_env", "vision", "reason", "set_provider", "reset",
]
