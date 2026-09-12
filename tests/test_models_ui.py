"""Saved model connections.

Setup had four bare text boxes and no way to find out whether what you typed
into them worked. A wrong base URL, or a model name your server does not
serve, was discovered on the first design of a five thousand design run.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from stockforge.ui import models as store


@pytest.fixture
def server():
    """An OpenAI-compatible server, and a switch to make it misbehave."""
    state = {"models": ["llama-3.2-vision", "qwen2.5vl:7b"], "chat": 200,
             "auth": [], "asked": []}

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _send(self, code, payload):
            raw = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            state["asked"].append(self.path)
            state["auth"].append(self.headers.get("Authorization"))
            if self.path.endswith("/models"):
                if state["models"] is None:
                    return self._send(200, {"object": "list"})
                return self._send(200, {"data": [{"id": m} for m in state["models"]]})
            self._send(404, {"error": "no"})

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            state["asked"].append(self.path)
            if state["chat"] != 200:
                return self._send(state["chat"], {"error": "that model is not loaded"})
            self._send(200, {"choices": [{"message": {"content": "ready"}}]})

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    state["url"] = f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    threading.Thread(target=httpd.serve_forever,
                     kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        yield state
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- asking a server what it serves --------------------------------------

def test_the_models_a_server_has_are_listed(server):
    """So you pick from a list instead of typing a name from memory and finding
    out it is wrong on design one."""
    got = store.fetch(server["url"])
    assert got["models"] == ["llama-3.2-vision", "qwen2.5vl:7b"]


def test_a_url_with_nothing_behind_it_says_so_plainly(server):
    got = store.fetch("http://127.0.0.1:9/v1")
    assert "error" in got
    assert "nothing answered" in got["error"]


def test_a_server_that_answers_with_something_else_is_not_mistaken_for_models(server):
    server["models"] = None
    got = store.fetch(server["url"])
    assert "error" in got and "not with a list" in got["error"]


def test_a_key_is_sent_when_there_is_one(server):
    store.fetch(server["url"], api_key="sk-abc")
    assert server["auth"][-1] == "Bearer sk-abc"


def test_no_key_means_no_header(server):
    store.fetch(server["url"])
    assert server["auth"][-1] is None


# --- testing one -----------------------------------------------------------

def test_a_working_model_reports_what_it_said_and_how_long_it_took(server):
    """Not a ping. A server can be up and reachable and still not serve the
    model you named."""
    got = store.test(server["url"], "llama-3.2-vision")
    assert got["ok"] is True
    assert got["said"] == "ready"
    assert got["seconds"] >= 0


def test_a_model_the_server_will_not_serve_reports_the_servers_own_words(server):
    server["chat"] = 404
    got = store.test(server["url"], "not-loaded")
    assert got["ok"] is False
    assert "404" in got["error"]
    assert "not loaded" in got["error"]


def test_testing_without_a_model_name_is_refused_before_the_request(server):
    got = store.test(server["url"], "")
    assert got["ok"] is False
    assert server["asked"] == []


# --- keeping them ----------------------------------------------------------

def test_a_connection_is_saved_and_comes_back(tmp_path):
    saved = store.upsert(tmp_path, {"label": "Local NIM", "model": "a-model",
                                    "base_url": "http://localhost:8000/v1"})
    assert [c.id for c in store.load(tmp_path)] == [saved.id]
    assert store.load(tmp_path)[0].label == "Local NIM"


def test_editing_one_changes_it_rather_than_adding_another(tmp_path):
    first = store.upsert(tmp_path, {"label": "One", "model": "a"})
    store.upsert(tmp_path, {"id": first.id, "label": "Renamed", "model": "b"})
    saved = store.load(tmp_path)
    assert len(saved) == 1
    assert (saved[0].label, saved[0].model) == ("Renamed", "b")


def test_an_edit_does_not_wipe_the_key_it_was_never_shown(tmp_path):
    """The browser is never sent the real key, so an edit posts an empty one.
    Taking that literally would log you out of a hosted endpoint on every
    rename."""
    first = store.upsert(tmp_path, {"label": "Hosted", "model": "a",
                                    "api_key": "sk-real"})
    store.upsert(tmp_path, {"id": first.id, "label": "Renamed", "api_key": ""})
    assert store.load(tmp_path)[0].api_key == "sk-real"


def test_the_key_is_never_handed_back_to_the_browser(tmp_path):
    saved = store.upsert(tmp_path, {"model": "a", "api_key": "sk-real"})
    public = saved.public()
    assert public["api_key"] == ""
    assert public["has_key"] is True
    assert "sk-real" not in json.dumps(public)


def test_deleting_one_leaves_the_others(tmp_path):
    a = store.upsert(tmp_path, {"label": "A", "model": "a"})
    b = store.upsert(tmp_path, {"label": "B", "model": "b"})
    assert store.remove(tmp_path, a.id) is True
    assert [c.id for c in store.load(tmp_path)] == [b.id]
    assert store.remove(tmp_path, "nope") is False


def test_several_reading_models_can_be_live_at_once(tmp_path):
    """This used to stand the first one down, which quietly made the second
    lane pointless: it had no model of its own to use. Reading is the one role
    where more than one is useful, because the lanes hand a different model to
    each."""
    a = store.upsert(tmp_path, {"label": "A", "model": "a", "role": "vision"})
    b = store.upsert(tmp_path, {"label": "B", "model": "b", "role": "vision"})
    store.activate(tmp_path, a.id)
    store.activate(tmp_path, b.id)
    assert {c.id for c in store.live(tmp_path, "vision")} == {a.id, b.id}


def test_making_one_writer_live_still_stands_the_others_down(tmp_path):
    """Nothing splits the writing work, so a second live one is only
    ambiguous. Same for drawing."""
    for role in ("text", "image"):
        a = store.upsert(tmp_path, {"label": "A", "model": f"a-{role}", "role": role})
        b = store.upsert(tmp_path, {"label": "B", "model": f"b-{role}", "role": role})
        store.activate(tmp_path, a.id)
        store.activate(tmp_path, b.id)
        assert [c.id for c in store.live(tmp_path, role)] == [b.id], role


def test_a_reading_model_can_be_stood_down_again(tmp_path):
    a = store.upsert(tmp_path, {"label": "A", "model": "a", "role": "vision"})
    b = store.upsert(tmp_path, {"label": "B", "model": "b", "role": "vision"})
    store.activate(tmp_path, a.id)
    store.activate(tmp_path, b.id)
    store.deactivate(tmp_path, a.id)
    assert [c.id for c in store.live(tmp_path, "vision")] == [b.id]


def test_a_text_model_going_live_does_not_stand_down_the_vision_one(tmp_path):
    """They are two different jobs and both are live at once."""
    vision = store.upsert(tmp_path, {"model": "v", "role": "vision"})
    text = store.upsert(tmp_path, {"model": "t", "role": "text"})
    store.activate(tmp_path, vision.id)
    store.activate(tmp_path, text.id)
    live = {c.role for c in store.load(tmp_path) if c.active}
    assert live == {"vision", "text"}


def test_going_live_writes_the_settings_the_pipeline_reads(tmp_path):
    c = store.upsert(tmp_path, {"model": "a-model", "role": "vision",
                                "base_url": "http://localhost:1234/v1",
                                "api_key": "sk-x"})
    assert store.env_for(c) == {
        "SF_VISION_BACKEND": "openai",
        "SF_VISION_BASE_URL": "http://localhost:1234/v1",
        "SF_VISION_MODEL": "a-model",
        "SF_VISION_API_KEY": "sk-x"}
    assert store.env_for(store.upsert(tmp_path, {"model": "t", "role": "text"}))[
        "SF_REASON_MODEL"] == "t"


def test_a_signed_in_connection_needs_no_server_and_no_key(tmp_path):
    """The owner's ask: add a model by signing in, not by pasting a key. A
    connection on a signed-in backend has no server to point at."""
    c = store.upsert(tmp_path, {"model": "claude-opus-5", "role": "vision",
                                "backend": "claude"})
    assert store.env_for(c) == {
        "SF_VISION_BACKEND": "claude",
        "SF_VISION_MODEL": "claude-opus-5",
        "SF_VISION_BASE_URL": "",
        "SF_VISION_API_KEY": ""}


def test_going_live_on_a_signed_in_backend_clears_an_old_key(tmp_path):
    """A key left from a previous connection would shadow the sign-in, which
    is the one thing the signed-in route exists to avoid."""
    c = store.upsert(tmp_path, {"model": "claude-opus-5", "role": "vision",
                                "backend": "claude", "api_key": "sk-stale",
                                "base_url": "http://localhost:1234/v1"})
    env = store.env_for(c)
    assert env["SF_VISION_API_KEY"] == "", "a stale key would shadow the sign-in"
    assert env["SF_VISION_BASE_URL"] == "", "a stale server address was left behind"


def test_a_corrupt_file_does_not_take_the_panel_down_with_it(tmp_path):
    (tmp_path / store.FILE).write_text("{ not json")
    assert store.load(tmp_path) == []


# --- the image role --------------------------------------------------------
#
# The owner pointed a connection at qwen-image-3.0 and got back:
#
#   Test failed: an odd reply: {'choices': [{'message': {'role': 'assistant'},
#   'finish_reason': 'stop', ...}], 'model': 'qwen-image-3.0', ...}
#
# That was a 200. The call worked, the model answered, and the probe rejected
# it — because an image model's message carries no text, and the probe only
# knew how to look for words.

def _image_reply(url="https://example.test/out.png"):
    return {"choices": [{"message": {"role": "assistant", "images": [{"url": url}]},
                         "finish_reason": "stop", "index": 0}],
            "model": "qwen-image-3.0"}


def test_an_image_model_is_tested_by_asking_for_a_picture(monkeypatch):
    seen = {}

    def fake(url, key, payload, timeout):
        seen.update(payload=payload, url=url)
        return _image_reply()

    monkeypatch.setattr(store, "_request", fake)
    result = store.test("http://x/v1", "qwen-image-3.0", role="image")

    assert result["ok"], result
    assert "picture" in result["scope"].lower() or "image" in result["scope"].lower()
    # it asked for something to draw, not for the word "ready"
    assert "ready" not in str(seen["payload"]["messages"]).lower()


def test_the_reply_that_used_to_be_called_odd_now_passes(monkeypatch):
    """The owner's exact envelope, with an image where the text would be."""
    monkeypatch.setattr(store, "_request", lambda *a: _image_reply())
    assert store.test("http://x/v1", "qwen-image-3.0", role="image")["ok"]


def test_an_image_model_that_returns_no_image_is_a_failure(monkeypatch):
    """Not everything that answers has drawn something. A reply with no image
    anywhere in it is the one case that should still fail — and the message
    has to show what did come back, or it is the old error again."""
    monkeypatch.setattr(store, "_request",
                        lambda *a: {"choices": [{"message": {"role": "assistant"}}]})
    result = store.test("http://x/v1", "qwen-image-3.0", role="image")
    assert not result["ok"]
    assert "no image" in result["error"]
    assert "assistant" in result["error"], "it does not show what came back"


def test_the_image_is_found_wherever_the_server_puts_it(monkeypatch):
    """Servers disagree about where the image goes. Looking beats assuming."""
    shapes = [
        {"choices": [{"message": {"images": [{"url": "https://a.test/1.png"}]}}]},
        {"data": [{"url": "https://b.test/2.png"}]},
        {"output": {"results": [{"url": "https://c.test/3.png"}]}},
        {"choices": [{"message": {"content": [{"image_url": "data:image/png;base64,AAA"}]}}]},
    ]
    for body in shapes:
        assert store._find_image(body), f"missed the image in {body}"


def test_an_image_connection_gets_its_own_settings(tmp_path):
    """Three roles, three prefixes. An image model written into SF_VISION_*
    would replace the model that reads the designs."""
    c = store.upsert(tmp_path, {"model": "qwen-image-3.0", "role": "image",
                                "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                                "api_key": "sk-x"})
    env = store.env_for(c)
    assert env["SF_IMAGE_MODEL"] == "qwen-image-3.0"
    assert env["SF_IMAGE_BASE_URL"].endswith("/compatible-mode/v1")
    assert not any(k.startswith("SF_VISION") for k in env), "it overwrote the reading model"


def test_the_three_roles_do_not_tread_on_each_other(tmp_path):
    prefixes = set()
    for role in ("vision", "text", "image"):
        c = store.upsert(tmp_path, {"model": f"m-{role}", "role": role})
        prefixes.add(next(k.rsplit("_", 1)[0] for k in store.env_for(c) if k.endswith("_MODEL")))
    assert prefixes == {"SF_VISION", "SF_REASON", "SF_IMAGE"}


# --- content as parts, and the wrong "Used for" ---------------------------
#
# The owner saved qwen-image-3.0 as "writes text", made it live, and pressed
# Test. Two faults, one theirs and one mine:
#
#   HTTP 400 — {"error":{"message":"Input should be a valid list:
#               input.messages.0.content", ...}}
#
# Qwen wants content as a list of parts. My own image probe sent a bare string
# too, so choosing the right role would have failed in exactly the same way —
# the probe had the fault it exists to catch.

QWEN_LIST_ERROR = ('{"error":{"message":"Input should be a valid list: '
                   'input.messages.0.content","type":"invalid_request_error",'
                   '"code":"invalid_parameter_error"}}')


def test_the_image_probe_sends_content_as_parts(monkeypatch):
    sent = {}
    monkeypatch.setattr(store, "_request",
                        lambda url, key, payload, timeout: sent.update(payload) or _image_reply())
    assert store.test("http://x/v1", "qwen-image-3.0", role="image")["ok"]
    content = sent["messages"][0]["content"]
    assert isinstance(content, list), (
        "a bare string here is the exact 400 the owner hit")
    assert content[0]["type"] == "text"


def test_an_image_model_saved_as_a_text_model_says_so(monkeypatch):
    """The live text role pointed at an image model would send every title and
    keyword to something that answers in pictures. Caught before the request,
    because the server's own complaint is about JSON shape and mentions
    nothing a person actually did wrong."""
    monkeypatch.setattr(store, "_request",
                        lambda *a: pytest.fail("it should not have asked the server"))
    result = store.test("http://x/v1", "qwen-image-3.0", role="text")
    assert not result["ok"]
    assert "draws pictures" in result["error"]
    assert "Drawing artwork" in result["error"], "it does not say what to change"


def test_the_same_guard_covers_the_reading_role(monkeypatch):
    monkeypatch.setattr(store, "_request", lambda *a: pytest.fail("no request expected"))
    result = store.test("http://x/v1", "wan2.7-image-pro", role="vision")
    assert not result["ok"]
    assert "reading designs" in result["error"]


@pytest.mark.parametrize("model,draws", [
    ("qwen-image-3.0", True), ("qwen-image-edit-plus", True),
    ("wan2.7-image-pro", True), ("z-image-turbo", True),
    ("qwen3-vl-plus", False), ("qwen-vl-max", False),
    ("qwen-vl-ocr", False), ("meta/muse-glimmer-30b", False),
    ("qwen3.8-flash", False),
])
def test_drawing_models_are_told_from_reading_ones(model, draws):
    """A vision model reads and must never be mistaken for one that draws —
    that would block the owner's actual reading models from being saved."""
    assert store.looks_like_an_image_model(model) is draws, model


def test_a_text_model_that_wants_parts_is_retried_rather_than_failed(monkeypatch):
    """Some servers take only the list form. Sending a string first and parts
    on the rebound works everywhere, and means nobody has to be told what
    "input.messages.0.content" means."""
    import io
    import urllib.error

    calls = []

    def fake(url, key, payload, timeout):
        calls.append(payload)
        if isinstance(payload["messages"][0]["content"], str):
            raise urllib.error.HTTPError(url, 400, "Bad Request", {},
                                         io.BytesIO(QWEN_LIST_ERROR.encode()))
        return {"choices": [{"message": {"content": "ready"}}]}

    monkeypatch.setattr(store, "_request", fake)
    result = store.test("http://x/v1", "qwen3.8-flash", role="text")

    assert result["ok"], result
    assert len(calls) == 2, "it did not retry"
    assert isinstance(calls[1]["messages"][0]["content"], list)


def test_a_400_that_is_not_about_shape_is_reported_as_it_came(monkeypatch):
    """Only that one complaint earns a retry. Everything else is a real answer
    and has to reach the user with its body intact."""
    import io
    import urllib.error

    body = '{"error":{"message":"model not found"}}'

    def fake(url, key, payload, timeout):
        raise urllib.error.HTTPError(url, 400, "Bad Request", {}, io.BytesIO(body.encode()))

    monkeypatch.setattr(store, "_request", fake)
    result = store.test("http://x/v1", "qwen3.8-flash", role="text")
    assert not result["ok"]
    assert "model not found" in result["error"], "the body was eaten by the retry"


# --- the size format -------------------------------------------------------
#
# With content fixed, the next layer:
#
#   HTTP 400 — {"error":{"message":"Expected format: '<width>*<height>'", ...}}
#
# Qwen wants 512*512, OpenAI's images API wants 512x512. Rather than keeping a
# list of which vendor uses which — wrong the moment somebody adds a third —
# the server names the separator in its complaint and that is what gets used.

QWEN_SIZE_ERROR = ('{"error":{"message":"Expected format: \'<width>*<height>\'",'
                   '"type":"invalid_request_error","code":"invalid_parameter_error"}}')


@pytest.mark.parametrize("message,expected", [
    ("Expected format: '<width>*<height>'", "*"),
    ("Expected format: '<width>x<height>'", "x"),
    ("Expected format: <width> X <height>", "X"),
    ("model not found", ""),
    ("", ""),
])
def test_the_separator_is_read_from_the_servers_own_words(message, expected):
    assert store.size_separator(message) == expected


def test_a_server_that_wants_an_asterisk_gets_one(monkeypatch):
    """The owner's second 400, end to end."""
    import io
    import urllib.error

    sizes = []

    def fake(url, key, payload, timeout):
        sizes.append(payload["size"])
        if "*" not in payload["size"]:
            raise urllib.error.HTTPError(url, 400, "Bad Request", {},
                                         io.BytesIO(QWEN_SIZE_ERROR.encode()))
        return _image_reply()

    monkeypatch.setattr(store, "_request", fake)
    result = store.test("http://x/v1", "qwen-image-3.0", role="image")

    assert result["ok"], result
    assert sizes == ["512x512", "512*512"], sizes


def test_the_dimensions_survive_the_swap(monkeypatch):
    """Only the separator changes. Swapping the numbers too would be a very
    quiet way to start generating the wrong shape."""
    import io
    import urllib.error

    seen = []

    def fake(url, key, payload, timeout):
        seen.append(payload["size"])
        if "*" not in payload["size"]:
            raise urllib.error.HTTPError(url, 400, "Bad Request", {},
                                         io.BytesIO(QWEN_SIZE_ERROR.encode()))
        return _image_reply()

    monkeypatch.setattr(store, "_request", fake)
    store.test("http://x/v1", "qwen-image-3.0", role="image")
    assert seen[-1] == "512*512"
    assert seen[-1].split("*") == ["512", "512"]


def test_a_size_error_that_names_nothing_is_reported_not_retried(monkeypatch):
    import io
    import urllib.error

    calls = []

    def fake(url, key, payload, timeout):
        calls.append(payload)
        raise urllib.error.HTTPError(
            url, 400, "Bad Request", {},
            io.BytesIO(b'{"error":{"message":"size 512x512 is not supported"}}'))

    monkeypatch.setattr(store, "_request", fake)
    result = store.test("http://x/v1", "qwen-image-3.0", role="image")
    assert not result["ok"]
    assert len(calls) == 1, "it retried on an error that named no format"
    assert "not supported" in result["error"]
