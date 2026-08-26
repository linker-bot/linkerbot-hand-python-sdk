"""Deterministic fake backend and clock for application-level tests."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field

from .backend import BackendReading, DispatchResult
from .errors import ClosedError, TransportError
from .types import (
    DeviceCapabilities,
    DeviceIdentity,
    HandCommand,
    SensorType,
    SensorValue,
)


class ManualClock:
    """Thread-safe wall/monotonic clock advanced explicitly by tests."""

    def __init__(self, *, monotonic: float = 0.0, wall_time: float = 0.0) -> None:
        """Initialize independently controlled monotonic and wall clocks.

        Args:
            monotonic: Initial monotonic timestamp in seconds.
            wall_time: Initial wall-clock timestamp in seconds.
        """
        self._monotonic = monotonic
        self._wall_time = wall_time
        self._condition = threading.Condition()

    def monotonic(self) -> float:
        """Return the current virtual monotonic timestamp."""
        with self._condition:
            return self._monotonic

    def time(self) -> float:
        """Return the current virtual wall-clock timestamp."""
        with self._condition:
            return self._wall_time

    def advance(self, seconds: float) -> None:
        """Advance both virtual clocks and wake waiting threads.

        Args:
            seconds: Amount added to both clocks.
        """
        with self._condition:
            self._monotonic += seconds
            self._wall_time += seconds
            self._condition.notify_all()

    def wait(self, event: threading.Event, timeout: float | None) -> bool:
        """Wait until an event is set or virtual time reaches the deadline.

        Args:
            event: Event that ends the wait when set.
            timeout: Virtual seconds to wait, or ``None`` without a deadline.

        Returns:
            Whether ``event`` was set before the virtual deadline.
        """
        with self._condition:
            deadline = None if timeout is None else self._monotonic + timeout
            while not event.is_set():
                if deadline is not None and self._monotonic >= deadline:
                    break
                self._condition.wait(timeout=0.001)
            return event.is_set()


@dataclass(slots=True)
class FakeBackendState:
    """Observable state shared across fake backend connection generations.

    Attributes:
        identity: Identity returned by successful opens.
        acknowledge_commands: Whether dispatch returns a positive protocol ACK.
        connected: Simulated physical transport connection state.
        readings: Sensor readings returned by polling.
        open_count: Number of successful fresh opens.
        close_count: Number of opened backends subsequently closed.
        active_connections: Currently open fake backend count.
        max_active_connections: Highest concurrent open backend count observed.
        dispatched_commands: Commands whose simulated transport send succeeded.
        poll_log: Sensor channels queried in order.
        dispatch_errors: Exceptions consumed by upcoming dispatch calls.
        poll_errors: Exceptions consumed by upcoming poll calls.
        lock: Lock protecting all mutable fake transport state.
    """

    identity: DeviceIdentity
    acknowledge_commands: bool = False
    connected: bool = True
    readings: dict[SensorType, BackendReading] = field(default_factory=dict)
    open_count: int = 0
    close_count: int = 0
    active_connections: int = 0
    max_active_connections: int = 0
    dispatched_commands: list[HandCommand] = field(default_factory=list)
    poll_log: list[SensorType] = field(default_factory=list)
    dispatch_errors: deque[BaseException] = field(default_factory=deque)
    poll_errors: deque[BaseException] = field(default_factory=deque)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def io_count(self) -> int:
        """Return recorded command dispatches plus sensor poll attempts."""
        with self.lock:
            return len(self.dispatched_commands) + len(self.poll_log)

    def set_reading(
        self,
        sensor: SensorType,
        values: tuple[SensorValue, ...],
    ) -> None:
        """Configure the reading returned for a sensor.

        Args:
            sensor: Sensor channel to configure.
            values: Decoded protocol-native values returned to the session.
        """
        with self.lock:
            self.readings[sensor] = BackendReading(sensor, values)

    def disconnect(self) -> None:
        """Make subsequent fake transport I/O fail."""
        with self.lock:
            self.connected = False

    def reconnect(self) -> None:
        """Allow subsequent fake transport opens and I/O to succeed."""
        with self.lock:
            self.connected = True


class FakeBackend:
    """Synchronous backend with observable I/O and fault injection."""

    def __init__(self, state: FakeBackendState) -> None:
        """Bind one backend generation to shared observable state.

        Args:
            state: State shared with the factory and other generations.
        """
        self.state = state
        self._open = False

    def open(self) -> DeviceIdentity:
        """Open this generation and return configured device identity.

        Returns:
            Configured fake identity.

        Raises:
            TransportError: If the fake transport is disconnected.
        """
        with self.state.lock:
            if not self.state.connected:
                raise TransportError("fake transport is disconnected")
            if self._open:
                return self.state.identity
            self._open = True
            self.state.open_count += 1
            self.state.active_connections += 1
            self.state.max_active_connections = max(
                self.state.max_active_connections, self.state.active_connections
            )
            return self.state.identity

    def close(self) -> None:
        """Close this fake backend generation idempotently."""
        with self.state.lock:
            if not self._open:
                return
            self._open = False
            self.state.close_count += 1
            self.state.active_connections -= 1

    def dispatch(self, command, *, claim_dispatch, mark_dispatched):
        """Record a claimed command and inject any queued dispatch failure."""
        if not claim_dispatch():
            return DispatchResult()
        with self.state.lock:
            self._ensure_ready_locked()
            if self.state.dispatch_errors:
                raise self.state.dispatch_errors.popleft()
            self.state.dispatched_commands.append(command)
            acknowledged = self.state.acknowledge_commands
        mark_dispatched()
        return DispatchResult(acknowledged=True if acknowledged else None)

    def poll(self, sensor: SensorType) -> BackendReading:
        """Record a sensor query and return its configured fake reading."""
        with self.state.lock:
            self._ensure_ready_locked()
            self.state.poll_log.append(sensor)
            if self.state.poll_errors:
                raise self.state.poll_errors.popleft()
            reading = self.state.readings.get(sensor)
            if reading is not None:
                return reading
            return BackendReading(sensor=sensor, values=())

    def _ensure_ready_locked(self) -> None:
        if not self._open:
            raise ClosedError("fake backend is closed")
        if not self.state.connected:
            raise TransportError("fake transport is disconnected")


class FakeBackendFactory:
    """Creates fresh backend generations over one observable fake transport."""

    def __init__(
        self,
        capabilities: DeviceCapabilities,
        *,
        state: FakeBackendState | None = None,
    ) -> None:
        """Configure a callable that creates fresh fake backend generations.

        Args:
            capabilities: Capabilities used to derive a default fake identity.
            state: Optional preconfigured shared state for fault injection.
        """
        self.capabilities = capabilities
        self.state = state or FakeBackendState(
            identity=DeviceIdentity(
                model=capabilities.model,
                firmware=capabilities.firmware or "fake-1.0",
                side=capabilities.side,
                serial_number="FAKE-0001",
            ),
            acknowledge_commands=capabilities.features.device_ack,
        )

    def __call__(self) -> FakeBackend:
        """Create a closed fake backend over the shared state."""
        return FakeBackend(self.state)


def wait_until(predicate, *, timeout_s: float = 1.0) -> None:
    """Wait for a scheduler assertion without tying tests to sleep duration.

    Args:
        predicate: Zero-argument callable that returns truthy on success.
        timeout_s: Real-time test deadline in seconds.

    Raises:
        TimeoutError: If ``predicate`` remains false through the deadline.
    """
    deadline = time.monotonic() + timeout_s
    while not predicate():
        if time.monotonic() >= deadline:
            raise TimeoutError("condition was not met before the test deadline")
        time.sleep(0.001)


__all__ = [
    "FakeBackend",
    "FakeBackendFactory",
    "FakeBackendState",
    "ManualClock",
    "wait_until",
]
