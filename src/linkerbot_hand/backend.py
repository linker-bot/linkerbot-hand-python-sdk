"""Injection contracts for protocol and transport implementations."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from .types import DeviceIdentity, HandCommand, SensorType, SensorValue


@dataclass(frozen=True, slots=True)
class BackendReading:
    """One sensor transaction returned by a protocol backend.

    Attributes:
        sensor: Sensor channel that was queried.
        values: Normalized values exposed by the unified SDK.
        raw_values: Exact protocol values when distinct from ``values``.
    """

    sensor: SensorType
    values: tuple[SensorValue, ...]
    raw_values: tuple[int, ...] | None = None


@dataclass(frozen=True, slots=True)
class DispatchResult:
    """Protocol outcome returned after a successful transport dispatch.

    Attributes:
        acknowledged: ``True`` only for an explicit positive device ACK;
            ``False`` for an explicit rejection and ``None`` when unsupported.
    """

    acknowledged: bool | None = None


class HandBackend(Protocol):
    """Model and protocol implementation injected into a :class:`HandSession`."""

    def open(self) -> DeviceIdentity:
        """Open transport resources and read device identity.

        Returns:
            Identity verified directly from the connected device.

        Raises:
            HandSDKError: If resources cannot be opened or identity cannot be
                verified.
        """

    def close(self) -> None:
        """Interrupt blocking I/O and close resources idempotently."""

    def dispatch(
        self,
        command: HandCommand,
        *,
        claim_dispatch: Callable[[], bool],
        mark_dispatched: Callable[[], None],
    ) -> DispatchResult:
        """Dispatch one command through the transport.

        The backend must call ``claim_dispatch`` immediately before its first
        transport send. If claiming returns ``False``, it must send nothing.
        It must call ``mark_dispatched`` only after the transport reports a
        successful send.

        Args:
            command: Validated command in the device capability units.
            claim_dispatch: Atomic latest-wins claim callback.
            mark_dispatched: Callback that records transport send success.

        Returns:
            Protocol ACK outcome after dispatch.

        Raises:
            HandSDKError: If encoding, transport, or device communication fails.
        """

    def poll(self, sensor: SensorType) -> BackendReading:
        """Perform one blocking sensor transaction for the SDK poller.

        Args:
            sensor: Supported sensor channel to query.

        Returns:
            Reading for the requested sensor.

        Raises:
            HandSDKError: If the transaction or response validation fails.
        """


class Clock(Protocol):
    """Injectable clock used by scheduling and deterministic tests."""

    def monotonic(self) -> float:
        """Return monotonic seconds for intervals and deadlines."""
        ...

    def time(self) -> float:
        """Return wall-clock seconds for externally visible timestamps."""
        ...

    def wait(self, event: threading.Event, timeout: float | None) -> bool:
        """Wait for an event using the clock's notion of elapsed time.

        Args:
            event: Event that ends the wait when set.
            timeout: Maximum seconds to wait, or ``None`` without a deadline.

        Returns:
            Whether the event was set before the deadline.
        """
        ...


class SystemClock:
    """Production clock backed by :mod:`time` and threading events."""

    def monotonic(self) -> float:
        """Return system monotonic time in seconds."""
        return time.monotonic()

    def time(self) -> float:
        """Return system wall-clock time in seconds."""
        return time.time()

    def wait(self, event: threading.Event, timeout: float | None) -> bool:
        """Wait on a threading event for up to ``timeout`` seconds."""
        return event.wait(timeout)


BackendFactory = Callable[[], HandBackend]


__all__ = [
    "BackendFactory",
    "BackendReading",
    "Clock",
    "DispatchResult",
    "HandBackend",
    "SystemClock",
]
