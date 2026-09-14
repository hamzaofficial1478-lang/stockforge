"""Reading a design: once, and several questions at a time.

Two costs that were nobody's decision, only nobody's attention.

The five reading calls ran one after another although four of them depend on
nothing but the survey — the palette does not need to know what the type is,
and the type does not need to know what the shapes are. On a hosted model that
is four waits of several minutes where one would do.

And the read was thrown away and re-made on every build, so pressing Run again
on a design in Review spent five model calls arriving at the answer already
sitting in the database — about twenty minutes to learn nothing new.
"""

import threading
import time

import pytest

from stockforge import providers
from stockforge.config import Settings
from stockforge.pipeline import Pipeline
from stockforge.sources import open_source
from stockforge.stages import analyse as analyse_stage

from conftest import build_font_library, build_motif_library
from test_pipeline import ScriptedProvider, _listing


class _Counting(ScriptedProvider):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def structured(self, system, user_text, images, model, **kw):
        self.calls += 1
        return super().structured(system, user_text, images, model, **kw)


class _Slow(ScriptedProvider):
    """A second a call, and it remembers how many were ever in flight at once."""

    def __init__(self, seconds: float = 0.4):
        super().__init__()
        self.seconds = seconds
        self.live = 0
        self.peak = 0
        self._lock = threading.Lock()

    def structured(self, system, user_text, images, model, **kw):
        with self._lock:
            self.live += 1
            self.peak = max(self.peak, self.live)
        try:
            time.sleep(self.seconds)
            return super().structured(system, user_text, images, model, **kw)
        finally:
            with self._lock:
                self.live -= 1


@pytest.fixture
def workspace(tmp_path):
    build_font_library(tmp_path / "fonts")
    build_motif_library(tmp_path / "motifs")
    cfg = Settings(root=tmp_path / "work", fonts_dir=tmp_path / "fonts",
                   motifs_dir=tmp_path / "motifs", preserve_original=True)
    cfg.ensure_dirs()
    _listing(tmp_path / "in", "card")
    return cfg, tmp_path


def _pulled(workspace, provider):
    cfg, tmp = workspace
    providers.set_provider("vision", provider)
    providers.set_provider("reason", provider)
    pipe = Pipeline(cfg)
    pipe.pull(open_source("folder", str(tmp / "in")))
    return pipe, pipe.store.designs()[0]["id"]


# --- several questions at once --------------------------------------------

def test_the_reading_calls_actually_overlap(workspace, monkeypatch):
    monkeypatch.setenv("SF_VISION_CONCURRENCY", "4")
    provider = _Slow()
    pipe, design_id = _pulled(workspace, provider)

    pipe.build(design_id)
    assert provider.peak > 1, (
        "every reading call still waited for the one before it")


def test_one_at_a_time_is_still_possible(workspace, monkeypatch):
    """For an endpoint that rate limits, or for working out which call is the
    slow one."""
    monkeypatch.setenv("SF_VISION_CONCURRENCY", "1")
    provider = _Slow(seconds=0.05)
    pipe, design_id = _pulled(workspace, provider)

    pipe.build(design_id)
    assert provider.peak == 1


def test_overlapping_is_faster_than_not(workspace, monkeypatch):
    """The point of the exercise, measured rather than assumed. Five calls of
    the same length: one at a time is five waits, four at a time is two."""
    cfg, tmp = workspace

    def timed(concurrency):
        monkeypatch.setenv("SF_VISION_CONCURRENCY", str(concurrency))
        provider = _Slow(seconds=0.35)
        providers.set_provider("vision", provider)
        providers.set_provider("reason", provider)
        pipe = Pipeline(Settings(root=tmp / f"work{concurrency}",
                                 fonts_dir=tmp / "fonts", motifs_dir=tmp / "motifs",
                                 preserve_original=True))
        pipe.cfg.ensure_dirs()
        pipe.pull(open_source("folder", str(tmp / "in")))
        started = time.monotonic()
        pipe.build(pipe.store.designs()[0]["id"])
        return time.monotonic() - started

    one = timed(1)
    many = timed(4)
    assert many < one * 0.8, (
        f"four at a time took {many:.2f}s against {one:.2f}s one at a time — "
        f"that is not a saving worth the threads")


def test_a_failure_in_one_reading_call_is_not_swallowed(workspace, monkeypatch):
    """Four answers where one is wrong is not three quarters of a design. It
    has to be read again, and saying so immediately beats assembling a spec
    around a hole."""
    monkeypatch.setenv("SF_VISION_CONCURRENCY", "4")

    class Breaks(ScriptedProvider):
        def structured(self, system, user_text, images, model, **kw):
            if model.__name__ == "StructureRead":
                raise RuntimeError("the endpoint fell over")
            return super().structured(system, user_text, images, model, **kw)

    pipe, design_id = _pulled(workspace, Breaks())
    # It raises rather than returning a half-read design; the worker is what
    # turns that into a failed row with the reason on it.
    with pytest.raises(RuntimeError, match="fell over"):
        pipe.build(design_id)


def test_the_model_a_lane_installed_is_the_one_that_reads(workspace, monkeypatch):
    """A lane installs its model for its own thread only. A worker thread
    started here would ask providers.vision() and get whatever the environment
    says instead — correct-looking output from the wrong endpoint, which is the
    worst shape of bug there is."""
    monkeypatch.setenv("SF_VISION_CONCURRENCY", "4")
    mine = _Counting()
    others = _Counting()
    providers.set_provider("vision", others)
    providers.set_provider("reason", others)

    cfg, tmp = workspace
    pipe = Pipeline(cfg)
    pipe.pull(open_source("folder", str(tmp / "in")))
    providers.use_in_this_thread("vision", mine)
    providers.use_in_this_thread("reason", mine)
    try:
        pipe.build(pipe.store.designs()[0]["id"])
    finally:
        providers.use_in_this_thread("vision", None)
        providers.use_in_this_thread("reason", None)

    assert mine.calls > 0, "the lane's own model was never used"
    assert others.calls == 0, (
        f"{others.calls} reading call(s) went to the environment's model rather "
        f"than the one this lane was given")


# --- and only once --------------------------------------------------------

def test_running_a_design_again_does_not_read_it_again(workspace):
    """The retry cost. Five calls to arrive at an answer already in the
    database, every time somebody pressed Run again."""
    provider = _Counting()
    pipe, design_id = _pulled(workspace, provider)

    pipe.build(design_id)
    first = provider.calls
    assert first >= 5, "this test is pointless if the first build was cheap"

    provider.calls = 0
    pipe.build(design_id)
    assert provider.calls < first, (
        f"the retry cost {provider.calls} calls, the same as reading it fresh")


def test_a_fresh_reading_can_still_be_asked_for(workspace):
    """For when the flattening or the prompts have changed underneath a stored
    read, which is the one case where the file on disk is no longer what the
    model would say about it."""
    provider = _Counting()
    pipe, design_id = _pulled(workspace, provider)
    pipe.build(design_id)
    first = provider.calls

    provider.calls = 0
    pipe.build(design_id, reread=True)
    assert provider.calls >= first * 0.8, (
        "asking to read it again reused the stored read anyway")


def test_a_design_that_failed_while_reading_is_read_again(workspace):
    """There is nothing stored to reuse, so this has to fall through on its own
    — it is the commonest retry there is, after a model went quiet mid-read."""
    class QuietThenFine(ScriptedProvider):
        def __init__(self):
            super().__init__()
            self.first_go = True

        def structured(self, system, user_text, images, model, **kw):
            if self.first_go and model.__name__ == "Survey":
                self.first_go = False
                raise RuntimeError("the read operation timed out")
            return super().structured(system, user_text, images, model, **kw)

    pipe, design_id = _pulled(workspace, QuietThenFine())
    with pytest.raises(RuntimeError, match="timed out"):
        pipe.build(design_id)
    assert pipe.store.get_read(design_id) is None, "a failed read was stored anyway"
    assert pipe.build(design_id) != "failed", (
        "a design that never got a reading could not be retried")


def test_a_stored_reading_that_no_longer_fits_is_replaced(workspace):
    """The schema moves. A read written against an older one must not fail the
    design — it should quietly be read again."""
    provider = _Counting()
    pipe, design_id = _pulled(workspace, provider)
    pipe.build(design_id)

    pipe.store.save_read(design_id, {"nonsense": True})
    provider.calls = 0
    assert pipe.build(design_id) != "failed"
    assert provider.calls >= 5, "it did not read the design again"


# --- reaching it from the panel -------------------------------------------

def test_the_panel_can_ask_for_a_fresh_reading(workspace):
    """Two different retries, and the difference is the expensive half. Running
    again mixes and draws from the reading on file; reading again looks at the
    artwork from scratch."""
    from urllib.request import Request, urlopen
    import json as _json
    import threading
    from http.server import ThreadingHTTPServer
    from stockforge.ui.server import Handler

    cfg, tmp = workspace
    provider = _Counting()
    pipe, design_id = _pulled(workspace, provider)
    pipe.build(design_id)
    pipe.store.queue_review(design_id, "so there is something to decide on", 0.0)
    assert pipe.store.get_read(design_id) is not None

    Handler.cfg = cfg
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever,
                     kwargs={"poll_interval": 0.05}, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        def decide(what):
            req = Request(base + "/api/decide",
                          data=_json.dumps({"design_id": design_id,
                                            "decision": what}).encode(),
                          headers={"Content-Type": "application/json"})
            with urlopen(req, timeout=10) as r:
                return _json.loads(r.read())

        assert decide("retry")["decision"] == "retry"
        assert pipe.store.get_read(design_id) is not None, (
            "Run again threw away the reading, which is the expensive half")

        assert decide("reread")["decision"] == "reread"
        assert pipe.store.get_read(design_id) is None, (
            "Read it again kept the old reading, so it would not read anything")
    finally:
        server.shutdown()
        server.server_close()


# --- a smaller model for the easy questions -------------------------------

def test_the_easy_questions_go_to_the_small_model(workspace, monkeypatch):
    """Three of the five reading passes do not need a big model. The palette is
    already measured off the pixels and the model only names the roles;
    provenance is "is any of this a photograph"; the survey is "which of these
    images are pages". Sending those to a 30B reasoner is minutes a design for
    nothing."""
    monkeypatch.setenv("SF_QUICK_MODEL", "something-small")
    big, small = _Counting(), _Counting()
    providers.set_provider("vision", big)
    providers.set_provider("reason", big)
    providers.set_provider("quick", small)

    pipe, design_id = _pulled(workspace, big)
    providers.set_provider("quick", small)
    pipe.build(design_id)

    assert small.calls >= 3, (
        f"the small model answered {small.calls} question(s) — the survey, the "
        f"palette and the provenance should all have gone to it")
    assert big.calls >= 2, "the hard passes did not go to the reading model"


def test_with_no_small_model_the_reading_model_does_all_of_it(workspace, monkeypatch):
    """It has to cost nothing to ignore. Somebody who never configures a second
    model should see exactly what they saw before."""
    monkeypatch.delenv("SF_QUICK_MODEL", raising=False)
    only = _Counting()
    pipe, design_id = _pulled(workspace, only)
    providers.reset()
    providers.set_provider("vision", only)
    providers.set_provider("reason", only)

    pipe.build(design_id)
    assert only.calls >= 5, "some reading went somewhere else entirely"


def test_the_small_model_still_respects_a_lane(workspace, monkeypatch):
    """A lane pins its own models. The easy questions must follow that too, or
    a lane's work quietly leaves through an endpoint it was never given."""
    monkeypatch.delenv("SF_QUICK_MODEL", raising=False)
    mine, others = _Counting(), _Counting()
    providers.set_provider("vision", others)
    providers.set_provider("reason", others)

    cfg, tmp = workspace
    pipe = Pipeline(cfg)
    pipe.pull(open_source("folder", str(tmp / "in")))
    providers.use_in_this_thread("vision", mine)
    providers.use_in_this_thread("reason", mine)
    try:
        pipe.build(pipe.store.designs()[0]["id"])
    finally:
        providers.use_in_this_thread("vision", None)
        providers.use_in_this_thread("reason", None)

    assert others.calls == 0, (
        f"{others.calls} easy question(s) went past the lane's own model")
