import base64
import io
from PIL import Image
from stockforge.ui import models
from stockforge.providers.openai_compat import OpenAICompatProvider
from test_provider_http import _Server, _ok, image


def test_muse_uses_documented_sampling_and_low_reasoning(image):
    with _Server(_ok) as server:
        OpenAICompatProvider(server.url, "meta/muse-glimmer-30b").chat("s", "u", [image])
    request = server.requests[0]
    assert request["temperature"] == 0.95
    assert request["top_p"] == 1.0
    assert request["reasoning_effort"] == "low"


def test_vision_probe_sends_a_real_image_and_checks_the_answer(monkeypatch):
    monkeypatch.setattr(models.secrets, "choice", lambda choices: choices[0])
    monkeypatch.setattr(models.secrets, "randbelow", lambda maximum: 12345)
    def request(url, key, payload, timeout):
        parts = payload["messages"][0]["content"]
        raw = base64.b64decode(parts[0]["image_url"]["url"].split(",")[1])
        image = Image.open(io.BytesIO(raw))
        assert image.size == (320, 160)
        assert image.getpixel((0, 0)) == (221, 48, 48)
        assert "22345" not in parts[1]["text"]
        return {"choices": [{"message": {"content": '{"color":"red","text":"22345"}'}}]}
    monkeypatch.setattr(models, "_request", request)
    result = models.test("http://test", "model", role="vision")
    assert result["ok"] and "Full designs" in result["scope"]


def test_vision_probe_rejects_a_fast_but_wrong_answer(monkeypatch):
    monkeypatch.setattr(models, "_request", lambda *args: {"choices": [{"message": {"content": '{"color":"purple","text":"wrong"}'}}]})
    result = models.test("http://test", "model", role="vision")
    assert not result["ok"]


def test_text_probe_states_its_limits(monkeypatch):
    monkeypatch.setattr(models, "_request", lambda *args: {"choices": [{"message": {"content": 'ready'}}]})
    result = models.test("http://test", "model", role="text")
    assert result["ok"] and "not tested" in result["scope"]
