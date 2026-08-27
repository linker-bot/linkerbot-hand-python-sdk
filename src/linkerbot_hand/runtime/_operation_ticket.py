"""Thread-safe completion ticket for bounded lifecycle operations."""

from __future__ import annotations

import threading

from ..api.errors import HandError


class OperationTicket:
    """One lifecycle result shared by concurrent blocking callers."""

    def __init__(self) -> None:
        self.event = threading.Event()
        self._lock = threading.Lock()
        self._error: HandError | None = None

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
