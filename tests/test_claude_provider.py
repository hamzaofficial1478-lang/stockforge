"""Claude as the model backend, signed in rather than keyed in.

Runs a real HTTP server speaking the Anthropic Messages shape and lets the real
SDK talk to it, for the same reason the OpenAI-compatible tests do: a mocked
client tests the mock, and the whole point of this backend is the wire format.
"""

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest

from stockforge import providers
from stockforge.providers.base import ProviderError

anthropic = pytest.importorskip("anthropic")

from stockforge.providers.claude import ClaudeProvider, available  # noqa: E402


@pytest.fixture
def image(tmp_path):
    import cv2

    path = tmp_path / "invite.jpg"
    art = np.full((900, 600, 3), 240, dtype=np.uint8)
    cv2.rectangle(art, (100, 200), (500, 400), (90, 120, 80), -1)
    cv2.imwrite(str(path), art)
    return path


def _reply(text="{}", stop_reason="end_turn", **extra):
    return {
        "id": "msg_test", "type": "message", "role": "assistant",
        "model": "claude-opus-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 11, "output_tokens": 7},
        **extra,
    }


class _Server:
    def __init__(self, handler):
        self.requests: list[dict] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                outer.requests.append(json.loads(body))
                code, payload = handler(len(outer.requests))
                raw = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)

    def __enter__(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"


@pytest.fixture
def signed_in(monkeypatch):
    """Stand in for a signed-in machine without touching the real profile."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    yield


# --- the sign-in question ------------------------------------------------

def test_a_signed_in_profile_counts_as_credentials(monkeypatch, tmp_path):
    """The point of the whole backend: no key pasted anywhere, and it still
    runs. `ant auth login` leaves a profile and the SDK picks it up."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    profile = tmp_path / "anthropic"
    profile.mkdir()
    (profile / "profile.json").write_text("{}")
    monkeypatch.setenv("ANTHROPIC_CONFIG_DIR", str(profile))

    ok, fix = available()
    assert ok is True, "a signed-in profile was not recognised"
    assert fix == ""


def test_no_credentials_at_all_says_how_to_sign_in(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_CONFIG_DIR", str(tmp_path / "nothing-here"))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "no-home")

    ok, fix = available()
    assert ok is False
    assert "ant auth login" in fix, "it does not say how to sign in"

    with pytest.raises(ProviderError) as exc:
        ClaudeProvider()
    assert "ant auth login" in str(exc.value)


def test_the_client_is_never_handed_a_key_of_our_own(signed_in, monkeypatch):
    """Passing api_key= would shadow the signed-in profile, which is exactly
    the behaviour this backend exists to avoid."""
    seen = {}
    real = anthropic.Anthropic

    def spy(*a, **kw):
        seen.update(kw)
        return real(*a, **kw)

    monkeypatch.setattr(anthropic, "Anthropic", spy)
    ClaudeProvider()
    assert "api_key" not in seen, "stockforge passed a key and shadowed the profile"
    assert "auth_token" not in seen


# --- the request it actually makes ---------------------------------------

def test_an_image_goes_as_a_base64_image_block(signed_in, monkeypatch, image):
    with _Server(lambda n: (200, _reply('{"answer":"yes"}'))) as s:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", s.url)
        got = ClaudeProvider().chat("be terse", "what is this", [image])

    assert got == '{"answer":"yes"}'
    sent = s.requests[0]
    assert sent["system"] == "be terse"
    blocks = sent["messages"][0]["content"]
    assert blocks[0]["type"] == "image"
    assert blocks[0]["source"]["media_type"] == "image/jpeg"
    assert base64.b64decode(blocks[0]["source"]["data"])[:2] == b"\xff\xd8"
    assert blocks[-1] == {"type": "text", "text": "what is this"}


def test_several_images_keep_their_order(signed_in, monkeypatch, tmp_path, image):
    import cv2

    second = tmp_path / "b.jpg"
    cv2.imwrite(str(second), np.full((400, 400, 3), 10, dtype=np.uint8))

    with _Server(lambda n: (200, _reply())) as s:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", s.url)
        ClaudeProvider().chat("s", "u", [image, second])

    blocks = s.requests[0]["messages"][0]["content"]
    assert [b["type"] for b in blocks] == ["image", "image", "text"]
    assert blocks[0]["source"]["data"] != blocks[1]["source"]["data"]


def test_effort_is_low_by_default(signed_in, monkeypatch, image):
    """Five passes across five thousand designs. The default has to be the
    cheap one, and it has to be changeable."""
    with _Server(lambda n: (200, _reply())) as s:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", s.url)
        ClaudeProvider().chat("s", "u", [image])
        ClaudeProvider(effort="high").chat("s", "u", [image])

    assert s.requests[0]["output_config"]["effort"] == "low"
    assert s.requests[1]["output_config"]["effort"] == "high"


# --- when it goes wrong --------------------------------------------------

def test_a_refusal_is_reported_not_retried(signed_in, monkeypatch, image):
    """A refusal is a 200 with no usable text. Read it as an empty reply and
    the repair loop spends three more calls learning the same thing."""
    refusal = _reply("", stop_reason="refusal")
    refusal["stop_details"] = {"type": "refusal", "category": "cyber",
                               "explanation": "not something I can help with"}

    with _Server(lambda n: (200, refusal)) as s:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", s.url)
        with pytest.raises(ProviderError) as exc:
            ClaudeProvider().chat("s", "u", [image])

    assert len(s.requests) == 1, "it retried a refusal"
    assert "declined" in str(exc.value)


def test_a_bad_sign_in_says_to_sign_in_again(signed_in, monkeypatch, image):
    err = {"type": "error", "error": {"type": "authentication_error", "message": "bad key"}}
    with _Server(lambda n: (401, err)) as s:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", s.url)
        with pytest.raises(ProviderError) as exc:
            ClaudeProvider().chat("s", "u", [image])
    assert "ant auth login" in str(exc.value)


def test_an_empty_reply_is_an_error_not_an_empty_spec(signed_in, monkeypatch, image):
    with _Server(lambda n: (200, _reply(""))) as s:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", s.url)
        with pytest.raises(ProviderError):
            ClaudeProvider().chat("s", "u", [image])


# --- choosing the backend ------------------------------------------------

def test_a_claude_model_id_selects_the_claude_backend(monkeypatch):
    """Someone who typed a Claude model into Setup has already said what they
    meant; they should not also have to find a switch."""
    monkeypatch.setenv("SF_VISION_MODEL", "claude-opus-5")
    monkeypatch.delenv("SF_VISION_BACKEND", raising=False)
    assert providers.backend("SF_VISION") == "claude"


def test_anything_else_stays_on_the_local_backend(monkeypatch):
    monkeypatch.setenv("SF_VISION_MODEL", "meta/muse-glimmer-30b")
    monkeypatch.delenv("SF_VISION_BACKEND", raising=False)
    assert providers.backend("SF_VISION") == "openai"


def test_the_backend_can_be_named_outright(monkeypatch):
    monkeypatch.setenv("SF_VISION_MODEL", "meta/muse-glimmer-30b")
    monkeypatch.setenv("SF_VISION_BACKEND", "claude")
    assert providers.backend("SF_VISION") == "claude"


def test_switching_backend_rebuilds_the_provider(monkeypatch, signed_in):
    """The cache watches the environment. A backend it did not watch would be
    the same silent bug the model-change fingerprint was added for."""
    providers.reset()
    monkeypatch.setenv("SF_VISION_BASE_URL", "http://localhost:8000/v1")
    monkeypatch.setenv("SF_VISION_MODEL", "meta/muse-glimmer-30b")
    monkeypatch.delenv("SF_VISION_BACKEND", raising=False)
    first = providers.vision()

    monkeypatch.setenv("SF_VISION_BACKEND", "claude")
    monkeypatch.setenv("SF_VISION_MODEL", "claude-opus-5")
    second = providers.vision()

    assert type(first) is not type(second), "changing backend changed nothing"
    providers.reset()
