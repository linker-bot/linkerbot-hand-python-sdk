"""Injectable clock boundary shared by runtime schedulers."""

from __future__ import annotations

import threading
import time
from typing import Protocol, cast

from ..api.errors import ValidationError


class RuntimeClock(Protocol):
    """Wall, monotonic, and interruptible wait operations used by the runtime."""

    def monotonic(self) -> float: ...

    def time(self) -> float: ...

    def wait(self, event: threading.Event, timeout: float | None) -> bool: ...


class SystemClock:
    """Production clock implemented by the Python standard library."""

    @staticmethod
    def monotonic() -> float:
        return time.monotonic()

    @staticmethod
    def time() -> float:
        return time.time()

    @staticmethod
    def wait(event: threading.Event, timeout: float | None) -> bool:
        return event.wait(timeout)


def resolve_clock(clock: object | None) -> RuntimeClock:
    """Validate an injected clock without reading it during construction."""

    if clock is None:
        return SystemClock()
    for name in ("monotonic", "time", "wait"):
        if not callable(getattr(clock, name, None)):
            raise ValidationError(f"clock must provide callable {name}()")
    return cast(RuntimeClock, clock)


__all__ = ["RuntimeClock", "SystemClock", "resolve_clock"]
