"""Public lifecycle-safe hand session façade."""

from __future__ import annotations

from collections.abc import Iterable

from .commands.model import CommandHandle, HandCommand
from .commands.validation import coerce_values, validate_command
from .contracts import (
    AckSupport,
    CommandKind,
    CommandPolicy,
    ConnectionState,
    DeviceCapabilities,
    HandSnapshot,
    HealthStatus,
)
from .runtime.angle_conversion import (
    percent_to_raw,
    physical_angles_to_raw,
    raw_to_percent,
    raw_to_physical_angles,
)
from .runtime.lifecycle import _LifecycleRuntime
from .runtime.state import _Lifecycle


class HandSession(_LifecycleRuntime):
    """One connection to a hand with cached state and non-blocking commands.

    Construction allocates only in-memory synchronization primitives. The
    backend factory is not invoked until :meth:`open`.

    Args:
        capabilities: Static capability description used before identity read.
        backend_factory: No-I/O callable that creates a fresh backend generation.
        config: Optional scheduler and connection lifecycle configuration.
        clock: Optional injected clock for deterministic scheduling tests.
    """

    def open(self) -> HandSession:
        """Open the device idempotently and return this session.

        Returns:
            This open session.

        Raises:
            HandSDKError: If backend creation, connection, or identity
                verification fails.
        """
        super().open()
        return self

    def submit_command(
        self,
        command: HandCommand,
        *,
        policy: CommandPolicy | None = None,
    ) -> CommandHandle:
        """Validate and enqueue a command without touching the backend.

        Args:
            command: Continuous target or discrete operation to submit.
            policy: Required scheduling policy, or ``None`` for the command
                class default.

        Returns:
            Non-blocking handle initially in :attr:`CommandStage.ACCEPTED`.

        Raises:
            ClosedError: If the session is not open and connected.
            ValidationError: If values or scheduling policy are invalid.
            UnsupportedOperationError: If capabilities lack the operation.
            QueueFullError: If the bounded discrete command queue is full.
        """
        normalized = validate_command(command, self._capabilities)
        with self._condition:
            self._ensure_connected_locked()
            ack_support = (
                AckSupport.SUPPORTED
                if self._capabilities.features.supports_ack(normalized.kind)
                else AckSupport.UNSUPPORTED
            )
            handle = self._scheduler.submit(
                normalized,
                ack_support=ack_support,
                policy=policy,
            )
            self._wake_event.set()
            return handle

    def submit_angles(
        self,
        values: Iterable[float | int],
        *,
        policy: CommandPolicy = CommandPolicy.LATEST,
    ) -> CommandHandle:
        """Submit normalized joint positions using latest-wins scheduling.

        Flexion axes use 0=flexed and 100=extended. Other axes use the
        reversible endpoints declared in their :class:`PercentageMapping`.

        Args:
            values: One normalized value in [0, 100] per joint.
            policy: Scheduling policy; continuous commands require ``LATEST``.

        Returns:
            Non-blocking command handle.

        Raises:
            HandSDKError: If the session, values, policy, or capability is invalid.
        """
        return self.submit_command(
            HandCommand(CommandKind.ANGLES, coerce_values(values)),
            policy=policy,
        )

    def submit_raw_angles(
        self,
        values: Iterable[float | int],
        *,
        policy: CommandPolicy = CommandPolicy.LATEST,
    ) -> CommandHandle:
        """Submit exact protocol joint-angle values.

        Args:
            values: One raw protocol value per joint in capability order.
            policy: Scheduling policy; continuous commands require ``LATEST``.

        Returns:
            Non-blocking command handle.

        Raises:
            HandSDKError: If the session, values, policy, or capability is invalid.
        """
        return self.submit_command(
            HandCommand(CommandKind.RAW_ANGLES, coerce_values(values)),
            policy=policy,
        )

    def submit_speeds(
        self,
        values: Iterable[float | int],
        *,
        policy: CommandPolicy = CommandPolicy.LATEST,
    ) -> CommandHandle:
        """Submit joint speeds in capability-declared units.

        Args:
            values: One speed value per joint in capability order.
            policy: Scheduling policy; continuous commands require ``LATEST``.

        Returns:
            Non-blocking command handle.

        Raises:
            HandSDKError: If the session, values, policy, or capability is invalid.
        """
        return self.submit_command(
            HandCommand(CommandKind.SPEEDS, coerce_values(values)),
            policy=policy,
        )

    def submit_torques(
        self,
        values: Iterable[float | int],
        *,
        policy: CommandPolicy = CommandPolicy.LATEST,
    ) -> CommandHandle:
        """Submit joint torques in capability-declared units.

        Args:
            values: One torque value per joint in capability order.
            policy: Scheduling policy; continuous commands require ``LATEST``.

        Returns:
            Non-blocking command handle.

        Raises:
            HandSDKError: If the session, values, policy, or capability is invalid.
        """
        return self.submit_command(
            HandCommand(CommandKind.TORQUES, coerce_values(values)),
            policy=policy,
        )

    def clear_faults(self) -> CommandHandle:
        """Enqueue a bounded-FIFO fault-clear operation.

        Returns:
            Non-blocking command handle.

        Raises:
            HandSDKError: If unavailable, unsupported, or the FIFO is full.
        """
        return self.submit_command(HandCommand(CommandKind.CLEAR_FAULTS))

    def enable(self) -> CommandHandle:
        """Enqueue a bounded-FIFO device-enable operation."""
        return self.submit_command(HandCommand(CommandKind.ENABLE))

    def disable(self) -> CommandHandle:
        """Enqueue a bounded-FIFO device-disable operation."""
        return self.submit_command(HandCommand(CommandKind.DISABLE))

    def calibrate(self) -> CommandHandle:
        """Enqueue a bounded-FIFO device-calibration operation."""
        return self.submit_command(HandCommand(CommandKind.CALIBRATE))

    def get_capabilities(self) -> DeviceCapabilities:
        """Return current capabilities without device I/O.

        After :meth:`open`, detected firmware and side are reflected in the
        returned immutable object.
        """
        with self._condition:
            return self._capabilities

    def get_snapshot(self) -> HandSnapshot:
        """Return cached data only; this method never invokes the backend.

        Returns:
            Immutable snapshot with per-sensor validity, staleness, timestamps,
            sequence numbers, and connection generation.
        """
        with self._condition:
            now_monotonic = self._clock.monotonic()
            return self._snapshot_cache.snapshot(
                captured_at=self._clock.time(),
                captured_monotonic=now_monotonic,
                generation=self._connection_generation,
            )

    def get_health(self) -> HealthStatus:
        """Return a transport-free health view derived from cached counters."""
        with self._condition:
            now = self._clock.monotonic()
            pending = self._scheduler.pending_count
            if (
                self._inflight_handle is not None
                and not self._inflight_handle.done
                and not self._scheduler.is_unclaimed(self._inflight_handle)
            ):
                pending += 1
            return HealthStatus(
                connection_state=self._connection_state,
                connected=(
                    self._lifecycle is _Lifecycle.OPEN
                    and self._connection_state is ConnectionState.CONNECTED
                ),
                last_successful_send_at=self._last_successful_send_at,
                last_successful_receive_at=self._last_successful_receive_at,
                consecutive_errors=self._consecutive_errors,
                recent_error=(
                    None
                    if self._recent_error is None
                    else self._error_info(self._recent_error)
                ),
                reconnect_count=self._reconnect_count,
                connection_generation=self._connection_generation,
                superseded_commands=self._scheduler.superseded_commands,
                pending_commands=pending,
                data_ages=self._snapshot_cache.data_ages(now),
            )

    def percent_to_raw(self, values: Iterable[float | int]) -> tuple[int, ...]:
        """Convert normalized joint positions to exact protocol values.

        Args:
            values: One value in [0, 100] per joint. Flexion axes use 0=flexed
                and 100=extended; directional endpoints come from capabilities.

        Returns:
            Rounded protocol values in joint order.

        Raises:
            ValidationError: If count or values are invalid.
            UnsupportedOperationError: If a joint has no verified mapping.
        """
        return percent_to_raw(values, self._capabilities)

    def raw_to_percent(self, values: Iterable[float | int]) -> tuple[float, ...]:
        """Convert protocol values to normalized joint positions.

        Args:
            values: One exact protocol value per joint.

        Returns:
            Normalized positions in joint order; values are not clamped.

        Raises:
            ValidationError: If count or values are invalid.
            UnsupportedOperationError: If a joint has no verified mapping.
        """
        return raw_to_percent(values, self._capabilities)

    def raw_to_physical_angles(
        self, values: Iterable[float | int]
    ) -> tuple[float, ...]:
        """Convert protocol positions to calibrated physical angles.

        Args:
            values: One raw protocol value per joint.

        Returns:
            Physical angles in each joint's declared unit.

        Raises:
            ValidationError: If count or values are outside verified ranges.
            UnsupportedOperationError: If any joint lacks physical calibration.
        """
        return raw_to_physical_angles(values, self._capabilities)

    def physical_angles_to_raw(self, values: Iterable[float | int]) -> tuple[int, ...]:
        """Convert calibrated physical angles to protocol values.

        Args:
            values: One physical angle per joint in declared units.

        Returns:
            Rounded protocol values in joint order.

        Raises:
            ValidationError: If count or values are outside verified ranges.
            UnsupportedOperationError: If any joint lacks physical calibration.
        """
        return physical_angles_to_raw(values, self._capabilities)

    def __enter__(self) -> HandSession:
        """Open and return this session for context-manager use."""
        return self.open()

    def __exit__(self, *_: object) -> None:
        """Close the session when leaving a context manager."""
        self.close()


class HandDevice(HandSession):
    """Configured hand endpoint whose construction performs no device I/O."""

    def open(self) -> HandDevice:
        """Open the configured device idempotently and return it."""
        super().open()
        return self

    def __enter__(self) -> HandDevice:
        """Open and return this device for context-manager use."""
        return self.open()


__all__ = ["HandDevice", "HandSession"]
