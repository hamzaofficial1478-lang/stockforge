"""The control panel: settings that survive a restart, the HTTP layer, and the
worker that is meant to be left running all day.

None of this had a test. The panel is the documented way in, and the settings
it writes were going to a file nothing read.
"""

import json
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

import pytest

from stockforge import providers
from stockforge.config import Settings, load_env
from stockforge.pipeline import Pipeline
from stockforge.sources import open_source
from stockforge.ui.server import EDITABLE, Handler, read_env, write_env
from stockforge.worker import State, Worker

from conftest import build_font_library, build_motif_library
from test_pipeline import ScriptedProvider, _listing


# --- settings that outlive the process ------------------------------------

def test_env_is_actually_read(tmp_path, monkeypatch):
    """It never was. The panel wrote your model URL and every slider into this
    file and nothing loaded it, so a restart put it all back to defaults."""
    monkeypatch.delenv("SF_MIX", raising=False)
    env = tmp_path / ".env"
    env.write_text("# a comment\nSF_MIX=0.9\n\nSF_VISION_MODEL=\"some/model\"\n")

    assert load_env(env) == {"SF_MIX": "0.9", "SF_VISION_MODEL": "some/model"}
    assert Settings().mix == 0.9


def test_env_is_read_on_startup_without_being_asked(tmp_path):
    """The explicit test above only proves load_env works. What matters is that
    it runs on import, before anything reads a setting — a fresh process with a
    .env beside it must come up configured."""
    import subprocess
    import sys

    (tmp_path / ".env").write_text("SF_MIX=0.9\nSF_PUBLISH_ALL=1\n")
    proc = subprocess.run(
        [sys.executable, "-c",
         "from stockforge.config import settings; "
         "print(settings.mix, settings.publish_all)"],
        cwd=tmp_path, capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(Path(__file__).parent.parent)},
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "0.9 True", proc.stdout


def test_something_exported_beats_the_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SF_MIX", "0.25")
    env = tmp_path / ".env"
    env.write_text("SF_MIX=0.9\n")
    assert load_env(env) == {}
    assert Settings().mix == 0.25


def test_a_setting_takes_effect_without_a_restart(tmp_path, monkeypatch):
    """The pipeline and the worker hold one Settings between them, so it has
    to change in place — a new object would leave them on the old values."""
    monkeypatch.setenv("SF_MOTIF_THRESHOLD", "0.45")
    cfg = Settings()
    assert cfg.motif_threshold == 0.45

    monkeypatch.setenv("SF_MOTIF_THRESHOLD", "0.80")
    cfg.reload()
    assert cfg.motif_threshold == 0.80


def test_writing_settings_keeps_the_comments(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# vision\nSF_VISION_MODEL=old\nSF_MIX=0.5\n")
    write_env(env, {"SF_VISION_MODEL": "new", "SF_ETSY_API_KEY": "abc"})

    text = env.read_text()
    assert "# vision" in text
    values = read_env(env)
    assert values["SF_VISION_MODEL"] == "new"
    assert values["SF_MIX"] == "0.5"
    assert values["SF_ETSY_API_KEY"] == "abc"


# --- the HTTP layer -------------------------------------------------------

@pytest.fixture
def panel(tmp_path, monkeypatch):
    monkeypatch.delenv("FONTCONFIG_FILE", raising=False)
    monkeypatch.setenv("SF_ENV_FILE", str(tmp_path / ".env"))
    build_font_library(tmp_path / "fonts")
    build_motif_library(tmp_path / "motifs")
    cfg = Settings(root=tmp_path / "work", fonts_dir=tmp_path / "fonts",
                   motifs_dir=tmp_path / "motifs")
    cfg.ensure_dirs()

    Handler.cfg = cfg
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05},
                     daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield base, cfg
    finally:
        server.shutdown()
        server.server_close()


def get(base, path):
    with urlopen(base + path, timeout=10) as r:
        return r.status, r.read(), r.headers.get("Content-Type", "")


def post(base, path, body):
    req = Request(base + path, data=json.dumps(body).encode(),
                  headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=30) as r:
        return r.status, json.loads(r.read())


@pytest.mark.parametrize("route", [
    "/api/health", "/api/status", "/api/worker", "/api/designs",
    "/api/review", "/api/motif-gaps",
])
def test_every_screen_has_something_to_read(panel, route):
    base, _ = panel
    status, body, ctype = get(base, route)
    assert status == 200
    assert ctype.startswith("application/json")
    json.loads(body)


def test_the_panel_itself_is_served(panel):
    base, _ = panel
    status, body, ctype = get(base, "/")
    assert status == 200
    assert ctype.startswith("text/html")
    assert b"stockforge" in body


def test_an_unknown_route_is_a_404(panel):
    base, _ = panel
    with pytest.raises(Exception) as exc:
        get(base, "/api/nonsense")
    assert "404" in str(exc.value)


def test_a_file_inside_the_workspace_is_served(panel):
    base, cfg = panel
    target = cfg.root / "renders" / "hello.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("artwork")

    status, body, _ = get(base, "/file?path=" + quote(str(target)))
    assert status == 200
    assert body == b"artwork"


@pytest.mark.parametrize("path", ["/etc/passwd", "../../../etc/passwd"])
def test_a_file_outside_the_workspace_is_refused(panel, path):
    """The panel has no login because it is only meant to answer your own
    browser. It still must not hand out the rest of the disk."""
    base, _ = panel
    with pytest.raises(Exception) as exc:
        get(base, "/file?path=" + quote(path))
    assert "403" in str(exc.value) or "404" in str(exc.value)


def test_the_browser_cannot_set_whatever_environment_variable_it_likes(panel):
    base, cfg = panel
    status, body = post(base, "/api/config",
                        {"SF_MIX": "0.8", "PATH": "/tmp/evil", "SF_NOT_A_SETTING": "x"})
    assert status == 200
    assert body["keys"] == ["SF_MIX"]

    saved = read_env(Path(body["file"]))
    assert saved == {"SF_MIX": "0.8"}
    assert "PATH" not in saved
    assert set(body["keys"]) <= EDITABLE


def test_saving_a_setting_changes_the_running_program(panel):
    base, cfg = panel
    post(base, "/api/config", {"SF_MOTIF_THRESHOLD": "0.9"})
    assert cfg.motif_threshold == 0.9


def test_a_secret_is_not_read_back_out(panel):
    base, _ = panel
    post(base, "/api/config", {"SF_ETSY_API_KEY": "a-real-key"})
    status, body, _ = get(base, "/api/config")
    values = json.loads(body)["values"]
    assert values["SF_ETSY_API_KEY"] == "••••••••"
    assert "SF_ETSY_API_KEY" in json.loads(body)["set"]


def test_a_decision_moves_the_design(panel):
    base, cfg = panel
    pipe = Pipeline(cfg)
    pipe.store.add_design(id="abc123", design_key="k", state="review")
    pipe.store.queue_review("abc123", "needs a look", 0.4)

    status, body = post(base, "/api/decide",
                        {"design_id": "abc123", "decision": "approve"})
    assert body["decision"] == "approve"
    row = pipe.store.conn.execute(
        "SELECT state FROM designs WHERE id=?", ("abc123",)).fetchone()
    assert row["state"] == "ready"


def test_a_decision_it_does_not_understand_is_refused(panel):
    base, _ = panel
    with pytest.raises(Exception) as exc:
        post(base, "/api/decide", {"design_id": "abc123", "decision": "delete"})
    assert "400" in str(exc.value)


# --- the worker -----------------------------------------------------------

def _wait(predicate, timeout=120.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def loaded(tmp_path, monkeypatch):
    """A workspace with two designs pulled and waiting."""
    monkeypatch.delenv("FONTCONFIG_FILE", raising=False)
    build_font_library(tmp_path / "fonts")
    build_motif_library(tmp_path / "motifs")
    cfg = Settings(root=tmp_path / "work", fonts_dir=tmp_path / "fonts",
                   motifs_dir=tmp_path / "motifs")

    provider = ScriptedProvider()
    providers.set_provider("vision", provider)
    providers.set_provider("reason", provider)

    _listing(tmp_path / "exports", "invite-one")
    _listing(tmp_path / "exports", "invite-two")
    pipe = Pipeline(cfg)
    pipe.pull(open_source("folder", str(tmp_path / "exports")))
    assert len(pipe.store.designs(state="pending")) == 2
    return cfg


def test_the_worker_works_the_queue_and_then_stands_down(loaded):
    worker = Worker(loaded)
    worker.set_pace(0)
    assert worker.start() is True
    assert _wait(lambda: not worker.alive), "the worker never finished"

    assert worker.progress.state is State.IDLE
    assert worker.progress.done == 2
    assert worker.progress.failed == 0
    assert len(worker.progress.recent) == 2


def test_starting_a_worker_that_is_already_going_does_not_start_a_second(loaded):
    worker = Worker(loaded)
    worker.set_pace(0)
    worker.start()
    assert worker.start() is False
    worker.stop()
    _wait(lambda: not worker.alive)


def test_a_limit_is_respected(loaded):
    worker = Worker(loaded)
    worker.set_pace(0)
    worker.start(limit=1)
    assert _wait(lambda: not worker.alive)
    assert worker.progress.done == 1
    assert len(Pipeline(loaded).store.designs(state="pending")) == 1


def test_stopping_it_stops_it(loaded):
    worker = Worker(loaded)
    worker.set_pace(30)                       # resting between designs
    worker.start()
    # state-agnostic: it has finished a design, whatever the verdict was
    assert _wait(lambda: len(worker.progress.recent) >= 1)

    worker.stop()
    assert _wait(lambda: not worker.alive, timeout=60), "stop was not honoured"
    assert worker.progress.state is State.STOPPED


def test_pausing_holds_it_and_resuming_lets_it_go(loaded):
    worker = Worker(loaded)
    worker.set_pace(0)
    worker.start()
    worker.pause()
    assert worker.progress.state in (State.PAUSING, State.PAUSED, State.IDLE)

    worker.resume()
    assert _wait(lambda: not worker.alive)
    assert worker.progress.done == 2
