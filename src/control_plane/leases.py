from __future__ import annotations

from collections.abc import Callable
from threading import Event, Thread


class LeaseHeartbeat:
    """Runs a bounded background heartbeat while external work is in flight."""

    def __init__(self, heartbeat: Callable[[], None], *, interval_seconds: float) -> None:
        if interval_seconds <= 0:
            raise ValueError("heartbeat interval must be positive")
        self.heartbeat = heartbeat
        self.interval_seconds = interval_seconds
        self._stop = Event()
        self._thread: Thread | None = None
        self.last_error: Exception | None = None

    def __enter__(self) -> LeaseHeartbeat:
        self._thread = Thread(target=self._run, name="task-lease-heartbeat", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=min(self.interval_seconds, 5.0))

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self.heartbeat()
                self.last_error = None
            except Exception as exc:  # finalization remains the authority check
                self.last_error = exc
