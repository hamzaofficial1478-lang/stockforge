"""The request the program actually makes to your model.

This was the least covered code in the project and the most used: every design
goes through chat(), and nothing had ever executed it. The analysis tests all
stub structured() one level above, so the payload shape, the base64 image
parts, the retry when a server rejects response_format, and the error handling
were all assumption.

So this runs a real HTTP server and lets urllib really talk to it. A mocked
urlopen would test the mock.
"""

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest

from stockforge.providers.base import ProviderError
from stockforge.providers.openai_compat import OpenAICompatProvider, from_env


@pytest.fixture
def image(tmp_path):
    import cv2

    path = tmp_path / "listing.jpg"
    art = np.full((900, 600, 3), 240, dtype=np.uint8)
    cv2.rectangle(art, (100, 200), (500, 400), (90, 120, 80), -1)
    cv2.imwrite(str(path), art)
    return path


class _Server:
    """An OpenAI-compatible endpoint that records what it was sent."""

    def __init__(self, handler):
        self.requests: list[dict] = []
        self.headers: list[dict] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                outer.requests.append(json.loads(body))
                outer.headers.append(dict(self.headers))
                code, payload = handler(len(outer.requests), json.loads(body))
                raw = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]

    def __enter__(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/v1"


def _ok(_n, _body):
    return 200, {"choices": [{"message": {"content": '{"answer": "yes"}'}}]}


@pytest.mark.parametrize("content", [None, '{"partial":'])
def test_truncated_response_retries_once_with_more_room(image, content):
    def reply(n, body):
        if n == 1:
            return 200, {"choices": [{"finish_reason": "length", "message": {"content": content}}]}
        return _ok(n, body)
    with _Server(reply) as s:
        assert OpenAICompatProvider(s.url, "m").chat("s", "u", [image]) == '{"answer": "yes"}'
    assert [r["max_tokens"] for r in s.requests] == [4096, 16384]


def test_repeated_truncation_stops_and_does_not_expose_reasoning(image):
    def reply(n, body):
        return 200, {"choices": [{"finish_reason": "length", "message": {"content": None, "reasoning_content": "internal analysis"}}]}
    with _Server(reply) as s:
        with pytest.raises(ProviderError, match="response limit") as exc:
            OpenAICompatProvider(s.url, "m").chat("s", "u", [image])
    assert len(s.requests) == 2
    assert "internal analysis" not in str(exc.value)


def test_empty_final_answer_has_a_readable_error(image):
    def reply(n, body):
        return 200, {"choices": [{"finish_reason": "stop", "message": {"content": None, "reasoning_content": "internal analysis"}}]}
    with _Server(reply) as s:
        with pytest.raises(ProviderError, match="no final answer") as exc:
            OpenAICompatProvider(s.url, "m").chat("s", "u", [image])
    assert len(s.requests) == 1
    assert "internal analysis" not in str(exc.value)


# --- the happy path ------------------------------------------------------

def test_a_reply_comes_back_as_its_content(image):
    with _Server(_ok) as s:
        p = OpenAICompatProvider(s.url, "a-model")
        assert p.chat("be terse", "what is this", [image]) == '{"answer": "yes"}'


def test_the_payload_is_the_shape_a_server_expects(image):
    with _Server(_ok) as s:
        OpenAICompatProvider(s.url, "a-model").chat("SYSTEM", "USER", [image])
    sent = s.requests[0]

    assert sent["model"] == "a-model"
    assert [m["role"] for m in sent["messages"]] == ["system", "user"]
    assert sent["messages"][0]["content"] == "SYSTEM"

    parts = sent["messages"][1]["content"]
    assert [p["type"] for p in parts] == ["image_url", "text"], \
        "the image has to come before the question"
    assert parts[-1]["text"] == "USER"


def test_the_image_really_is_a_decodable_jpeg(image):
    """A data URI that is not valid base64, or not actually an image, fails at
    the far end where the error is someone else's."""
    import cv2

    with _Server(_ok) as s:
        OpenAICompatProvider(s.url, "m").chat("s", "u", [image])
    url = s.requests[0]["messages"][1]["content"][0]["image_url"]["url"]

    assert url.startswith("data:image/jpeg;base64,")
    raw = base64.standard_b64decode(url.split(",", 1)[1])
    decoded = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    assert decoded is not None, "the model would have been sent something unreadable"
    assert decoded.shape[0] > 0


def test_a_big_image_is_shrunk_before_it_is_sent(tmp_path):
    """A 4000px listing image costs a local model a lot of latency and tells it
    nothing a 1280px one does not."""
    import cv2

    big = tmp_path / "big.jpg"
    cv2.imwrite(str(big), np.full((4000, 3000, 3), 200, dtype=np.uint8))

    with _Server(_ok) as s:
        OpenAICompatProvider(s.url, "m", max_image_edge=1280).chat("s", "u", [big])
    url = s.requests[0]["messages"][1]["content"][0]["image_url"]["url"]
    raw = base64.standard_b64decode(url.split(",", 1)[1])
    sent = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    assert max(sent.shape[:2]) == 1280, sent.shape


def test_several_images_all_go_and_keep_their_order(tmp_path):
    import cv2

    paths = []
    for i in range(3):
        p = tmp_path / f"{i}.jpg"
        cv2.imwrite(str(p), np.full((100, 100, 3), 10 * i + 10, dtype=np.uint8))
        paths.append(p)

    with _Server(_ok) as s:
        OpenAICompatProvider(s.url, "m").chat("s", "u", paths)
    parts = s.requests[0]["messages"][1]["content"]
    assert [p["type"] for p in parts] == ["image_url"] * 3 + ["text"]


# --- json mode and the retry --------------------------------------------

def test_json_mode_is_asked_for_by_default(image):
    with _Server(_ok) as s:
        OpenAICompatProvider(s.url, "m").chat("s", "u", [image])
    assert s.requests[0]["response_format"] == {"type": "json_object"}


def test_a_server_that_rejects_response_format_is_retried_without_it(image):
    """vLLM builds and older NIM images reject it. Failing the design over a
    field the server did not have to honour would lose the whole catalogue on
    the wrong backend."""
    def handler(n, _body):
        if n == 1:
            return 400, {"error": "response_format is not supported by this model"}
        return 200, {"choices": [{"message": {"content": "recovered"}}]}

    with _Server(handler) as s:
        got = OpenAICompatProvider(s.url, "m").chat("s", "u", [image])

    assert got == "recovered"
    assert len(s.requests) == 2, "it did not retry"
    assert "response_format" in s.requests[0]
    assert "response_format" not in s.requests[1], "it retried with the same payload"


def test_it_does_not_retry_forever(image):
    """A server that says response_format on every 400 must not loop."""
    def always_400(_n, _body):
        return 400, {"error": "response_format still no good"}

    with _Server(always_400) as s:
        with pytest.raises(ProviderError):
            OpenAICompatProvider(s.url, "m").chat("s", "u", [image])
    assert len(s.requests) == 2, f"made {len(s.requests)} attempts"


def test_json_mode_can_be_turned_off_at_the_call(image):
    with _Server(_ok) as s:
        OpenAICompatProvider(s.url, "m").chat("s", "u", [image], json_mode=False)
    assert "response_format" not in s.requests[0]


# --- when it goes wrong --------------------------------------------------

def test_an_http_error_names_the_server_and_the_body(image):
    """"400 Bad Request" alone tells you nothing about which of four possible
    servers rejected it or why."""
    def bad(_n, _body):
        return 422, {"error": "context length exceeded"}

    with _Server(bad) as s:
        with pytest.raises(ProviderError) as exc:
            OpenAICompatProvider(s.url, "the-model").chat("s", "u", [image])
    text = str(exc.value)
    assert "422" in text
    assert "the-model" in text
    assert "context length exceeded" in text


def test_a_server_that_is_not_running_says_so_plainly(image):
    """The most common first-run failure: nothing listening on the port."""
    p = OpenAICompatProvider("http://127.0.0.1:9/v1", "m", timeout=5)
    with pytest.raises(ProviderError) as exc:
        p.chat("s", "u", [image])
    assert "unreachable" in str(exc.value)


def test_a_reply_in_an_unexpected_shape_is_reported_with_the_reply(image):
    def odd(_n, _body):
        return 200, {"result": "not what an OpenAI server sends"}

    with _Server(odd) as s:
        with pytest.raises(ProviderError) as exc:
            OpenAICompatProvider(s.url, "m").chat("s", "u", [image])
    assert "odd response" in str(exc.value)
    assert "not what an OpenAI server sends" in str(exc.value)


def test_an_unreadable_image_is_caught_before_the_request(tmp_path):
    bad = tmp_path / "not-an-image.jpg"
    bad.write_text("this is not a jpeg")
    with _Server(_ok) as s:
        with pytest.raises(ProviderError):
            OpenAICompatProvider(s.url, "m").chat("s", "u", [bad])
    assert s.requests == [], "it sent a request it could not fill"


# --- authorisation -------------------------------------------------------

def test_an_api_key_is_sent_as_a_bearer_token(image):
    with _Server(_ok) as s:
        OpenAICompatProvider(s.url, "m", api_key="sk-secret").chat("s", "u", [image])
    assert s.headers[0].get("Authorization") == "Bearer sk-secret"


def test_no_key_means_no_header(image):
    """A local server given an empty Authorization header can reject the call."""
    with _Server(_ok) as s:
        OpenAICompatProvider(s.url, "m").chat("s", "u", [image])
    assert "Authorization" not in s.headers[0]


# --- from_env ------------------------------------------------------------

def test_from_env_reads_every_setting(monkeypatch):
    monkeypatch.setenv("SF_VISION_BASE_URL", "http://localhost:1234/v1/")
    monkeypatch.setenv("SF_VISION_MODEL", "a-model")
    monkeypatch.setenv("SF_VISION_API_KEY", "k")
    monkeypatch.setenv("SF_VISION_TIMEOUT", "45")
    monkeypatch.setenv("SF_VISION_MAX_EDGE", "800")

    p = from_env("SF_VISION")
    assert p.base_url == "http://localhost:1234/v1", "the trailing slash was kept"
    assert p.model == "a-model"
    assert p.api_key == "k"
    assert p.timeout == 45
    assert p.max_image_edge == 800


def test_from_env_without_a_model_says_which_setting_to_set(monkeypatch):
    monkeypatch.delenv("SF_VISION_MODEL", raising=False)
    with pytest.raises(ProviderError) as exc:
        from_env("SF_VISION")
    assert "SF_VISION_MODEL" in str(exc.value)
