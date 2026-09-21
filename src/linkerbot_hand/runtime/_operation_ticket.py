"""Thread-safe completion ticket for bounded lifecycle operations."""

from __future__ import annotations

import threading

from ..api.errors import HandError


class OperationTicket:
    """One lifecycle result shared by concurrent blocking callers."""

    def __init__(self, *, deadline_at_s: float | None = None) -> None:
        self.event = threading.Event()
        self._lock = threading.Lock()
        self._error: HandError | None = None
        self._deadline_at_s = deadline_at_s

    @property
    def deadline_at_s(self) -> float | None:
        """Return the open budget extended by actual initialization stages."""

        with self._lock:
            return self._deadline_at_s

    def add_stage_budget(self, timeout_s: float) -> None:
        """Reserve a stage's wait before dispatch, without resetting elapsed time."""

        with self._lock:
            if self._deadline_at_s is not None and not self.event.is_set():
                self._deadline_at_s += timeout_s

    @property
    def error(self) -> HandError | None:
        """Return the first terminal failure, if any."""

        with self._lock:
            return self._error

    def succeed(self) -> None:
        """Complete successfully unless another result already won."""

        with self._lock:
            if self.event.is_set():
                return
            self.event.set()

    def fail(self, error: HandError) -> None:
        """Complete with the first stable Hand API error."""

        with self._lock:
            if self.event.is_set():
                return
            self._error = error
            self.event.set()


__all__ = ["OperationTicket"]
