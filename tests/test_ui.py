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
    # Both, because load_env deliberately lets an exported value win over the
    # file — so either one already in the environment makes this test measure
    # the environment instead of the loader.
    monkeypatch.delenv("SF_MIX", raising=False)
    monkeypatch.delenv("SF_VISION_MODEL", raising=False)
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
    cfg = Settings(root=tmp_path / "work", fonts_dir=tmp_path / "fonts", preserve_original=False,
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
    target = cfg.root / "renders" / "hello.svg"
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


# --- the doors the panel opens --------------------------------------------

def post_raw(base, path, body):
    """Like post(), but hands back the error responses too rather than raising,
    because refusing badly-formed input correctly is the thing under test."""
    from urllib.error import HTTPError
    req = Request(base + path, data=json.dumps(body).encode(),
                  headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_pulling_a_folder_through_the_panel_brings_designs_in(panel, tmp_path):
    """The panel and the command line had drifted apart once before over
    exactly this kind of thing, and this door had no test at all."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_pipeline import _listing

    base, cfg = panel
    _listing(tmp_path / "exports", "wedding-invite")

    status, body = post_raw(base, "/api/pull",
                            {"kind": "folder", "target": str(tmp_path / "exports")})
    assert status == 200, body
    assert body["pulled"] == 1

    status, body, _ = get(base, "/api/status")
    assert json.loads(body)["designs"] == 1


def test_pulling_without_a_target_is_refused_rather_than_crashing(panel):
    base, _ = panel
    for payload in ({}, {"kind": "folder"}, {"kind": "nonsense", "target": "/tmp"},
                    {"target": "/tmp"}):
        status, body = post_raw(base, "/api/pull", payload)
        assert status == 400, f"{payload} was accepted"
        assert "error" in body
        # Falling through to the exception handler also gives a 400, so the
        # status alone does not show the request was checked. What separates
        # them is whether the answer tells you what to fix or hands you the
        # text of whatever blew up further in.
        assert "required" in body["error"], (
            f"{payload} was answered with {body['error']!r} rather than a "
            f"reason you could act on")


def test_pulling_a_folder_that_is_not_there_says_so(panel, tmp_path):
    """A stack trace in the browser console is not an answer."""
    base, _ = panel
    status, body = post_raw(base, "/api/pull",
                            {"kind": "folder", "target": str(tmp_path / "nope")})
    assert status in (200, 400)
    if status == 200:
        assert body["pulled"] == 0
    else:
        assert "error" in body


def test_counting_a_folder_answers_without_pulling_it(panel, tmp_path):
    """The question you ask first: how big is this job."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_pipeline import _listing

    base, cfg = panel
    _listing(tmp_path / "exports", "one")
    _listing(tmp_path / "exports", "two")

    status, body = post_raw(base, "/api/count",
                            {"kind": "folder", "target": str(tmp_path / "exports")})
    assert status == 200, body
    assert body["count"] == 2

    status, after, _ = get(base, "/api/status")
    assert json.loads(after)["designs"] == 0, "counting pulled them in"


def test_counting_a_bad_source_is_an_error_not_a_crash(panel):
    base, _ = panel
    status, body = post_raw(base, "/api/count", {"kind": "wat", "target": "x"})
    assert status == 400
    assert "error" in body


def test_publishing_with_nothing_ready_refuses_and_says_why(panel):
    """It used to be possible to send an empty batch. The answer has to name
    the reason, because "nothing happened" is indistinguishable from a bug."""
    base, _ = panel
    status, body = post_raw(base, "/api/publish", {"dry_run": True})
    assert status == 400
    assert "nothing" in body["error"].lower()


def test_a_dry_run_writes_the_csvs_and_sends_nothing(panel, tmp_path, monkeypatch):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_pipeline import ScriptedProvider, _listing

    from stockforge import providers
    from stockforge.pipeline import Pipeline
    from stockforge.sources import open_source

    base, cfg = panel
    provider = ScriptedProvider()
    providers.set_provider("vision", provider)
    providers.set_provider("reason", provider)
    _listing(tmp_path / "exports", "wedding-invite")
    pipe = Pipeline(cfg)
    pipe.pull(open_source("folder", str(tmp_path / "exports")))
    pipe.build(pipe.store.designs()[0]["id"])

    sent = []
    import stockforge.publish as publish_stage
    monkeypatch.setattr(publish_stage, "upload_batch",
                        lambda *a, **k: sent.append(a) or {})

    status, body = post_raw(base, "/api/publish", {"dry_run": True})
    assert status == 200, body
    assert body["uploaded"] is False
    assert body["files"] >= 1
    assert sent == [], "a dry run uploaded something"
    for path in body["metadata"].values():
        assert Path(path).is_file()


# --- uploading through the panel ------------------------------------------

def upload(base, files):
    """Post files the way a browser's <input type=file multiple> does."""
    import io
    from urllib.error import HTTPError

    boundary = "----stockforgepaneltest"
    out = io.BytesIO()
    for name, content in files:
        out.write(f"--{boundary}\r\n".encode())
        out.write(f'Content-Disposition: form-data; name="files"; '
                  f'filename="{name}"\r\n'.encode())
        out.write(b"Content-Type: application/octet-stream\r\n\r\n")
        out.write(content)
        out.write(b"\r\n")
    out.write(f"--{boundary}--\r\n".encode())

    req = Request(base + "/api/upload", data=out.getvalue(),
                  headers={"Content-Type":
                           f"multipart/form-data; boundary={boundary}"})
    try:
        with urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _jpg(w=400, h=560, shade=230):
    """Distinct bytes per call: identical images hash to one asset, which is
    right, and would make this test measure the deduplication instead."""
    import cv2
    import numpy as np
    art = np.full((h, w, 3), 230, np.uint8)
    art[10:60, 10:60] = shade
    ok, buf = cv2.imencode(".jpg", art)
    return buf.tobytes()


def test_images_dropped_on_the_panel_become_designs(panel):
    """The folder door assumed you could type the path of a folder that already
    existed. From a browser, with the exports in a download folder, there was
    no way to put them anywhere at all."""
    base, cfg = panel
    status, body = upload(base, [("card-1.jpg", _jpg(shade=40)),
                                 ("card-2.jpg", _jpg(shade=90))])

    assert status == 200, body
    assert body["accepted"] == 2
    assert body["pulled"] == 1, "two images of one design should group as one"
    assert body["by_extension"] == {".jpg": 2}

    status, totals, _ = get(base, "/api/status")
    assert json.loads(totals)["images"] == 2


def test_the_panel_says_what_it_could_not_take(panel):
    """"12 of 50 uploaded" with no list of the other 38 is the failure this
    project keeps running into."""
    base, _ = panel
    status, body = upload(base, [("good.jpg", _jpg()), ("notes.txt", b"hello"),
                                 ("prices.csv", b"a,b")])
    assert status == 200, body
    assert body["accepted"] == 1
    assert body["rejected"] == 2
    refused = {f["name"] for f in body["files"] if not f["ok"]}
    assert refused == {"notes.txt", "prices.csv"}
    assert all(f["reason"] for f in body["files"] if not f["ok"])


def test_a_zip_is_unpacked_by_the_panel(panel):
    """Exports arrive zipped. Asking someone to unpack it first is asking them
    to do the program's job."""
    import io
    import zipfile

    base, _ = panel
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("shop/design-1.jpg", _jpg(shade=40))
        zf.writestr("shop/design-2.jpg", _jpg(shade=90))
        zf.writestr("shop/readme.txt", b"x")

    status, body = upload(base, [("exports.zip", buf.getvalue())])
    assert status == 200, body
    assert body["accepted"] == 2
    assert body["files"][0]["extracted"] == 2
    assert "1 file(s)" in body["files"][0]["reason"]
    assert body["pulled"] == 1


def test_an_upload_with_nothing_usable_is_refused_with_a_reason(panel):
    base, _ = panel
    status, body = upload(base, [("notes.txt", b"hello")])
    assert status == 400
    assert "image" in body["error"]
    assert body["rejected"] == 1


def test_uploading_nothing_is_not_a_crash(panel):
    from urllib.error import HTTPError
    base, _ = panel
    req = Request(base + "/api/upload", data=b"",
                  headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=20) as r:
            status, body = r.status, json.loads(r.read())
    except HTTPError as exc:
        status, body = exc.code, json.loads(exc.read())
    assert status == 400
    assert "error" in body


# --- managing models through the panel ------------------------------------

def test_a_model_can_be_added_listed_edited_and_deleted(panel):
    """Setup had four bare text boxes, no list, and no way to remove anything
    you had typed into them."""
    base, _ = panel

    status, body = post_raw(base, "/api/models/save",
                            {"label": "Local NIM", "model": "a-model",
                             "base_url": "http://localhost:8000/v1"})
    assert status == 200, body
    made = body["model"]

    status, listed, _ = get(base, "/api/models")
    assert [m["label"] for m in json.loads(listed)["models"]] == ["Local NIM"]

    post_raw(base, "/api/models/save", {"id": made["id"], "label": "Renamed"})
    status, listed, _ = get(base, "/api/models")
    rows = json.loads(listed)["models"]
    assert len(rows) == 1 and rows[0]["label"] == "Renamed"

    status, body = post_raw(base, "/api/models/delete", {"id": made["id"]})
    assert status == 200 and body["deleted"] is True
    status, listed, _ = get(base, "/api/models")
    assert json.loads(listed)["models"] == []


def test_deleting_something_that_is_not_there_is_a_404_not_a_crash(panel):
    base, _ = panel
    status, body = post_raw(base, "/api/models/delete", {"id": "nope"})
    assert status == 404


def test_making_a_model_live_changes_what_the_pipeline_uses(panel):
    """The point of the button. Saving a connection and having the run keep
    using the old one is the bug this replaces."""
    import os

    from stockforge import providers

    base, cfg = panel
    status, body = post_raw(base, "/api/models/save",
                            {"label": "New", "model": "the-new-model",
                             "base_url": "http://localhost:11434/v1"})
    made = body["model"]

    status, body = post_raw(base, "/api/models/activate", {"id": made["id"]})
    assert status == 200, body
    assert body["active"]["active"] is True

    assert os.environ["SF_VISION_MODEL"] == "the-new-model"
    assert providers.vision().model == "the-new-model", \
        "the pipeline is still on the old connection"


def test_the_panel_never_hands_an_api_key_back(panel):
    base, _ = panel
    post_raw(base, "/api/models/save", {"label": "Hosted", "model": "m",
                                        "api_key": "sk-secret"})
    status, listed, _ = get(base, "/api/models")
    raw = listed.decode()
    assert "sk-secret" not in raw
    assert json.loads(raw)["models"][0]["has_key"] is True


def test_fetching_models_from_a_dead_url_says_so(panel):
    base, _ = panel
    status, body = post_raw(base, "/api/models/fetch",
                            {"base_url": "http://127.0.0.1:9/v1"})
    assert status == 200
    assert "error" in body


def test_the_panel_says_which_file_types_it_takes(panel):
    """So the upload box can name them rather than the user guessing."""
    base, _ = panel
    status, listed, _ = get(base, "/api/models")
    exts = json.loads(listed)["extensions"]
    assert ".svg" in exts and ".jpg" in exts and ".webp" in exts


def test_the_panel_can_update_itself(panel):
    """The last thing the launcher menu could do that the panel could not,
    which meant closing the browser and going back to a terminal for it."""
    base, _ = panel
    status, body = post_raw(base, "/api/update", {})
    assert status in (200, 400), body
    if status == 200:
        assert "message" in body and body["message"]
        assert "changed" in body
    else:
        assert body["error"], "it failed without saying why"


def test_the_file_types_are_offered_in_a_sensible_order(panel):
    """The panel prints this list to say what it takes. Alphabetical put .bmp
    first, which is a strange thing to lead with."""
    base, _ = panel
    status, listed, _ = get(base, "/api/models")
    exts = json.loads(listed)["extensions"]
    assert exts[:3] == [".jpg", ".jpeg", ".png"]
    assert ".svg" in exts
