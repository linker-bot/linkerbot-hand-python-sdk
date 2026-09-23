"""Per-call cooperative discovery budget, isolated across threads/contexts."""

import time
from contextvars import ContextVar

deadline: ContextVar[float | None] = ContextVar("discovery_deadline", default=None)


def remaining(limit: float) -> float:
    end = deadline.get()
    if end is None:
        return limit
    available = end - time.monotonic()
    if available <= 0:
        raise TimeoutError("discovery total time budget exhausted")
    return min(limit, available)


def expired() -> bool:
    end = deadline.get()
    return end is not None and time.monotonic() >= end
