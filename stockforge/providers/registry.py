"""Which model backends exist, and how to add one.

The backend switch started as an `if backend == "claude"` with an `else`. That
is fine for two and wrong for three, and it quietly says the program is an
OpenAI program with Anthropic bolted on — which is not what anyone wants from
something meant to outlive whichever model is best this month.

So a backend is a small object that says what it is called, how to build it,
and whether it can run right now. Two ship with the program:

    openai   anything speaking OpenAI's chat-completions shape. That is most
             things: Ollama, vLLM, NIM, LM Studio, OpenAI itself, OpenRouter,
             Together, Groq. Hermes, Qwen, Llama and the rest are models you
             run *on* one of those, not separate backends.
    claude   Anthropic's own API, because it does not speak that shape.

Adding a third takes no change to this file. Write a module with a BACKEND in
it and point a setting at it:

    SF_VISION_BACKEND=my_company.gemini_backend

Any name that is not already registered is tried as a module to import and
asked for its BACKEND — dotted or not, since `acme_gemini` is as real a module
as `acme.models.gemini`. That is the whole extension mechanism: no plugin
folder to learn, no entry points to register, no subclass to get right. See
`docs/backends.md` for a worked one.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field
from typing import Callable

from .base import ProviderError, VisionProvider

log = logging.getLogger("stockforge.providers.registry")


@dataclass(frozen=True)
class Preset:
    """A server someone might point the OpenAI-compatible backend at.

    Only the address is recorded. What a given model can actually do is not
    guessed at here — the Setup screen's Test button sends a real image and
    finds out, which beats a table that goes stale.
    """

    name: str
    label: str
    base_url: str
    model: str = ""
    needs_key: bool = False
    note: str = ""

    def as_dict(self) -> dict:
        return {"name": self.name, "label": self.label, "base_url": self.base_url,
                "model": self.model, "needs_key": self.needs_key, "note": self.note}


@dataclass(frozen=True)
class Backend:
    """One way of talking to a model.

    build    (prefix) -> a VisionProvider, reading SF_VISION_* or SF_REASON_*
    ready    () -> (usable now?, what to do about it if not)
    settings the per-role suffixes it reads beyond the common ones, so the
             provider cache knows to rebuild when one of them changes. Leave
             it out and changing your backend's own settings will silently do
             nothing — the same bug the cache fingerprint was added for.
    """

    name: str
    label: str
    build: Callable[[str], VisionProvider]
    ready: Callable[[], tuple[bool, str]] = lambda: (True, "")
    settings: tuple[str, ...] = ()
    doc: str = ""
    presets: tuple[Preset, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        ok, fix = self.ready()
        return {"name": self.name, "label": self.label, "doc": self.doc,
                "ready": ok, "fix": fix,
                "presets": [p.as_dict() for p in self.presets]}


# Suffixes every backend is expected to understand, whatever it talks to.
COMMON_SETTINGS = ("BACKEND", "MODEL", "API_KEY", "TIMEOUT", "MAX_TOKENS",
                   "MAX_EDGE", "RETRIES")

_backends: dict[str, Backend] = {}


def register(backend: Backend) -> Backend:
    """Add a backend. Registering the same name twice replaces it, so a custom
    backend can deliberately stand in for a built-in one."""
    _backends[backend.name.lower()] = backend
    return backend


def names() -> list[str]:
    return sorted(_backends)


def all_backends() -> list[Backend]:
    return [_backends[n] for n in names()]


def watched() -> tuple[str, ...]:
    """Every setting suffix any registered backend reads."""
    out = set(COMMON_SETTINGS)
    for b in _backends.values():
        out |= set(b.settings)
    return tuple(sorted(out))


def get(name: str) -> Backend:
    """Find a backend by name, importing it first if it is someone else's.

    Anything not already registered is tried as a module path, dotted or not —
    `acme_gemini` is as valid a module as `acme.models.gemini`, and requiring a
    dot would be an arbitrary rule for someone to trip over. The module is
    imported and its BACKEND taken, so a third party writes ordinary Python and
    nothing here needs to know it exists.
    """
    key = (name or "").strip()
    if key.lower() in _backends:
        return _backends[key.lower()]
    if not key:
        raise ProviderError(
            f"No backend chosen. Built in: {', '.join(names())}."
        )

    try:
        module = importlib.import_module(key)
    except ImportError as exc:
        raise ProviderError(
            f"No backend called {key!r}. Built in: {', '.join(names())}. "
            f"It was also tried as a module to import, which failed with: {exc}. "
            f"For your own backend, install a module with a BACKEND in it and "
            f"put its import path here — see docs/backends.md."
        ) from exc

    found = getattr(module, "BACKEND", None)
    if not isinstance(found, Backend):
        raise ProviderError(
            f"{key} imported, but it has no BACKEND in it. A backend module "
            f"needs a module-level BACKEND = Backend(...). See docs/backends.md."
        )
    log.info("loaded backend %r from %s", found.name, key)
    return register(found)
