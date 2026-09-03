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


def vision() -> VisionProvider:
    if "vision" not in _cache:
        _cache["vision"] = from_env("SF_VISION")
    return _cache["vision"]


def reason() -> VisionProvider:
    """Falls back to the vision model if no separate text model is configured."""
    if "reason" not in _cache:
        _cache["reason"] = from_env("SF_REASON") if os.environ.get("SF_REASON_MODEL") else vision()
    return _cache["reason"]


def set_provider(role: str, provider: VisionProvider) -> None:
    """For tests, and for anyone wiring this into something bigger."""
    _cache[role] = provider


__all__ = [
    "ProviderError", "VisionProvider", "OpenAICompatProvider",
    "extract_json", "from_env", "vision", "reason", "set_provider",
]
