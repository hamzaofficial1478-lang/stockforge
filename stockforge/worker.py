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

State lives in the database after every step, so a stop is never a loss — the
next start picks up the step it had not reached yet.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .config import Settings, settings as default_settings
from .pipeline import Pipeline

log = logging.getLogger("stockforge.worker")


class State(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSING = "pausing"
    PAUSED = "paused"
    STOPPED = "stopped"


@dataclass
class Progress:
    state: State = State.IDLE
    current_design: str | None = None
    current_step: str = ""
    done: int = 0
    failed: int = 0
    review: int = 0
    remaining: int = 0
    started_at: float | None = None
    last_error: str = ""
    recent: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        elapsed = (time.time() - self.started_at) if self.started_at else 0
        rate = (self.done / elapsed * 3600) if elapsed > 60 and self.done else 0
        return {
            "state": self.state.value,
            "current_design": self.current_design,
            "current_step": self.current_step,
            "done": self.done,
            "failed": self.failed,
            "review": self.review,
            "remaining": self.remaining,
            "elapsed_s": int(elapsed),
            "per_hour": round(rate, 1),
            "eta_s": int(self.remaining / rate * 3600) if rate else None,
            "last_error": self.last_error,
            "recent": self.recent[-12:],
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

    def set_pace(self, seconds: float) -> None:
        self.pace_seconds = max(0.0, min(120.0, seconds))

    @property
    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # -- the loop ---------------------------------------------------------

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
        pipe = Pipeline(self.cfg)
        processed = 0

        while not self._stop.is_set():
            if not self._wait_if_paused():
                break

            pending = pipe.store.designs(state="pending")
            self.progress.remaining = len(pending)
            if not pending:
                self.progress.current_step = "nothing pending"
                self.progress.state = State.IDLE
                break
            if limit and processed >= limit:
                self.progress.current_step = f"reached the limit of {limit}"
                self.progress.state = State.IDLE
                break

            row = pending[0]
            did = row["id"]
            self.progress.current_design = row["design_key"] or did[:12]
            self.progress.current_step = "reading"

            try:
                result = pipe.build(did)
            except Exception as exc:
                log.exception("[%s] failed", did[:8])
                pipe.store.set_design_state(did, "failed")
                pipe.store.queue_review(did, f"exception: {exc}", 0.0)
                self.progress.failed += 1
                self.progress.last_error = str(exc)[:300]
                result = "failed"
            else:
                if result == "review":
                    self.progress.review += 1
                elif result in ("ready", "master_only"):
                    self.progress.done += 1
                else:
                    self.progress.failed += 1

            processed += 1
            self.progress.recent.append(f"{self.progress.current_design} -> {result}")
            self.progress.current_step = "resting"

            # the breather. This is what makes it liveable to leave running.
            slept = 0.0
            while slept < self.pace_seconds and not self._stop.is_set():
                time.sleep(0.2)
                slept += 0.2

        self.progress.current_design = None
        if self._stop.is_set():
            self.progress.state = State.STOPPED
            self.progress.current_step = "stopped"


# one worker per process, shared by the UI and the CLI
_worker: Worker | None = None


def get_worker(cfg: Settings | None = None) -> Worker:
    global _worker
    if _worker is None:
        _worker = Worker(cfg)
    return _worker
