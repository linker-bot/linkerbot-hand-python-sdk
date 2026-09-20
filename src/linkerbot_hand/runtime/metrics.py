"""Mutable runtime counters projected as immutable zero-I/O health."""

from __future__ import annotations

import math
import threading
from bisect import bisect_left, insort
from collections import deque

from linkerbot_transport import TransportHealth

from ..api.errors import ErrorInfo, HandError, ValidationError
from ..api.health import (
    ConnectionState,
    HealthStatus,
    LatencyMetrics,
    LifecycleState,
    TransportMetrics,
)
from ..capabilities.types import DeviceCapabilities
from .command_scheduler import CommandIngress
from .poll_scheduler import PollScheduler
from .snapshot_store import SnapshotStore


class RuntimeMetrics:
    """Single lock for lifecycle, success/error timestamps, and latency history."""

    def __init__(self, initial: HealthStatus) -> None:
        if not isinstance(initial, HealthStatus):
            raise ValidationError("initial must be HealthStatus")
        self._lock = threading.Lock()
        self._lifecycle_state = initial.lifecycle_state
        self._connection_state = initial.connection_state
        self._connected = initial.connected
        self._generation = initial.connection_generation
        self._identity_verified = initial.identity_verified
        self._reconnect_count = initial.reconnect_count
        self._last_send = initial.last_successful_send_at
        self._last_receive = initial.last_successful_receive_at
        self._last_transaction = initial.last_successful_transaction_at
        self._consecutive_errors = initial.consecutive_errors
        self._recent_error = initial.recent_error
        self._latencies: deque[float] = deque(maxlen=2048)
        self._ordered_latencies: list[float] = []
        self._latency_cache: LatencyMetrics | None = None

    def opening(self) -> None:
        """Publish the synchronous open transition."""

        self._set_connection(
            LifecycleState.OPENING,
            ConnectionState.CONNECTING,
            connected=False,
            verified=False,
        )

    def ready(self, *, generation: int, recovered: bool) -> None:
        """Publish a verified generation and reset consecutive errors."""

        with self._lock:
            self._lifecycle_state = LifecycleState.OPEN
            self._connection_state = ConnectionState.CONNECTED
            self._connected = True
            self._identity_verified = True
            self._generation = generation
            if recovered:
                self._reconnect_count += 1
            self._consecutive_errors = 0
            self._recent_error = None

    def recovering(self, error: HandError | None = None) -> None:
        """Publish a disconnected state while preserving the last generation."""

        with self._lock:
            self._lifecycle_state = LifecycleState.RECOVERING
            self._connection_state = ConnectionState.RECONNECTING
            self._connected = False
            self._identity_verified = False
            if error is not None:
                self._record_error_locked(error)

    def faulted(self, error: HandError) -> None:
        """Publish a stable fault and structured recent error."""

        with self._lock:
            self._lifecycle_state = LifecycleState.FAULTED
            self._connection_state = ConnectionState.FAULTED
            self._connected = False
            self._identity_verified = False
            self._record_error_locked(error)

    def closing(self) -> None:
        """Publish close admission before workers and routes stop."""

        self._set_connection(
            LifecycleState.CLOSING,
            ConnectionState.CLOSED,
            connected=False,
            verified=False,
        )

    def closed(self) -> None:
        """Publish complete bounded shutdown."""

        self._set_connection(
            LifecycleState.CLOSED,
            ConnectionState.CLOSED,
            connected=False,
            verified=False,
        )

    def sent(self, *, timestamp: float) -> None:
        """Record the latest device transaction dispatched by the transport.

        Dispatch is not success: clearing consecutive errors here would erase a
        prior response timeout as soon as the next request goes out. Only
        ``transaction_completed`` and ``ready`` may reset that counter.
        """

        with self._lock:
            self._last_send = timestamp

    def received(self, *, timestamp: float) -> None:
        """Record one frame accepted by this device's exact route."""

        with self._lock:
            self._last_receive = timestamp

    def transaction_completed(self, *, timestamp: float, duration_s: float) -> None:
        """Record one complete logical response and its latency."""

        if not math.isfinite(duration_s) or duration_s < 0:
            raise ValidationError("duration_s must be finite and non-negative")
        with self._lock:
            self._last_transaction = timestamp
            # Keep the same bounded window, but avoid sorting it on every read.
            # Moving at most 2048 list entries costs much less than a full sort.
            if len(self._latencies) == self._latencies.maxlen:
                oldest = self._latencies[0]
                self._ordered_latencies.pop(
                    bisect_left(self._ordered_latencies, oldest)
                )
            self._latencies.append(duration_s)
            insort(self._ordered_latencies, duration_s)
            self._latency_cache = None
            self._consecutive_errors = 0

    def record_error(self, error: HandError) -> None:
        """Increment stable consecutive-error state without changing lifecycle."""

        if not isinstance(error, HandError):
            raise ValidationError("error must be HandError")
        with self._lock:
            self._record_error_locked(error)

    def health(
        self,
        *,
        now: float,
        captured_at: float,
        capabilities: DeviceCapabilities,
        commands: CommandIngress,
        polling: PollScheduler,
        snapshot_store: SnapshotStore,
        transport: TransportHealth | None,
        arbiter: object | None,
    ) -> HealthStatus:
        """Build one immutable health view from cached component snapshots."""

        with self._lock:
            lifecycle = self._lifecycle_state
            connection = self._connection_state
            connected = self._connected
            generation = self._generation
            verified = self._identity_verified
            reconnects = self._reconnect_count
            last_send = self._last_send
            last_receive = self._last_receive
            last_transaction = self._last_transaction
            consecutive_errors = self._consecutive_errors
            recent_error = self._recent_error
            if self._latency_cache is None:
                self._latency_cache = _latency_metrics(self._ordered_latencies)
            latency = self._latency_cache
        data_ages = snapshot_store.data_ages(
            captured_at=captured_at,
            monotonic_now=now,
        )
        channels = polling.metrics(now=now, data_ages=data_ages)
        return HealthStatus(
            lifecycle_state=lifecycle,
            connection_state=connection,
            connected=connected,
            polling_active=polling.active,
            connection_generation=generation,
            identity_verified=verified,
            reconnect_count=reconnects,
            last_successful_send_at=last_send,
            last_successful_receive_at=last_receive,
            last_successful_transaction_at=last_transaction,
            consecutive_errors=consecutive_errors,
            recent_error=recent_error,
            command_families=commands.metrics(),
            discrete_queue_depth=commands.discrete_depth,
            channels=channels,
            transaction_latency=latency,
            transport=_transport_metrics(transport, arbiter),
            force_fields=polling.force_metrics(now=now),
        )

    def _set_connection(
        self,
        lifecycle: LifecycleState,
        connection: ConnectionState,
        *,
        connected: bool,
        verified: bool,
    ) -> None:
        with self._lock:
            self._lifecycle_state = lifecycle
            self._connection_state = connection
            self._connected = connected
            self._identity_verified = verified

    def _record_error_locked(self, error: HandError) -> None:
        self._consecutive_errors += 1
        self._recent_error = ErrorInfo.from_exception(error)


def _latency_metrics(values: list[float]) -> LatencyMetrics:
    """Project the already ordered latency window while its owner is locked."""
    if not values:
        return LatencyMetrics()
    return LatencyMetrics(
        count=len(values),
        p50_s=_percentile(values, 0.50),
        p95_s=_percentile(values, 0.95),
        p99_s=_percentile(values, 0.99),
    )


def _percentile(values: list[float], fraction: float) -> float:
    index = max(0, min(len(values) - 1, round((len(values) - 1) * fraction)))
    return values[index]


def _transport_metrics(
    health: TransportHealth | None,
    arbiter: object | None,
) -> TransportMetrics:
    if health is None:
        return TransportMetrics()
    pending_arbiter = 0
    if arbiter is not None:
        pending_arbiter = int(getattr(arbiter, "pending_total", 0)) + int(
            bool(getattr(arbiter, "in_flight", False))
        )
    return TransportMetrics(
        tx_frames=health.tx_frames,
        rx_frames=health.rx_frames,
        tx_bytes=health.tx_bytes,
        rx_bytes=health.rx_bytes,
        dropped_frames=health.rx_overflows + health.dropped_rx_on_close,
        route_misses=health.route_misses,
        pending_tx=health.pending_tx + pending_arbiter,
        pending_rx=health.pending_rx,
    )


__all__ = ["RuntimeMetrics"]
