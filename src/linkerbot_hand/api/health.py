"""Immutable health, scheduler, channel, and transport metrics."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .commands import CommandFamily
from .enums import AngleFeedbackPolicy, DataOrigin
from .errors import ErrorInfo


class LifecycleState(str, Enum):
    """Explicit lifecycle state of one HandDevice."""

    CLOSED = "closed"
    OPENING = "opening"
    OPEN = "open"
    CLOSING = "closing"
    RECOVERING = "recovering"
    FAULTED = "faulted"


class ConnectionState(str, Enum):
    """Cached physical connection state."""

    CLOSED = "closed"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    FAULTED = "faulted"


@dataclass(frozen=True, slots=True)
class DataAge:
    """Validity and age of one canonical state channel."""

    channel: str
    age_s: float | None
    valid: bool
    stale: bool
    sequence: int
    connection_generation: int
    origin: DataOrigin


@dataclass(frozen=True, slots=True)
class CommandFamilyMetrics:
    """Counters and current depth for one independent command family."""

    family: CommandFamily
    pending: int = 0
    accepted: int = 0
    dispatched: int = 0
    acknowledged: int = 0
    failed: int = 0
    superseded: int = 0


@dataclass(frozen=True, slots=True)
class ChannelMetrics:
    """Polling-plan progress and cached age for one state channel."""

    channel: str
    requested_hz: float
    achieved_hz: float
    missed_deadlines: int
    timeouts: int
    completed_reads: int
    command_feedback_samples: int
    feedback_samples: int
    feedback_policy: AngleFeedbackPolicy | None
    data_age: DataAge


@dataclass(frozen=True, slots=True)
class LatencyMetrics:
    """Completed transaction latency distribution in seconds."""

    count: int = 0
    p50_s: float | None = None
    p95_s: float | None = None
    p99_s: float | None = None


@dataclass(frozen=True, slots=True)
class TransportMetrics:
    """Cached counters for the complete shared physical transport channel."""

    tx_frames: int = 0
    rx_frames: int = 0
    tx_bytes: int = 0
    rx_bytes: int = 0
    dropped_frames: int = 0
    route_misses: int = 0
    bus_errors: int = 0
    pending_tx: int = 0
    pending_rx: int = 0
    estimated_utilization: float | None = None


@dataclass(frozen=True, slots=True)
class HealthStatus:
    """Complete zero-I/O health view for one HandDevice generation."""

    lifecycle_state: LifecycleState
    connection_state: ConnectionState
    connected: bool
    polling_active: bool
    connection_generation: int
    identity_verified: bool
    reconnect_count: int
    last_successful_send_at: float | None
    last_successful_receive_at: float | None
    last_successful_transaction_at: float | None
    consecutive_errors: int
    recent_error: ErrorInfo | None
    command_families: tuple[CommandFamilyMetrics, ...]
    discrete_queue_depth: int
    channels: tuple[ChannelMetrics, ...]
    transaction_latency: LatencyMetrics
    transport: TransportMetrics

    @classmethod
    def initial(cls) -> HealthStatus:
        """Return the explicit closed state used before the first ``open()``."""

        return cls(
            lifecycle_state=LifecycleState.CLOSED,
            connection_state=ConnectionState.CLOSED,
            connected=False,
            polling_active=False,
            connection_generation=0,
            identity_verified=False,
            reconnect_count=0,
            last_successful_send_at=None,
            last_successful_receive_at=None,
            last_successful_transaction_at=None,
            consecutive_errors=0,
            recent_error=None,
            command_families=(),
            discrete_queue_depth=0,
            channels=(),
            transaction_latency=LatencyMetrics(),
            transport=TransportMetrics(),
        )

    @property
    def pending_commands(self) -> int:
        """Return pending continuous and discrete command count."""

        return sum(metrics.pending for metrics in self.command_families)

    @property
    def superseded_commands(self) -> int:
        """Return total pre-dispatch command replacements."""

        return sum(metrics.superseded for metrics in self.command_families)


__all__ = [
    "ChannelMetrics",
    "CommandFamilyMetrics",
    "ConnectionState",
    "DataAge",
    "HealthStatus",
    "LatencyMetrics",
    "LifecycleState",
    "TransportMetrics",
]
