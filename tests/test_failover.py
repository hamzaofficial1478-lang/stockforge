"""One model going quiet must not lose the design.

The report, verbatim:

    20260913-191319/il_1588xN.8431939551_6j  folder  1  —  failed
    exception: meta/muse-glimmer-30b@https://integrate.api.nvidia.com/v1
    could not read a response: The read operation timed out

Two vision models were live at the time and neither the design nor the second
model did anything about it. Lanes were the wrong answer: they spread
*different* designs across models, so with one design in flight the second
model sat idle while the first timed out.

Two things have to be true for that to stop happening. A model that stops
answering has to be noticed while the design is still alive, which means the
clock runs on silence rather than on the total — a model reading a dense card
may legitimately think for minutes, and cutting that off throws away work that
was going to succeed. And when it is noticed, the work has to move to another
model rather than the design failing.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from stockforge.providers.base import ProviderError, VisionProvider
from stockforge.providers.failover import Failover, chain
from stockforge.providers.openai_compat import OpenAICompatProvider, Silent


# --- a server that behaves however the test needs it to -------------------

class _Script:
    """What the next request should do."""
    mode = "answer"          # answer | silent | slow-then-answer | http
    code = 500
    pause = 0.0
    hits = 0


def _serve(script):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def do_POST(self):
            script.hits += 1
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)

            if script.mode == "http":
                self.send_response(script.code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error": "scripted"}')
                return

            if script.mode == "silent":
                # Headers, then nothing. Exactly what a model that has stopped
                # looks like from this end.
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.flush()
                time.sleep(script.pause or 30)
                return

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            if script.mode == "slow-then-answer":
                # Thinking out loud for longer than the silence budget, but
                # never actually going quiet for that long.
                deadline = time.monotonic() + script.pause
                while time.monotonic() < deadline:
                    self.wfile.write(b': keep-alive\n\n')
                    self.wfile.flush()
                    time.sleep(0.05)
            for piece in ('{"answer"', ': "yes"}'):
                chunk = {"choices": [{"delta": {"content": piece}}]}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.flush()
            done = {"choices": [{"delta": {}, "finish_reason": "stop"}]}
            self.wfile.write(f"data: {json.dumps(done)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever,
                     kwargs={"poll_interval": 0.02}, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/v1"


@pytest.fixture
def endpoint():
    script = _Script()
    server, url = _serve(script)
    try:
        yield script, url
    finally:
        server.shutdown()
        server.server_close()


# --- the clock is on silence, not on the total ----------------------------

def test_a_model_that_says_nothing_is_given_up_on(endpoint):
    script, url = endpoint
    script.mode, script.pause = "silent", 10
    model = OpenAICompatProvider(url, "quiet-one", silence=1, retries=0)

    started = time.monotonic()
    with pytest.raises(Silent) as quiet:
        model.chat("s", "u", [])
    took = time.monotonic() - started

    assert took < 5, f"waited {took:.1f}s for a model that had stopped"
    assert "quiet-one" in str(quiet.value)


def test_a_model_that_keeps_talking_is_left_alone(endpoint):
    """The point of measuring silence rather than the total. This one answers
    over four times its silence budget and must not be cut off — a vision model
    reading a dense card is exactly this shape, and killing it loses a design
    that was about to succeed."""
    script, url = endpoint
    script.mode, script.pause = "slow-then-answer", 2.0
    model = OpenAICompatProvider(url, "thinker", silence=0.45, retries=0)

    started = time.monotonic()
    got = model.chat("s", "u", [])
    took = time.monotonic() - started

    assert got == '{"answer": "yes"}'
    assert took > 2.0, f"it did not actually take longer than the budget ({took:.1f}s)"


def test_the_wait_is_not_the_whole_request_timeout(endpoint):
    """The old behaviour, named so it cannot come back: a single timeout on the
    whole call, five minutes long, so a dead endpoint held a design for five
    minutes and a slow one was killed at exactly the wrong moment."""
    script, url = endpoint
    script.mode, script.pause = "silent", 10
    model = OpenAICompatProvider(url, "quiet-one", silence=1, timeout=300, retries=0)

    started = time.monotonic()
    with pytest.raises(Silent):
        model.chat("s", "u", [])
    assert time.monotonic() - started < 5


# --- and then somebody else does the work ---------------------------------

class _Stub(VisionProvider):
    def __init__(self, name, answer=None, raises=None):
        self.name = self.model = name
        self.answer, self.raises, self.calls = answer, raises, 0

    def chat(self, system, user_text, images, **kw):
        self.calls += 1
        if self.raises:
            raise self.raises
        return self.answer


def test_the_next_model_finishes_the_design():
    quiet = _Stub("quiet", raises=Silent("quiet", 120))
    spare = _Stub("spare", answer='{"ok": true}')

    assert chain([quiet, spare]).chat("s", "u", []) == '{"ok": true}'
    assert quiet.calls == 1 and spare.calls == 1


def test_the_design_only_fails_when_every_model_has(endpoint):
    quiet = _Stub("quiet", raises=Silent("quiet", 120))
    broken = _Stub("broken", raises=ProviderError("broken HTTP 500: upstream"))

    with pytest.raises(ProviderError) as exc:
        chain([quiet, broken]).chat("s", "u", [])
    text = str(exc.value)
    assert "quiet" in text and "broken" in text, "it does not say what each one did"
    assert "Review" in text, "it does not say what to do about it"


def test_a_refused_request_is_not_asked_of_everyone():
    """A 400 is the request being wrong, and it will be just as wrong at the
    next model. Walking a malformed call down four endpoints is four times the
    wait for the same answer."""
    refused = _Stub("picky", raises=ProviderError("picky HTTP 400: bad image"))
    spare = _Stub("spare", answer="never reached")

    with pytest.raises(ProviderError, match="HTTP 400"):
        chain([refused, spare]).chat("s", "u", [])
    assert spare.calls == 0


def test_a_bad_key_is_not_asked_of_everyone():
    refused = _Stub("unauth", raises=ProviderError("unauth HTTP 401: bad key"))
    spare = _Stub("spare", answer="never reached")

    with pytest.raises(ProviderError, match="HTTP 401"):
        chain([refused, spare]).chat("s", "u", [])
    assert spare.calls == 0


def test_one_model_is_left_as_it_is():
    """Nothing to fall back to, so the wrapper only adds a layer and a name
    that reads as though there were two."""
    only = _Stub("only")
    assert chain([only]) is only


def test_the_chain_says_what_it_will_try():
    made = chain([_Stub("first"), _Stub("second")])
    assert isinstance(made, Failover)
    assert "first" in made.name and "second" in made.name


def test_a_switch_can_be_watched():
    """The queue log has to be able to say a model was swapped out, or it looks
    like the design simply took a long time for no reason."""
    seen = []
    quiet = _Stub("quiet", raises=Silent("quiet", 120))
    spare = _Stub("spare", answer="{}")
    chain([quiet, spare], on_switch=lambda p, why: seen.append((p.name, why))).chat("s", "u", [])

    assert len(seen) == 1
    assert seen[0][0] == "spare"
    assert "quiet" in seen[0][1]


# --- the whole thing, over a real socket ----------------------------------

def test_a_dead_endpoint_and_a_live_one_produce_an_answer(endpoint):
    """End to end: a model that goes quiet, a model that answers, and a design
    that survives. This is the run that failed."""
    dead_script = _Script()
    dead_script.mode, dead_script.pause = "silent", 10
    dead_server, dead_url = _serve(dead_script)

    script, good_url = endpoint
    script.mode = "answer"
    try:
        both = chain([
            OpenAICompatProvider(dead_url, "muse-glimmer-30b", silence=1, retries=0),
            OpenAICompatProvider(good_url, "the-spare", silence=5, retries=0),
        ])
        assert both.chat("s", "u", []) == '{"answer": "yes"}'
        assert dead_script.hits == 1 and script.hits == 1
    finally:
        dead_server.shutdown()
        dead_server.server_close()
