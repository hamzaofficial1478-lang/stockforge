"""A paced worker.

Your point about load balancing, taken seriously. The old `run` command blasted
through the queue as fast as it could, which on a local GPU means the fans spin
up, everything else on the machine crawls, and one bad design can wedge the
whole batch.

This works differently. One small unit at a time, a breather between units, and
a pace you can change while it runs. It is meant to be left going all day and
forgotten about, not babysat.

Each design is broken into steps that are individually cheap:

    read -> derive -> check -> polish -> render -> export

Pause and Stop finish the current design before taking effect. Finished
designs are saved; Start processes the remaining pending designs.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum

from .config import Settings, settings as default_settings
from .pipeline import Pipeline
from .db import FIT_TO_LEARN_FROM, Store

log = logging.getLogger("stockforge.worker")


class State(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSING = "pausing"
    PAUSED = "paused"
    STOPPED = "stopped"
    STOPPING = "stopping"
    FAILED = "failed"


@dataclass
class Progress:
    state: State = State.IDLE
    current_design: str | None = None
    current_design_id: str | None = None
    current_step: str = ""
    step_started_at: float | None = None
    done: int = 0
    failed: int = 0
    review: int = 0
    remaining: int = 0
    started_at: float | None = None
    finished_at: float | None = None
    last_error: str = ""
    model: str = ""
    recent: list[str] = field(default_factory=list)
    events: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        elapsed = ((self.finished_at or time.time()) - self.started_at) if self.started_at else 0
        rate = (self.done / elapsed * 3600) if elapsed > 60 and self.done else 0
        return {
            "state": self.state.value,
            "current_design": self.current_design,
            "current_design_id": self.current_design_id,
            "current_step": self.current_step,
            "step_elapsed_s": int(time.time() - self.step_started_at) if self.step_started_at else 0,
            "done": self.done,
            "failed": self.failed,
            "review": self.review,
            "remaining": self.remaining,
            "elapsed_s": int(elapsed),
            "per_hour": round(rate, 1),
            "eta_s": int(self.remaining / rate * 3600) if rate else None,
            "last_error": self.last_error,
            "model": self.model,
            "recent": self.recent[-12:],
            "events": self.events[-30:],
        }


def lane_providers(cfg: Settings, lanes: int) -> list:
    """What each lane talks to: its own model first, the others behind it.

    Two jobs, and they were confused for one. Lanes spread *different* designs
    across models, which buys throughput across a catalogue. Failover hands
    *one* design to another model when the first stops answering, which is what
    saves a design instead of losing it — and with a single lane, which is how
    most runs go, lanes bought nothing at all while a perfectly good second
    model sat idle and the design failed:

        failed — meta/muse-glimmer-30b@... could not read a response:
                 The read operation timed out

    So every lane gets the whole list, rotated so it starts on a different
    model. One lane and two models is now a chain rather than a single point of
    failure; four lanes and two models still spread the work.

    Returns a list of providers, or None entries meaning "use whatever the
    environment says", which is what a run with one model has always done.
    """
    lanes = max(1, lanes)
    try:
        # The connection store lives under ui/ because that is where it is
        # edited, but it is the record of which models exist, so this is the
        # right place to read it from.
        from .ui import models as connections
        from .providers.failover import chain
        from .providers.openai_compat import OpenAICompatProvider
    except Exception:                                       # pragma: no cover
        return [None] * lanes

    # The ones made live, not every one ever saved. Somebody who added four and
    # made two live meant those two — handing a lane a model that was tested
    # and deliberately stood down would be doing the opposite of what they said.
    saved = [c for c in connections.live(cfg.root, "vision") if c.model and c.base_url]
    if not saved:                                   # nothing live: fall back
        saved = [c for c in connections.load(cfg.root)
                 if c.role == "vision" and c.model and c.base_url]

    built = []
    for c in saved:
        try:
            built.append(OpenAICompatProvider(
                base_url=c.base_url, model=c.model, api_key=c.api_key or None,
                silence=cfg.silence))
        except Exception as exc:                            # pragma: no cover
            log.warning("lane provider %s unusable: %s", c.model, exc)

    # One model, or none: nothing to fall back to, so leave the environment
    # alone. It is already pointed at the live connection.
    if len(built) < 2:
        return [None] * lanes

    if len(built) < lanes:
        log.info("%d lanes across %d vision models — some will share",
                 lanes, len(built))
    return [chain(built[i % len(built):] + built[:i % len(built)])
            for i in range(lanes)]


class Worker:
    """Designs in parallel lanes, on a leash you hold.

    One lane is the original behaviour exactly: a design at a time with a
    breather between. More than one runs that same loop side by side, each on
    its own model and its own database connection, taking designs from the
    queue by claiming them so no two lanes ever build the same one.
    """

    def __init__(self, cfg: Settings | None = None):
        self.cfg = cfg or default_settings
        self.progress = Progress()
        self.lanes: list[Progress] = []
        self.pace_seconds = 2.0          # breather between designs
        self._thread: threading.Thread | None = None
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._lock = threading.Lock()
        # A limit is a limit on the run, not on each lane — "run 3" with two
        # lanes should build three designs, not six.
        self._claimed = 0

    # -- controls ---------------------------------------------------------

    def start(self, limit: int | None = None) -> bool:
        with self._lock:
            if self._thread and self._thread.is_alive():
                if self.progress.state is State.PAUSED:
                    self._pause.clear()
                    self.progress.state = State.RUNNING
                    return True
                return False
            self._stop.clear()
            self._pause.clear()
            self.progress = Progress(state=State.RUNNING, started_at=time.time())
            self._claimed = 0
            count = max(1, min(8, self.cfg.workers))
            providers_for_lanes = lane_providers(self.cfg, count)
            self.lanes = [Progress(state=State.RUNNING, started_at=time.time())
                          for _ in range(count)]
            self._report(
                "Starting — loading the queue" if count == 1
                else f"Starting {count} lanes — loading the queue")

            # Anything left 'building' belonged to a run that did not finish.
            # Freed here rather than on a timer: a design nothing will pick up
            # again is worse than one built twice.
            try:
                store = Store(self.cfg.db_path)
                store.reclaim_abandoned(older_than=0.0)
                store.conn.close()
            except Exception as exc:
                log.warning("could not reclaim abandoned designs: %s", exc)

            self._threads = []
            for index in range(count):
                thread = threading.Thread(
                    target=self._loop,
                    args=(limit, index, providers_for_lanes[index]),
                    daemon=True, name=f"stockforge-lane-{index}")
                thread.start()
                self._threads.append(thread)
            self._thread = self._threads[0]
            return True

    def pause(self) -> None:
        if self.progress.state is State.RUNNING:
            self._pause.set()
            self.progress.state = State.PAUSING

    def resume(self) -> None:
        self._pause.clear()
        if self.progress.state in (State.PAUSED, State.PAUSING):
            self.progress.state = State.RUNNING

    def stop(self) -> None:
        self._stop.set()
        self._pause.clear()
        if self.alive:
            self.progress.state = State.STOPPING

    def set_pace(self, seconds: float) -> None:
        self.pace_seconds = max(0.0, min(120.0, seconds))

    @property
    def alive(self) -> bool:
        return any(t.is_alive() for t in self._threads)

    @property
    def running_lanes(self) -> int:
        return sum(1 for t in self._threads if t.is_alive())

    def snapshot(self) -> dict:
        """What the whole run is doing, plus each lane separately.

        The totals are summed across lanes rather than kept on the parent, so
        they cannot drift: there is one place each number comes from.
        """
        self._sync()
        result = self.progress.as_dict()

        elapsed = result["elapsed_s"] or 0
        rate = (result["done"] / elapsed * 3600) if elapsed > 60 and result["done"] else 0
        result["per_hour"] = round(rate, 1)

        building = [l for l in self.lanes if l.current_design]
        if len(building) == 1:
            result["current_step"] = building[0].current_step

        store = Store(self.cfg.db_path)
        try:
            pending = store.conn.execute(
                "SELECT COUNT(*) n FROM designs WHERE state='pending'").fetchone()["n"]
            # Where the niche is up to. Without this the run looks like it is
            # producing finished designs when it is reading a catalogue in, and
            # the owner has no way to tell the difference from the outside —
            # which is exactly the confusion that made them ask.
            result["learning"] = self._learning(store)
        finally:
            store.conn.close()
        result["remaining"] = pending
        result["eta_s"] = int(pending / rate * 3600) if rate else None

        result["lanes"] = [
            {"lane": i + 1, **l.as_dict()} for i, l in enumerate(self.lanes)
        ]
        result["lane_count"] = len(self.lanes)
        result["lanes_running"] = self.running_lanes
        return result

    def _learning(self, store: Store) -> dict | None:
        """How far off this niche is from being drawn from, or None once it is
        there. One line on screen, because the answer is one number."""
        niche = self.cfg.collection
        if not niche:
            return None
        # The same count the gate uses, from the same definition, because a
        # screen that says twenty-four while the gate says twenty is worse than
        # no screen at all.
        row = store.conn.execute(
            f"SELECT SUM(CASE WHEN {FIT_TO_LEARN_FROM} THEN 1 ELSE 0 END) AS read "
            "FROM designs d LEFT JOIN specs s ON s.design_id = d.id "
            "WHERE d.collection = ?", (niche,)).fetchone()
        read = (row["read"] if row else 0) or 0
        if read >= self.cfg.seed_designs:
            return None
        record = store.collection(niche)
        return {"niche": (record or {}).get("name") or niche,
                "read": read, "needs": self.cfg.seed_designs}

    # -- the loop ---------------------------------------------------------

    def _report(self, step: str, lane: "Progress | None" = None) -> None:
        target = lane if lane is not None else self.progress
        target.current_step = step
        target.step_started_at = time.time()
        stamp = time.strftime('%H:%M:%S')
        prefix = "" if len(self.lanes) < 2 else f"lane {self.lanes.index(target) + 1}  " \
            if target in self.lanes else ""
        target.events = (target.events + [f"{stamp}  {step}"])[-30:]
        if target is not self.progress:
            with self._lock:
                self.progress.events = (
                    self.progress.events + [f"{stamp}  {prefix}{step}"])[-30:]

    def _sync(self) -> None:
        """Roll the lanes' counters up into the run's own.

        `worker.progress.done` has to keep meaning what it always meant — the
        number of designs this run finished — whether there is one lane or
        four. Leaving the totals only in snapshot() would make the object read
        zero while work was plainly happening, which is the kind of quiet lie
        that costs an hour to track down.
        """
        if not self.lanes:
            return
        with self._lock:
            for key in ("done", "failed", "review"):
                setattr(self.progress, key, sum(getattr(l, key) for l in self.lanes))
            building = [l for l in self.lanes if l.current_design]
            self.progress.current_design = (
                building[0].current_design if len(building) == 1
                else (f"{len(building)} designs at once" if building else None))
            self.progress.current_design_id = (
                building[0].current_design_id if len(building) == 1 else None)

    def _settle(self) -> None:
        """When the last lane finishes, say what the run as a whole did."""
        self._sync()
        with self._lock:
            if any(t.is_alive() for t in self._threads if t is not threading.current_thread()):
                return
            self.progress.finished_at = time.time()
            self.progress.current_design = None
            self.progress.current_design_id = None
            if any(l.state is State.FAILED for l in self.lanes):
                self.progress.state = State.FAILED
            elif self._stop.is_set():
                self.progress.state = State.STOPPED
            else:
                self.progress.state = State.IDLE

    def _wait_if_paused(self, lane: "Progress | None" = None) -> bool:
        """Returns False if we should stop entirely."""
        target = lane if lane is not None else self.progress
        while self._pause.is_set() and not self._stop.is_set():
            target.state = State.PAUSED
            target.current_step = "paused"
            self.progress.state = State.PAUSED
            time.sleep(0.4)
        if self._stop.is_set():
            return False
        if target.state is State.PAUSED:
            target.state = State.RUNNING
            self.progress.state = State.RUNNING
        return True

    def _loop(self, limit: int | None, index: int = 0, provider=None) -> None:
        lane = self.lanes[index] if index < len(self.lanes) else self.progress
        pipe = None
        if provider is not None:
            # This lane's model, for this thread only. The analysis stages ask
            # providers.vision() and get this one without knowing why.
            from . import providers as provider_registry
            provider_registry.use_in_this_thread("vision", provider)
            provider_registry.use_in_this_thread("reason", provider)
            # A switch has to be visible. A model going quiet and another
            # picking the design up looks, from the queue, exactly like one
            # design taking an unexplained age — so it is said out loud, and
            # the lane's model label follows the one actually doing the work.
            def switched(taking_over, why, lane=lane):
                lane.model = getattr(taking_over, "name", "")
                self._report(f"{why} — handed to {lane.model}", lane)

            if hasattr(provider, "on_switch"):
                provider.on_switch = switched
            lane.model = getattr(getattr(provider, "first", provider), "name", "")
        try:
            pipe = Pipeline(self.cfg, on_progress=lambda step: self._report(step, lane))
            self._run(pipe, limit, lane, index)
        except Exception as exc:
            log.exception("lane %d stopped unexpectedly", index)
            lane.last_error = str(exc)[:1000]
            lane.state = State.FAILED
            self.progress.last_error = lane.last_error
            self._report("Worker failed — fix the error below and press Start again", lane)
        finally:
            lane.finished_at = time.time()
            lane.current_design = None
            lane.current_design_id = None
            if lane.state is State.RUNNING:
                lane.state = State.IDLE
            if pipe is not None:
                pipe.store.conn.close()
            self._settle()

    def _run(self, pipe: Pipeline, limit: int | None,
             lane: "Progress", index: int = 0) -> None:
        processed = 0
        name = f"lane-{index}"

        while not self._stop.is_set():
            if not self._wait_if_paused(lane):
                break
            if limit and self._claimed_total() >= limit:
                self._report(f"reached the limit of {limit}", lane)
                lane.state = State.IDLE
                break

            # Claim, don't browse. Reading "the first pending design" from two
            # lanes hands both the same one; claiming hands it to exactly one
            # and the other moves on to the next.
            row = pipe.store.claim_design(name)
            if row is None:
                self._report("Queue complete" if processed
                             else "No pending designs — add images in Sources", lane)
                lane.state = State.IDLE
                break

            with self._lock:
                self._claimed += 1
            did = row["id"]
            lane.current_design = row["design_key"] or did[:12]
            lane.current_design_id = did
            lane.remaining = self._pending(pipe)
            self._sync()
            self._report("Preparing design", lane)

            try:
                result = pipe.build(did)
            except Exception as exc:
                log.exception("[%s] failed", did[:8])
                pipe.store.set_design_state(did, "failed")
                pipe.store.queue_review(did, f"exception: {exc}", 0.0)
                lane.failed += 1
                lane.last_error = str(exc)[:1000]
                self.progress.last_error = lane.last_error
                result = "failed"
            else:
                if result == "review":
                    lane.review += 1
                elif result in ("ready", "master_only"):
                    lane.done += 1
                else:
                    lane.failed += 1

            pipe.store.set_design_state(did, result)
            if result in ("review", "failed"):
                reason = pipe.store.conn.execute(
                    "SELECT reason FROM review WHERE design_id=?", (did,)).fetchone()
                if reason:
                    self._report(f"{result}: {reason['reason']}", lane)
                    if result == "failed":
                        lane.last_error = reason["reason"][:1000]
                        self.progress.last_error = lane.last_error

            processed += 1
            self._sync()
            lane.recent.append(f"{lane.current_design} -> {result}")
            with self._lock:
                self.progress.recent = (
                    self.progress.recent + [f"{lane.current_design} -> {result}"])[-24:]
            lane.remaining = self._pending(pipe)
            lane.current_design_id = None
            lane.current_design = None
            self._sync()
            self._report(f"Design finished: {result}", lane)

            # the breather. This is what makes it liveable to leave running.
            slept = 0.0
            while slept < self.pace_seconds and not self._stop.is_set():
                time.sleep(0.2)
                slept += 0.2

        lane.current_design = None
        if self._stop.is_set():
            # Anything this lane had in flight goes back, or a stop would take
            # designs out of the queue permanently.
            if lane.current_design_id:
                pipe.store.release_design(lane.current_design_id)
            lane.state = State.STOPPED
            self._report("Stopped after the current design; pending designs are saved", lane)

    def _pending(self, pipe: Pipeline) -> int:
        return pipe.store.conn.execute(
            "SELECT COUNT(*) n FROM designs WHERE state='pending'").fetchone()["n"]

    def _claimed_total(self) -> int:
        with self._lock:
            return self._claimed


# one worker per process, shared by the UI and the CLI
_worker: Worker | None = None


def get_worker(cfg: Settings | None = None) -> Worker:
    global _worker
    if _worker is None:
        _worker = Worker(cfg)
    return _worker
