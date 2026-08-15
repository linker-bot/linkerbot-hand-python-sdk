"""Single owner for all mutable hand session runtime state."""

from __future__ import annotations

import math
import threading
from enum import Enum

from ..backend import (
    BackendFactory,
    BackendReading,
    Clock,
    HandBackend,
    SystemClock,
)
from ..commands.model import CommandHandle
from ..commands.scheduler import CommandScheduler
from ..contracts import (
    ConnectionState,
    DeviceCapabilities,
    DeviceIdentity,
    ErrorInfo,
    PollingPlan,
    SensorType,
    SessionConfig,
)
from ..errors import (
    ClosedError,
    DeviceNotReadyError,
    HandSDKError,
    TransportError,
    ValidationError,
)
from .snapshot_cache import SnapshotCache


class _Lifecycle(Enum):
    CLOSED = "closed"
    OPENING = "opening"
    OPEN = "open"
    CLOSING = "closing"


class _SessionState:
    """Own the one condition lock, I/O lock, worker, and runtime state graph.

    Subclasses must hold ``_condition`` while reading or mutating lifecycle,
    connection, polling, recovery, inflight-command, and snapshot-cache state.
    ``_io_lock`` serializes backend calls so one session never writes or queries
    the transport concurrently.
    """

    def __init__(
        self,
        capabilities: DeviceCapabilities,
        backend_factory: BackendFactory,
        *,
        config: SessionConfig | None = None,
        clock: Clock | None = None,
    ) -> None:
        """Initialize a closed session without constructing a backend.

        Args:
            capabilities: Static capabilities for the configured model and side.
            backend_factory: Callable that creates a fresh backend generation.
            config: Optional lifecycle and scheduling configuration.
            clock: Optional injected scheduler clock.

        Raises:
            ValidationError: If capabilities, factory, or configuration is invalid.
        """
        if not isinstance(capabilities, DeviceCapabilities):
            raise ValidationError("capabilities must be DeviceCapabilities")
        if not callable(backend_factory):
            raise ValidationError("backend_factory must be callable")
        if config is not None and not isinstance(config, SessionConfig):
            raise ValidationError("config must be SessionConfig")
        self._base_capabilities = capabilities
        self._capabilities = capabilities
        self._backend_factory = backend_factory
        self._config = config or SessionConfig()
        self._clock = clock or SystemClock()

        self._scheduler = CommandScheduler(
            clock=self._clock,
            discrete_queue_size=self._config.discrete_queue_size,
        )

        self._condition = threading.Condition(threading.RLock())
        self._io_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._lifecycle = _Lifecycle.CLOSED
        self._connection_state = ConnectionState.CLOSED
        self._open_in_progress = False
        self._backend: HandBackend | None = None
        self._candidate_backend: HandBackend | None = None
        self._worker: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()

        self._connection_generation = 0
        self._last_identity: DeviceIdentity | None = None
        self._last_successful_send_at: float | None = None
        self._last_successful_receive_at: float | None = None
        self._consecutive_errors = 0
        self._recent_error: HandSDKError | None = None
        self._reconnect_count = 0

        self._polling = False
        self._polling_plan = PollingPlan()
        self._poll_deadlines: dict[SensorType, float] = {}
        self._poll_epoch = 0
        self._poll_inflight_epoch: int | None = None
        self._prefer_poll = True

        self._next_command_at = 0.0
        self._inflight_handle: CommandHandle | None = None
        self._snapshot_cache = SnapshotCache(capabilities)

        self._next_reconnect_at: float | None = None
        self._reconnect_delay = self._config.reconnect_initial_delay_s
        self._next_recovery_token = 1
        self._preparing_recovery_token: int | None = None
        self._pending_recovery_token: int | None = None
        self._active_recovery_token: int | None = None
        self._recovery_results: dict[int, HandSDKError | None] = {}

    def _mark_disconnected_locked(self, error: HandSDKError) -> None:
        if self._lifecycle is not _Lifecycle.OPEN:
            return
        self._connection_state = ConnectionState.ERROR
        self._scheduler.clear(
            DeviceNotReadyError(
                "queued command discarded after connection failure", cause=error
            )
        )
        self._snapshot_cache.invalidate()
        if self._config.auto_reconnect:
            self._schedule_reconnect_locked()
        self._wake_event.set()

    def _schedule_reconnect_locked(self) -> None:
        self._next_reconnect_at = self._clock.monotonic() + self._reconnect_delay
        self._reconnect_delay = min(
            self._reconnect_delay * 2.0,
            self._config.reconnect_max_delay_s,
        )

    def _finish_recovery_locked(self, token: int, error: HandSDKError | None) -> None:
        self._active_recovery_token = None
        self._recovery_results[token] = error
        if len(self._recovery_results) > 64:
            oldest = min(self._recovery_results)
            if oldest != token:
                self._recovery_results.pop(oldest, None)
        self._condition.notify_all()
        self._wake_event.set()

    def _finish_inflight_locked(self, handle: CommandHandle) -> None:
        self._scheduler.finish(handle)
        if self._inflight_handle is handle:
            self._inflight_handle = None
        self._condition.notify_all()

    def _finish_poll_locked(self, poll_epoch: int) -> None:
        if self._poll_inflight_epoch == poll_epoch:
            self._poll_inflight_epoch = None
        self._condition.notify_all()

    def _advance_poll_deadline_locked(
        self, sensor: SensorType, deadline: float, now: float
    ) -> None:
        rate = self._polling_plan.enabled_rates()[sensor]
        period = 1.0 / rate
        periods = max(1, math.floor((now - deadline) / period) + 1)
        self._poll_deadlines[sensor] = deadline + periods * period

    def _verify_identity(self, identity: DeviceIdentity, *, recovery: bool) -> None:
        if not isinstance(identity, DeviceIdentity):
            raise ValidationError("backend.open() returned an invalid identity")
        if identity.model != self._base_capabilities.model:
            raise DeviceNotReadyError(
                f"expected model {self._base_capabilities.model}, got {identity.model}"
            )
        expected_firmware = (
            self._last_identity.firmware
            if recovery and self._last_identity is not None
            else self._base_capabilities.firmware
        )
        if expected_firmware is not None and identity.firmware != expected_firmware:
            raise DeviceNotReadyError(
                f"expected firmware {expected_firmware}, got {identity.firmware}"
            )
        expected_side = (
            self._last_identity.side
            if recovery and self._last_identity is not None
            else self._base_capabilities.side
        )
        if (
            expected_side.value != "unknown"
            and identity.side.value != "unknown"
            and identity.side is not expected_side
        ):
            raise DeviceNotReadyError(
                f"expected {expected_side.value} hand, got {identity.side.value}"
            )

    @staticmethod
    def _validate_reading(reading: BackendReading, sensor: SensorType) -> None:
        if not isinstance(reading, BackendReading):
            raise TransportError("backend.poll() returned an invalid reading")
        if reading.sensor is not sensor:
            raise TransportError(
                f"backend returned {reading.sensor.value} for a {sensor.value} poll"
            )

    def _ensure_connected_locked(self) -> None:
        if self._lifecycle is not _Lifecycle.OPEN:
            raise ClosedError("session is closed")
        if self._connection_state is not ConnectionState.CONNECTED:
            raise DeviceNotReadyError(f"device is {self._connection_state.value}")

    def _record_error_locked(self, error: HandSDKError) -> None:
        self._recent_error = error
        self._consecutive_errors += 1

    def _allocate_recovery_token_locked(self) -> int:
        token = self._next_recovery_token
        self._next_recovery_token += 1
        return token

    @staticmethod
    def _error_info(error: HandSDKError) -> ErrorInfo:
        return ErrorInfo.from_exception(error)


__all__ = ["_Lifecycle", "_SessionState"]
