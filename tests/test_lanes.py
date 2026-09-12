"""Building several designs at once.

One vision model, one design at a time, was the whole shape of the worker. A
second model bought nothing, because nothing used it — which is what the owner
noticed: "I have 3 more vision models ... the 2 vision models will work more
faster", and they did not, because the queue was worked by a single thread.

Lanes are that same loop run side by side. Everything here is about the two
things that go wrong when you do that: two lanes building the same design, and
counters that stop adding up.
"""

import threading
import time

import pytest

from stockforge.config import Settings
from stockforge.db import Store
from stockforge.worker import Progress, State, Worker, lane_providers


def _queue(tmp_path, count: int) -> Store:
    store = Store(tmp_path / "work" / "stockforge.db")
    for i in range(count):
        store.add_design(id=f"d{i:03d}", design_key=f"design-{i}",
                         source="folder", state="pending")
    return store


# --- claiming --------------------------------------------------------------

def test_two_lanes_never_get_the_same_design(tmp_path):
    """The fault that makes parallelism worse than useless: both lanes read
    "the first pending design", both build it, and the second overwrites the
    first. Twice the model spend for one result."""
    _queue(tmp_path, 40)
    db = tmp_path / "work" / "stockforge.db"

    taken: list[str] = []
    guard = threading.Lock()

    def lane(name):
        store = Store(db)
        # Bounded deliberately. A claim that does not actually take the design
        # hands the same row back for ever, and an unbounded loop would hang
        # the suite rather than fail it — a far worse way to find out.
        for _ in range(200):
            row = store.claim_design(name)
            if row is None:
                break
            with guard:
                taken.append(row["id"])
            time.sleep(0.001)          # a build takes time; that is the window
        store.conn.close()

    threads = [threading.Thread(target=lane, args=(f"lane-{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(taken) == 40, "some designs were never picked up"
    assert len(set(taken)) == 40, "a design was claimed twice"


def test_a_claimed_design_is_no_longer_pending(tmp_path):
    store = _queue(tmp_path, 3)
    row = store.claim_design("lane-0")
    assert row is not None
    assert len(store.designs(state="pending")) == 2
    assert store.conn.execute(
        "SELECT claimed_by FROM designs WHERE id=?", (row["id"],)
    ).fetchone()["claimed_by"] == "lane-0"


def test_an_empty_queue_claims_nothing(tmp_path):
    store = _queue(tmp_path, 0)
    assert store.claim_design("lane-0") is None


def test_a_released_design_goes_back_in_the_queue(tmp_path):
    """Stopping mid-build must not swallow the design. It is not pending and
    not finished, so nothing would ever look at it again."""
    store = _queue(tmp_path, 1)
    row = store.claim_design("lane-0")
    assert store.designs(state="pending") == []
    store.release_design(row["id"])
    assert len(store.designs(state="pending")) == 1


def test_work_abandoned_by_a_dead_process_is_picked_up_again(tmp_path):
    """A crash mid-build leaves a design claimed forever. Without reclaiming,
    it silently disappears from the queue — not pending, so nothing builds it,
    not finished, so nothing reports it."""
    store = _queue(tmp_path, 2)
    store.claim_design("lane-0")
    assert len(store.designs(state="pending")) == 1

    assert store.reclaim_abandoned(older_than=0.0) == 1
    assert len(store.designs(state="pending")) == 2
    assert store.conn.execute(
        "SELECT COUNT(*) n FROM designs WHERE claimed_by IS NOT NULL").fetchone()["n"] == 0


def test_work_still_in_progress_is_not_reclaimed(tmp_path):
    """The other half of it: a lane that is merely slow must keep its design."""
    store = _queue(tmp_path, 1)
    store.claim_design("lane-0")
    assert store.reclaim_abandoned(older_than=3600.0) == 0
    assert store.designs(state="pending") == []


# --- each lane on its own model -------------------------------------------

def test_one_lane_uses_whatever_the_environment_says(tmp_path):
    """Unchanged behaviour for anyone who never turns this on."""
    cfg = Settings(root=tmp_path / "work")
    assert lane_providers(cfg, 1) == [None]


def test_lanes_are_handed_the_saved_vision_models(tmp_path):
    from stockforge.ui import models as connections

    cfg = Settings(root=tmp_path / "work")
    cfg.ensure_dirs()
    connections.upsert(cfg.root, {"model": "model-a", "role": "vision",
                                  "base_url": "http://a/v1"})
    connections.upsert(cfg.root, {"model": "model-b", "role": "vision",
                                  "base_url": "http://b/v1"})

    built = lane_providers(cfg, 2)
    assert [p.model for p in built] == ["model-a", "model-b"], (
        "two lanes were not given two different models")


def test_more_lanes_than_models_share_rather_than_fail(tmp_path):
    from stockforge.ui import models as connections

    cfg = Settings(root=tmp_path / "work")
    cfg.ensure_dirs()
    connections.upsert(cfg.root, {"model": "only-one", "role": "vision",
                                  "base_url": "http://a/v1"})
    built = lane_providers(cfg, 3)
    assert len(built) == 3
    assert {p.model for p in built} == {"only-one"}


def test_a_text_only_connection_is_not_handed_to_a_vision_lane(tmp_path):
    from stockforge.ui import models as connections

    cfg = Settings(root=tmp_path / "work")
    cfg.ensure_dirs()
    connections.upsert(cfg.root, {"model": "reader", "role": "vision",
                                  "base_url": "http://a/v1"})
    connections.upsert(cfg.root, {"model": "writer", "role": "text",
                                  "base_url": "http://b/v1"})
    assert {p.model for p in lane_providers(cfg, 2)} == {"reader"}


def test_a_lane_model_only_applies_to_its_own_thread():
    """Lanes share a process. A provider set globally would mean every lane
    used whichever one started last, which is the bug this avoids."""
    from stockforge import providers
    from stockforge.providers.base import VisionProvider

    class Named(VisionProvider):
        def __init__(self, name):
            self.name = name

        def chat(self, system, user_text, images, **kw):
            return "{}"

    providers.reset()
    providers.set_provider("vision", Named("shared"))
    seen = {}

    def lane(name):
        providers.use_in_this_thread("vision", Named(name))
        time.sleep(0.02)                       # overlap the lanes deliberately
        seen[name] = providers.vision().name

    threads = [threading.Thread(target=lane, args=(f"lane-{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert seen == {f"lane-{i}": f"lane-{i}" for i in range(4)}
    assert providers.vision().name == "shared", "a lane leaked into the main thread"
    providers.reset()


# --- the numbers keep adding up -------------------------------------------

def _worker_with_lanes(tmp_path, lanes: int) -> Worker:
    worker = Worker(Settings(root=tmp_path / "work", workers=lanes))
    worker.lanes = [Progress(state=State.RUNNING, started_at=time.time())
                    for _ in range(lanes)]
    return worker


def test_the_run_total_is_the_sum_of_the_lanes(tmp_path):
    """`worker.progress.done` has to keep meaning what it always meant. Leaving
    the totals only in the lanes would make it read zero while work was plainly
    happening."""
    worker = _worker_with_lanes(tmp_path, 3)
    worker.lanes[0].done, worker.lanes[0].review = 4, 1
    worker.lanes[1].done, worker.lanes[1].failed = 3, 2
    worker.lanes[2].done = 5

    worker._sync()
    assert worker.progress.done == 12
    assert worker.progress.review == 1
    assert worker.progress.failed == 2


def test_the_panel_is_told_what_each_lane_is_doing(tmp_path):
    worker = _worker_with_lanes(tmp_path, 2)
    worker.lanes[0].current_design = "halloween-invite"
    worker.lanes[0].model = "model-a"
    worker.lanes[1].current_design = "wedding-suite"
    worker.lanes[1].model = "model-b"

    snap = worker.snapshot()
    assert snap["lane_count"] == 2
    assert [l["lane"] for l in snap["lanes"]] == [1, 2]
    assert [l["model"] for l in snap["lanes"]] == ["model-a", "model-b"]
    assert snap["current_design"] == "2 designs at once"


def test_a_single_lane_still_names_the_design_it_is_on(tmp_path):
    """Someone running one lane should see no sign that any of this exists."""
    worker = _worker_with_lanes(tmp_path, 1)
    worker.lanes[0].current_design = "halloween-invite"
    snap = worker.snapshot()
    assert snap["current_design"] == "halloween-invite"
    assert snap["lane_count"] == 1


def test_the_pace_is_shared_by_every_lane(tmp_path):
    worker = _worker_with_lanes(tmp_path, 3)
    worker.set_pace(7.5)
    assert worker.pace_seconds == 7.5


# --- and does it actually go faster ---------------------------------------

def test_two_lanes_finish_a_queue_faster_than_one(tmp_path, monkeypatch):
    """The whole point, measured rather than assumed.

    Each design is made to take a fixed, sleepy amount of time — which is what
    a model call is: mostly waiting. Two lanes should get through the queue in
    appreciably less wall-clock than one, and must still build every design
    exactly once.
    """
    from stockforge import worker as worker_module

    BUILD_SECONDS = 0.12
    DESIGNS = 8

    built: list[str] = []
    guard = threading.Lock()

    class FakePipeline:
        def __init__(self, cfg, on_progress=None):
            self.store = Store(cfg.db_path)
            self.cfg = cfg

        def build(self, design_id):
            time.sleep(BUILD_SECONDS)
            with guard:
                built.append(design_id)
            return "ready"

    monkeypatch.setattr(worker_module, "Pipeline", FakePipeline)

    def run(lanes):
        built.clear()
        root = tmp_path / f"run-{lanes}"
        store = Store(root / "stockforge.db")
        for i in range(DESIGNS):
            store.add_design(id=f"d{i:03d}", design_key=f"design-{i}",
                             source="folder", state="pending")
        store.conn.close()

        w = Worker(Settings(root=root, workers=lanes))
        w.pace_seconds = 0.0
        started = time.time()
        assert w.start() is True
        deadline = time.time() + 60
        while w.alive and time.time() < deadline:
            time.sleep(0.02)
        took = time.time() - started
        assert not w.alive, "the worker never finished"
        return took, list(built), w

    one, built_one, w1 = run(1)
    two, built_two, w2 = run(2)

    assert len(built_one) == DESIGNS
    assert sorted(built_two) == sorted(built_one), "parallel built a different set"
    assert len(set(built_two)) == DESIGNS, "a design was built twice"
    assert w2.progress.done == DESIGNS, f"the run total was wrong: {w2.progress.done}"

    # Two lanes cannot be slower, and on work that is mostly waiting they
    # should be clearly quicker. Deliberately loose — this runs on shared CI
    # hardware and a flaky timing test is worse than none.
    assert two < one * 0.8, (
        f"two lanes took {two:.2f}s against one lane's {one:.2f}s — no gain")


def test_a_limit_counts_the_run_not_each_lane(tmp_path, monkeypatch):
    """"Run 3" with two lanes means three designs, not six."""
    from stockforge import worker as worker_module

    built: list[str] = []
    guard = threading.Lock()

    class FakePipeline:
        def __init__(self, cfg, on_progress=None):
            self.store = Store(cfg.db_path)

        def build(self, design_id):
            time.sleep(0.02)
            with guard:
                built.append(design_id)
            return "ready"

    monkeypatch.setattr(worker_module, "Pipeline", FakePipeline)

    root = tmp_path / "limited"
    store = Store(root / "stockforge.db")
    for i in range(10):
        store.add_design(id=f"d{i:03d}", design_key=f"d{i}", source="folder", state="pending")
    store.conn.close()

    w = Worker(Settings(root=root, workers=2))
    w.pace_seconds = 0.0
    w.start(limit=3)
    deadline = time.time() + 30
    while w.alive and time.time() < deadline:
        time.sleep(0.02)

    assert not w.alive
    assert len(built) <= 3, f"the limit was per lane, not per run: built {len(built)}"


def test_lanes_take_the_models_that_were_made_live(tmp_path):
    """Somebody who saved four and made two live meant those two. Handing a
    lane a model that was deliberately stood down does the opposite of what
    they said."""
    from stockforge.ui import models as connections

    cfg = Settings(root=tmp_path / "work")
    cfg.ensure_dirs()
    live_a = connections.upsert(cfg.root, {"model": "live-a", "role": "vision",
                                           "base_url": "http://a/v1"})
    live_b = connections.upsert(cfg.root, {"model": "live-b", "role": "vision",
                                           "base_url": "http://b/v1"})
    connections.upsert(cfg.root, {"model": "stood-down", "role": "vision",
                                  "base_url": "http://c/v1"})
    connections.activate(cfg.root, live_a.id)
    connections.activate(cfg.root, live_b.id)

    assert {p.model for p in lane_providers(cfg, 2)} == {"live-a", "live-b"}


def test_with_nothing_made_live_the_saved_models_are_used_anyway(tmp_path):
    """Better than refusing to start because a box was never ticked."""
    from stockforge.ui import models as connections

    cfg = Settings(root=tmp_path / "work")
    cfg.ensure_dirs()
    connections.upsert(cfg.root, {"model": "never-activated", "role": "vision",
                                  "base_url": "http://a/v1"})
    assert {p.model for p in lane_providers(cfg, 2)} == {"never-activated"}
