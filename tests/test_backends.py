"""Adding a model backend without touching the program.

The switch was an `if claude else openai`, which says the program is an OpenAI
program with Anthropic bolted on. These tests are the contract that it is not:
a third party writes a module, points a setting at it, and it runs — with no
change to any file in stockforge.
"""

import os
import sys
import textwrap

import pytest

from stockforge import providers
from stockforge.providers import registry
from stockforge.providers.base import ProviderError, VisionProvider


class _Fake(VisionProvider):
    """Stands in for someone else's model."""

    def __init__(self, prefix="SF_VISION", flavour="plain"):
        self.prefix = prefix
        self.flavour = flavour
        self.name = f"fake-{flavour}"

    def chat(self, system, user_text, images, **kw):
        return '{"from": "%s"}' % self.flavour


@pytest.fixture(autouse=True)
def clean():
    """Leave the registry exactly as found — these tests add to it."""
    before = dict(registry._backends)
    providers.reset()
    yield
    registry._backends.clear()
    registry._backends.update(before)
    providers.reset()


@pytest.fixture
def env(monkeypatch):
    for key in ("SF_VISION_BACKEND", "SF_VISION_MODEL", "SF_VISION_BASE_URL",
                "SF_VISION_API_KEY", "SF_REASON_MODEL", "SF_REASON_BACKEND"):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


# --- the built-ins are just entries like any other -----------------------

def test_the_two_that_ship_are_registered():
    assert {"openai", "claude"} <= set(registry.names())


def test_nothing_is_privileged_in_the_lookup():
    """Both built-ins are reached the same way a stranger's would be."""
    for name in ("openai", "claude"):
        assert registry.get(name).name == name


# --- adding one in process -----------------------------------------------

def test_a_registered_backend_can_be_selected(env):
    registry.register(registry.Backend(
        name="pretend", label="Pretend", build=lambda prefix: _Fake(prefix)))
    env.setenv("SF_VISION_BACKEND", "pretend")

    assert providers.backend("SF_VISION") == "pretend"
    assert isinstance(providers.vision(), _Fake)


def test_a_custom_backend_may_replace_a_built_in(env):
    """Registering an existing name deliberately stands in for it — which is
    how you swap the OpenAI client for your own without forking."""
    registry.register(registry.Backend(
        name="openai", label="Mine instead", build=lambda prefix: _Fake(prefix, "mine")))
    env.setenv("SF_VISION_BACKEND", "openai")
    assert providers.vision().flavour == "mine"


def test_a_backends_own_settings_are_watched(env):
    """A backend that adds a setting nothing watches is the silent bug the
    cache fingerprint exists to prevent: you change it and nothing happens."""
    registry.register(registry.Backend(
        name="knobby", label="Knobby",
        build=lambda prefix: _Fake(prefix, os.environ.get("SF_VISION_FLAVOUR", "one")),
        settings=("FLAVOUR",)))
    env.setenv("SF_VISION_BACKEND", "knobby")

    env.setenv("SF_VISION_FLAVOUR", "one")
    assert providers.vision().flavour == "one"
    env.setenv("SF_VISION_FLAVOUR", "two")
    assert providers.vision().flavour == "two", "changing its own setting did nothing"


def test_switching_backend_rebuilds_even_when_nothing_else_changed(env):
    """Guaranteed by BACKEND being one of the watched settings, which is why
    COMMON_SETTINGS includes it and why this test would notice if it stopped."""
    registry.register(registry.Backend(
        name="a", label="A", build=lambda p: _Fake(p, "a")))
    registry.register(registry.Backend(
        name="b", label="B", build=lambda p: _Fake(p, "b")))
    env.setenv("SF_VISION_BACKEND", "a")
    assert providers.vision().flavour == "a"
    env.setenv("SF_VISION_BACKEND", "b")
    assert providers.vision().flavour == "b"


# --- adding one from outside the program ---------------------------------

def _write_backend_module(tmp_path, monkeypatch, name, body):
    (tmp_path / f"{name}.py").write_text(textwrap.dedent(body))
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop(name, None)
    return name


def test_a_module_path_is_imported_and_used(env, tmp_path):
    """The whole extension mechanism: a normal Python module, a dotted path in
    a setting, and no change to any file in stockforge."""
    mod = _write_backend_module(tmp_path, env, "acme_gemini", '''
        from stockforge.providers.base import VisionProvider
        from stockforge.providers.registry import Backend

        class Gemini(VisionProvider):
            name = "acme-gemini"
            def chat(self, system, user_text, images, **kw):
                return '{"said": "hello from acme"}'

        BACKEND = Backend(name="acme-gemini", label="Acme Gemini",
                          build=lambda prefix: Gemini())
    ''')
    env.setenv("SF_VISION_BACKEND", mod)

    provider = providers.vision()
    assert provider.name == "acme-gemini"
    assert provider.chat("s", "u", []) == '{"said": "hello from acme"}'


def test_an_outside_backend_reaches_the_pipeline(env, tmp_path):
    """Registering is not the point; being the thing the analysis passes call
    is. structured() has to work through it like any other."""
    mod = _write_backend_module(tmp_path, env, "acme_answering", '''
        from stockforge.providers.base import VisionProvider
        from stockforge.providers.registry import Backend

        class Answering(VisionProvider):
            name = "acme-answering"
            def chat(self, system, user_text, images, **kw):
                return 'Here you go:\\n```json\\n{"category": "invitation"}\\n```'

        BACKEND = Backend(name="acme-answering", label="Acme",
                          build=lambda prefix: Answering())
    ''')
    env.setenv("SF_VISION_BACKEND", mod)

    from pydantic import BaseModel

    class Small(BaseModel):
        category: str

    got = providers.vision().structured("s", "u", [], Small)
    assert got.category == "invitation"


def test_a_module_that_will_not_import_says_so(env):
    env.setenv("SF_VISION_BACKEND", "no_such_package.nothing")
    with pytest.raises(ProviderError) as exc:
        providers.vision()
    text = str(exc.value)
    assert "no_such_package.nothing" in text, "it does not name what failed"
    assert "failed with" in text, "it does not pass on the import error"


def test_a_module_without_a_backend_says_what_is_missing(env, tmp_path):
    mod = _write_backend_module(tmp_path, env, "acme_empty", '''
        WRONG_NAME = "this module forgot"
    ''')
    env.setenv("SF_VISION_BACKEND", mod)
    with pytest.raises(ProviderError) as exc:
        providers.vision()
    text = str(exc.value)
    assert "BACKEND" in text
    assert "docs/backends.md" in text, "it does not say where to look"


def test_an_unknown_plain_name_lists_what_there_is(env):
    env.setenv("SF_VISION_BACKEND", "wishful")
    with pytest.raises(ProviderError) as exc:
        providers.vision()
    text = str(exc.value)
    assert "openai" in text and "claude" in text
    assert "import path" in text, "it does not say how to add your own"


# --- what the Setup screen is built from ---------------------------------

def test_backends_describe_themselves_for_the_panel():
    listed = {b["name"]: b for b in (x.as_dict() for x in registry.all_backends())}
    assert "openai" in listed and "claude" in listed
    for entry in listed.values():
        assert entry["label"] and entry["doc"], "a backend with no words is unusable in a menu"
        assert isinstance(entry["ready"], bool)


def test_the_openai_backend_offers_somewhere_to_point_it():
    presets = {p["name"]: p for p in registry.get("openai").as_dict()["presets"]}
    assert {"ollama", "openai", "nvidia", "openrouter"} <= set(presets)
    assert presets["openai"]["base_url"] == "https://api.openai.com/v1"
    assert presets["ollama"]["needs_key"] is False
    assert presets["openai"]["needs_key"] is True


def test_a_custom_backend_shows_up_in_the_panel_list(env, tmp_path):
    mod = _write_backend_module(tmp_path, env, "acme_listed", '''
        from stockforge.providers.base import VisionProvider
        from stockforge.providers.registry import Backend

        class P(VisionProvider):
            def chat(self, system, user_text, images, **kw): return "{}"

        BACKEND = Backend(name="acme-listed", label="Acme Listed",
                          doc="A backend from outside.", build=lambda p: P())
    ''')
    registry.get(mod)
    assert "acme-listed" in [b.name for b in registry.all_backends()]


# --- the documentation has to be true ------------------------------------

def test_the_worked_example_in_the_docs_actually_works(tmp_path, env):
    """A backend example that does not run is worse than none — someone copies
    it, it fails, and they conclude the extension point is broken. So the code
    block in docs/backends.md is executed here exactly as written."""
    import re
    from pathlib import Path

    doc = (Path(__file__).resolve().parent.parent / "docs" / "backends.md").read_text()
    blocks = re.findall(r"```python\n(.*?)```", doc, re.S)
    assert blocks, "the backend guide has no python example in it any more"
    example = blocks[0]
    assert "BACKEND = register(" in example, "the example no longer registers a backend"

    mod = _write_backend_module(tmp_path, env, "docs_example_backend", example)
    backend = registry.get(mod)

    assert backend.name == "gemini"
    assert backend.label and backend.doc
    # google-generativeai is not installed here, so ready() must say so rather
    # than throw — which is exactly the behaviour the guide asks for.
    ok, fix = backend.ready()
    assert ok is False
    assert "pip install" in fix, "ready() does not say what to do about it"

    # And the failure path it documents: no key, a ProviderError that names the
    # setting, not a stack trace from inside someone's SDK.
    env.setenv("SF_VISION_API_KEY", "")
    with pytest.raises(ProviderError) as exc:
        backend.build("SF_VISION")
    assert "SF_VISION_API_KEY" in str(exc.value)
