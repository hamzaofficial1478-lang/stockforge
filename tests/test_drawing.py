"""Asking a model to draw the motifs the library has not got.

Seven pieces of decoration in one Halloween card had no drawing anywhere, and
each one stopped its design dead. Harvesting cuts them out of your own artwork
where they exist; this is for the ones that do not, and it produces something
to trace rather than something to ship.

That distinction is the whole design and it is about rights, not taste. A
generated illustration is not a thing you own outright, and the reason every
delivered file matches against your own library is that every curve in it is
yours to sell. So the picture is reference, the reference gets traced, and the
trace is yours. Most of what follows is tests that the reference cannot
accidentally become the deliverable.
"""

import base64
import io
import json
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from stockforge.providers.base import ProviderError
from stockforge.providers.images import (ImageProvider, find_image, from_env,
                                         size_separator)
from stockforge.stages import motifs as motifs_stage
from stockforge.stages.motifs import Gap


PNG = base64.b64decode(
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    b"IQAAAABJRU5ErkJggg==")


class _Drawer:
    name = "pretend-image-model@http://example.invalid/v1"

    def __init__(self, data=PNG, fails_on=None):
        self.data = data
        self.fails_on = fails_on or ()
        self.prompts: list[str] = []

    def draw(self, prompt, size=None):
        self.prompts.append(prompt)
        for bad in self.fails_on:
            if bad in prompt:
                raise ProviderError("the model refused that one")
        return self.data


def _gap(what="a small black bat", kind="seasonal", designs=3):
    return Gap(description=what, kind=kind, designs=designs)


# --- what it asks for ------------------------------------------------------

def test_the_prompt_asks_for_something_that_can_be_traced():
    """A soft painterly render with a drop shadow is prettier and useless — you
    cannot pull a clean path out of it. Flat, one subject, white ground."""
    asked = motifs_stage.prompt_for(_gap("a grinning carved pumpkin"))
    assert "a grinning carved pumpkin" in asked
    for must in ("flat vector", "solid colours", "white background"):
        assert must in asked.lower(), f"the prompt does not ask for {must}"
    for must_not in ("no text", "no drop shadow", "no gradient"):
        assert must_not in asked.lower(), f"the prompt does not rule out {must_not}"


def test_the_kind_reaches_the_prompt():
    assert "botanical" in motifs_stage.prompt_for(_gap("eucalyptus", "botanical"))


def test_a_gap_with_no_description_is_not_drawn(tmp_path):
    """There is nothing to ask for. Sending an empty prompt gets you a picture
    of something, which is worse than nothing."""
    assert motifs_stage.draw(_gap(""), tmp_path, _Drawer()) is None


# --- where it puts it ------------------------------------------------------

def test_a_drawing_lands_where_the_library_will_not_find_it(tmp_path):
    """The single most important line here. A generated raster picked up as a
    real motif would be placed into a design and sold, which is the one outcome
    this must never allow."""
    made = motifs_stage.draw(_gap(), tmp_path, _Drawer())

    assert made.path.parent.name == motifs_stage.DRAWN_DIR
    assert made.path.suffix == ".png"
    assert motifs_stage.load(tmp_path) == [], (
        "a generated picture was loaded into the motif library")


def test_the_library_scan_only_ever_sees_vectors(tmp_path):
    """Belt and braces on the same point, from the other side: even sitting in
    the top of the folder, a PNG is not a motif."""
    (tmp_path / "stray.png").write_bytes(PNG)
    motifs_stage.draw(_gap(), tmp_path, _Drawer())
    assert all(str(e.path).endswith(".svg") for e in motifs_stage.load(tmp_path))


def test_where_it_came_from_is_written_beside_it(tmp_path):
    """Six months on, "did I draw this or did a model?" is not a question to
    answer by squinting at it."""
    made = motifs_stage.draw(_gap("a wax seal"), tmp_path, _Drawer())
    beside = json.loads(made.path.with_suffix(".json").read_text())

    assert beside["generated"] is True
    assert beside["description"] == "a wax seal"
    assert beside["prompt"] == made.prompt
    assert beside["model"] == _Drawer.name
    assert "trace" in beside["note"].lower()


def test_the_same_gap_twice_does_not_pile_up_files(tmp_path):
    """The name comes off the prompt, so asking again replaces rather than
    accumulating forty near-identical bats."""
    first = motifs_stage.draw(_gap(), tmp_path, _Drawer())
    second = motifs_stage.draw(_gap(), tmp_path, _Drawer())
    assert first.path == second.path
    assert len(list((tmp_path / motifs_stage.DRAWN_DIR).glob("*.png"))) == 1


# --- the whole list --------------------------------------------------------

def test_one_refusal_does_not_lose_the_rest(tmp_path):
    """A model that will not draw one prompt usually manages the next. Six of
    seven beats none."""
    gaps = [_gap("a small black bat"), _gap("a carved pumpkin"), _gap("a wax seal")]
    drawer = _Drawer(fails_on=("carved pumpkin",))

    drawn, trouble = motifs_stage.draw_all(gaps, tmp_path, drawer)
    assert len(drawn) == 2
    assert len(trouble) == 1 and "carved pumpkin" in trouble[0]


def test_what_went_wrong_is_said_rather_than_swallowed(tmp_path):
    drawn, trouble = motifs_stage.draw_all([_gap("a wax seal")], tmp_path,
                                           _Drawer(fails_on=("wax seal",)))
    assert not drawn
    assert "refused" in trouble[0]


def test_progress_is_reported_per_drawing(tmp_path):
    """One picture at a time and each takes seconds. Silence for a minute looks
    like a hang."""
    seen = []
    motifs_stage.draw_all([_gap("one"), _gap("two")], tmp_path, _Drawer(),
                          on_each=lambda n, total, what: seen.append((n, total, what)))
    assert seen == [(1, 2, "one"), (2, 2, "two")]


# --- talking to a real server ----------------------------------------------

def test_the_size_separator_is_read_from_the_servers_own_complaint():
    """Qwen wants 1024*1024, OpenAI wants the x. Keeping a list of which vendor
    uses which is a list that is wrong the day somebody adds a third."""
    assert size_separator("Expected format: '<width>*<height>'") == "*"
    assert size_separator("expected <width>x<height>") == "x"
    assert size_separator("something else entirely") == ""


@pytest.mark.parametrize("body,want", [
    ({"data": [{"url": "http://x/a.png"}]}, "http://x/a.png"),
    ({"choices": [{"message": {"images": [{"image_url": {"url": "http://x/b.png"}}]}}]},
     "http://x/b.png"),
    ({"output": {"results": [{"b64_json": "AAAA"}]}}, "AAAA"),
    ({"nothing": "here"}, ""),
])
def test_the_picture_is_found_wherever_the_server_put_it(body, want):
    assert find_image(body) == want


def _serve(script):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            sent = json.loads(self.rfile.read(length) or b"{}")
            script["seen"].append(sent)
            if script.get("fussy") and "*" not in str(sent.get("size")):
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(
                    {"error": "Expected format: '<width>*<height>'"}).encode())
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"data": [
                {"b64_json": base64.b64encode(PNG).decode()}]}).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever,
                     kwargs={"poll_interval": 0.02}, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/v1"


def test_a_drawing_comes_back_over_a_real_socket():
    script = {"seen": []}
    server, url = _serve(script)
    try:
        got = ImageProvider(url, "pretend-model").draw("a small black bat")
        assert got == PNG
        assert script["seen"][0]["messages"][0]["content"][0]["type"] == "text", (
            "the prompt went as a bare string; Qwen's image models refuse that")
    finally:
        server.shutdown()
        server.server_close()


def test_a_server_that_wants_a_star_gets_one_without_being_told_in_advance():
    script = {"seen": [], "fussy": True}
    server, url = _serve(script)
    try:
        assert ImageProvider(url, "pretend-model").draw("a bat") == PNG
        assert len(script["seen"]) == 2, "it did not retry with the format asked for"
        assert "*" in script["seen"][1]["size"]
    finally:
        server.shutdown()
        server.server_close()


def test_no_drawing_model_says_how_to_add_one(monkeypatch):
    """The commonest state by far. An error that names the setting and the
    address beats one that says the key is missing."""
    for key in ("SF_IMAGE_BASE_URL", "SF_IMAGE_MODEL"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ProviderError) as exc:
        from_env()
    text = str(exc.value)
    assert "Drawing artwork" in text
    assert "dashscope" in text.lower(), "it does not say where Qwen lives"
