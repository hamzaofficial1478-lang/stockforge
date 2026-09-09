"""Worker progress and import failures must reach the control panel."""
import threading

from stockforge.config import Settings
from stockforge.db import Store
from stockforge.worker import Worker, State
from stockforge.sources.links import LinksSource
from stockforge.sources.http import Unavailable
from test_ui import panel, get, post, post_raw, loaded, _wait


def test_startup_failure_is_visible_and_can_be_retried(tmp_path, monkeypatch):
    import stockforge.worker as module
    def broken(*args, **kwargs):
        raise OSError("workspace is not writable")
    monkeypatch.setattr(module, "Pipeline", broken)
    worker = Worker(Settings(root=tmp_path))
    assert worker.start()
    assert _wait(lambda: not worker.alive)
    assert worker.progress.state == State.FAILED
    assert "not writable" in worker.progress.last_error
    assert worker.start()
    assert _wait(lambda: not worker.alive)


def test_live_stage_is_visible_while_build_is_blocked(loaded, monkeypatch):
    from stockforge.pipeline import Pipeline
    entered, release = threading.Event(), threading.Event()
    def build(pipe, did):
        pipe.on_progress("Waiting for the vision model")
        entered.set()
        assert release.wait(10)
        return "master_only"
    monkeypatch.setattr(Pipeline, "build", build)
    worker = Worker(loaded)
    worker.set_pace(0)
    try:
        assert worker.snapshot()["remaining"] == 2
        worker.start(limit=1)
        assert entered.wait(10)
        progress = worker.snapshot()
        assert progress["state"] == "running"
        assert progress["current_design_id"]
        assert progress["current_step"] == "Waiting for the vision model"
        assert progress["events"] and progress["step_elapsed_s"] >= 0
        assert worker.start() is False
        worker.stop()
        assert worker.snapshot()["state"] == "stopping"
    finally:
        release.set()
        assert _wait(lambda: not worker.alive)
    assert worker.snapshot()["remaining"] == 1
    assert worker.snapshot()["current_design_id"] is None
    assert worker.progress.done == 1


def test_build_error_is_visible_and_does_not_leave_pending(loaded, monkeypatch):
    from stockforge.pipeline import Pipeline
    def build(*args):
        raise RuntimeError("Vision request timed out")
    monkeypatch.setattr(Pipeline, "build", build)
    worker = Worker(loaded)
    worker.set_pace(0)
    worker.start(limit=1)
    assert _wait(lambda: not worker.alive)
    assert worker.progress.failed == 1
    assert "timed out" in worker.snapshot()["last_error"]
    assert len(Store(loaded.db_path).designs(state="failed")) == 1


def test_etsy_403_is_reported_with_an_actionable_fallback(tmp_path, monkeypatch):
    from stockforge.sources import shop
    def blocked(*args):
        raise Unavailable("HTTP 403: blocked")
    monkeypatch.setattr(shop, "_get", blocked)
    monkeypatch.setattr("stockforge.sources.links.time.sleep", lambda seconds: None)
    source = LinksSource("https://www.etsy.com/listing/123/test", cache_dir=tmp_path)
    assert list(source.designs()) == []
    assert "HTTP 403" in source.warnings[0]
    assert "Upload the saved image" in source.warnings[0]


def test_import_warnings_reach_the_http_response(panel, monkeypatch):
    from stockforge.sources import shop
    def blocked(*args):
        raise Unavailable("HTTP 403")
    monkeypatch.setattr(shop, "_get", blocked)
    monkeypatch.setattr("stockforge.sources.links.time.sleep", lambda seconds: None)
    code, data = post(panel[0], "/api/pull", {"kind": "links", "target": "https://www.etsy.com/listing/123/test"})
    assert code == 200 and data["pulled"] == 0
    assert "HTTP 403" in data["warnings"][0]


def test_invalid_worker_request_returns_json(panel, monkeypatch):
    monkeypatch.setattr("stockforge.worker._worker", None)
    code, data = post_raw(panel[0], "/api/worker", {"action": "start", "limit": "bad"})
    assert code == 400 and data["error"]
