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
from .db import Store

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
            "recent": self.recent[-12:],
            "events": self.events[-30:],
        }


class Worker:
    """One design at a time, on a leash you hold."""

    def __init__(self, cfg: Settings | None = None):
        self.cfg = cfg or default_settings
        self.progress = Progress()
        self.pace_seconds = 2.0          # breather between designs
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._lock = threading.Lock()

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
            self._report("Starting — loading the queue")
            self._thread = threading.Thread(target=self._loop, args=(limit,), daemon=True)
            self._thread.start()
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
        return bool(self._thread and self._thread.is_alive())

    def snapshot(self) -> dict:
        result = self.progress.as_dict()
        if not self.alive:
            store = Store(self.cfg.db_path)
            try:
                result["remaining"] = len(store.designs(state="pending"))
            finally:
                store.conn.close()
        return result

    # -- the loop ---------------------------------------------------------

    def _report(self, step: str) -> None:
        self.progress.current_step = step
        self.progress.step_started_at = time.time()
        self.progress.events = (self.progress.events + [f"{time.strftime('%H:%M:%S')}  {step}"])[-30:]

    def _wait_if_paused(self) -> bool:
        """Returns False if we should stop entirely."""
        while self._pause.is_set() and not self._stop.is_set():
            self.progress.state = State.PAUSED
            self.progress.current_step = "paused"
            time.sleep(0.4)
        if self._stop.is_set():
            return False
        if self.progress.state is State.PAUSED:
            self.progress.state = State.RUNNING
        return True

    def _loop(self, limit: int | None) -> None:
        pipe = None
        try:
            pipe = Pipeline(self.cfg, on_progress=self._report)
            self._run(pipe, limit)
        except Exception as exc:
            log.exception("worker stopped unexpectedly")
            self.progress.last_error = str(exc)[:1000]
            self.progress.state = State.FAILED
            self._report("Worker failed — fix the error below and press Start again")
        finally:
            self.progress.finished_at = time.time()
            self.progress.current_design = None
            self.progress.current_design_id = None
            if pipe is not None:
                pipe.store.conn.close()

    def _run(self, pipe: Pipeline, limit: int | None) -> None:
        processed = 0

        while not self._stop.is_set():
            if not self._wait_if_paused():
                break

            pending = pipe.store.designs(state="pending")
            self.progress.remaining = len(pending)
            if not pending:
                self._report("Queue complete" if processed else "No pending designs — add images in Sources")
                self.progress.state = State.IDLE
                break
            if limit and processed >= limit:
                self.progress.current_step = f"reached the limit of {limit}"
                self.progress.state = State.IDLE
                break

            row = pending[0]
            did = row["id"]
            self.progress.current_design = row["design_key"] or did[:12]
            self.progress.current_design_id = did
            self._report("Preparing design")

            try:
                result = pipe.build(did)
            except Exception as exc:
                log.exception("[%s] failed", did[:8])
                pipe.store.set_design_state(did, "failed")
                pipe.store.queue_review(did, f"exception: {exc}", 0.0)
                self.progress.failed += 1
                self.progress.last_error = str(exc)[:1000]
                result = "failed"
            else:
                if result == "review":
                    self.progress.review += 1
                elif result in ("ready", "master_only"):
                    self.progress.done += 1
                else:
                    self.progress.failed += 1

            pipe.store.set_design_state(did, result)
            if result in ("review", "failed"):
                reason = pipe.store.conn.execute(
                    "SELECT reason FROM review WHERE design_id=?", (did,)).fetchone()
                if reason:
                    self._report(f"{result}: {reason['reason']}")
                    if result == "failed":
                        self.progress.last_error = reason["reason"][:1000]

            processed += 1
            self.progress.recent.append(f"{self.progress.current_design} -> {result}")
            self.progress.remaining = max(0, len(pending) - 1)
            self.progress.current_design_id = None
            self.progress.current_design = None
            self._report(f"Design finished: {result}")

            # the breather. This is what makes it liveable to leave running.
            slept = 0.0
            while slept < self.pace_seconds and not self._stop.is_set():
                time.sleep(0.2)
                slept += 0.2

        self.progress.current_design = None
        if self._stop.is_set():
            self.progress.state = State.STOPPED
            self._report("Stopped after the current design; pending designs are saved")


# one worker per process, shared by the UI and the CLI
_worker: Worker | None = None


def get_worker(cfg: Settings | None = None) -> Worker:
    global _worker
    if _worker is None:
        _worker = Worker(cfg)
    return _worker
