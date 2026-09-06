"""The provider registry.

A provider is built once and reused, which is right. Nothing re-read the
environment, though, so changing the vision model on the Setup screen changed
nothing: the panel saved it, the health check read os.environ directly and
reported the new model as reachable, and every design carried on going to the
old one without a word.
"""

import pytest

from stockforge import providers
from stockforge.providers.base import VisionProvider


@pytest.fixture(autouse=True)
def clean_registry():
    providers.reset()
    yield
    providers.reset()


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("SF_VISION_BASE_URL", "http://localhost:8000/v1")
    monkeypatch.setenv("SF_VISION_MODEL", "first-model")
    monkeypatch.delenv("SF_REASON_MODEL", raising=False)
    return monkeypatch


def test_the_same_settings_give_the_same_object(env):
    """Rebuilding per call would be wasteful, and the point of the cache."""
    assert providers.vision() is providers.vision()


def test_changing_the_model_changes_what_the_pipeline_uses(env):
    """What the Setup screen does. It used to keep the old model for the life
    of the process while the check screen reported the new one."""
    before = providers.vision()
    assert before.model == "first-model"

    env.setenv("SF_VISION_MODEL", "second-model")

    after = providers.vision()
    assert after.model == "second-model", "the pipeline is still on the old model"
    assert after is not before


def test_changing_the_base_url_takes_effect_too(env):
    """Moving from a NIM container to Ollama is a base URL change and nothing
    else."""
    assert providers.vision().base_url == "http://localhost:8000/v1"
    env.setenv("SF_VISION_BASE_URL", "http://localhost:11434/v1")
    assert providers.vision().base_url == "http://localhost:11434/v1"


def test_changing_the_api_key_takes_effect(env):
    env.setenv("SF_VISION_API_KEY", "one")
    assert providers.vision().api_key == "one"
    env.setenv("SF_VISION_API_KEY", "two")
    assert providers.vision().api_key == "two"


def test_every_setting_from_env_reads_is_watched(env):
    """The failure this guards against is adding a knob to from_env and not
    here, which brings back exactly the bug: a setting that saves and does
    nothing."""
    import inspect
    import re

    from stockforge.providers import openai_compat

    source = inspect.getsource(openai_compat.from_env)
    # Only the ones it actually looks up, not the ones it names in a message.
    read = set(re.findall(r'environ\.get\(f"\{prefix\}_([A-Z_]+)"', source))
    assert read, "found no settings at all — the extraction is broken"
    assert read <= set(providers._WATCHED), (
        f"from_env reads {read - set(providers._WATCHED)}, which the cache "
        f"does not watch, so changing it would do nothing")


def test_a_provider_installed_by_hand_survives_an_environment_change(env):
    """set_provider is how the tests and the worker supply their own. Re-reading
    the environment must not quietly replace one."""
    class _Mine(VisionProvider):
        name = "mine"

        def chat(self, system, user_text, images, **kw):
            return "{}"

    mine = _Mine()
    providers.set_provider("vision", mine)
    env.setenv("SF_VISION_MODEL", "something-else")
    assert providers.vision() is mine


def test_reason_follows_vision_when_no_text_model_is_set(env):
    """It falls back to the vision model, so it has to follow its changes too."""
    assert providers.reason().model == "first-model"
    env.setenv("SF_VISION_MODEL", "second-model")
    assert providers.reason().model == "second-model"


def test_reason_uses_its_own_model_when_one_is_set(env):
    env.setenv("SF_REASON_MODEL", "small-text-model")
    env.setenv("SF_REASON_BASE_URL", "http://localhost:9000/v1")
    assert providers.reason().model == "small-text-model"
    assert providers.vision().model == "first-model"
