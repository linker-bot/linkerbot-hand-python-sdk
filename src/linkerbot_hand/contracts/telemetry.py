"""Cached sensor, snapshot, and health contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from ..errors import ValidationError
from .enums import ConnectionState, SensorType
from .errors import ErrorInfo


@dataclass(frozen=True, slots=True)
class TactileState:
    """One tactile matrix returned by a finger sensor.

    Attributes:
        finger: Stable finger or sensor name.
        rows: Number of matrix rows.
        columns: Number of matrix columns.
        values: Row-major sensor readings.
        online: Reported online state, or ``None`` when unavailable.
    """

    finger: str
    rows: int
    columns: int
    values: tuple[tuple[int, ...], ...]
    online: bool | None = None

    def __post_init__(self) -> None:
        """Validate that the matrix matches its declared dimensions.

        Raises:
            ValidationError: If dimensions are not positive or values do not
                have the declared shape.
        """
        if self.rows <= 0 or self.columns <= 0:
            raise ValidationError("tactile dimensions must be positive")
        if len(self.values) != self.rows or any(
            len(row) != self.columns for row in self.values
        ):
            raise ValidationError("tactile values do not match declared dimensions")


SensorValue: TypeAlias = int | float | bool | str | bytes | None | TactileState


@dataclass(frozen=True, slots=True)
class SensorSample:
    """Cached value and metadata for one sensor channel.

    Attributes:
        sensor: Sensor channel represented by this sample.
        values: Normalized SDK values without protocol framing.
        raw_values: Exact protocol values when the backend provides them.
        collected_at: Wall-clock collection timestamp, or ``None`` if no valid
            value has been collected in the current connection generation.
        sequence: Per-sensor sequence number, incremented on cache publication.
        valid: Whether the cache contains a usable current-generation reading.
        stale: Whether the reading is older than the capability threshold.
        connection_generation: Connection generation that produced the reading.
    """

    sensor: SensorType
    values: tuple[SensorValue, ...]
    raw_values: tuple[int, ...] | None
    collected_at: float | None
    sequence: int
    valid: bool
    stale: bool
    connection_generation: int


@dataclass(frozen=True, slots=True)
class JointState:
    """Convenience view that combines cached per-joint telemetry.

    Attributes:
        name: Stable joint name in capability order.
        angle_percent: Normalized joint position, or ``None`` when unavailable.
        raw_angle: Exact protocol angle value, or ``None`` when unavailable.
        speed: Current joint speed in the model capability unit.
        torque: Current joint torque in the model capability unit.
        fault: Current joint fault code.
    """

    name: str
    angle_percent: float | None = None
    raw_angle: int | None = None
    speed: float | int | None = None
    torque: float | int | None = None
    fault: int | None = None


@dataclass(frozen=True, slots=True)
class HandSnapshot:
    """Immutable, I/O-free view of all cached hand state.

    Attributes:
        captured_at: Wall-clock time at which the cache view was assembled.
        snapshot_sequence: Monotonic sequence for snapshots from this cache.
        connection_generation: Active connection generation at capture time.
        joints: Joint states in capability order.
        samples: Sensor samples in capability order.
    """

    captured_at: float
    snapshot_sequence: int
    connection_generation: int
    joints: tuple[JointState, ...]
    samples: tuple[SensorSample, ...]

    def sample(self, sensor: SensorType) -> SensorSample | None:
        """Look up a cached sample by sensor type.

        Args:
            sensor: Sensor channel to find.

        Returns:
            The cached sample, or ``None`` if the device has no such channel.
        """
        return next(
            (sample for sample in self.samples if sample.sensor is sensor), None
        )

    @property
    def angles(self) -> SensorSample | None:
        """Return the cached joint-angle sample, if supported."""
        return self.sample(SensorType.ANGLES)

    @property
    def force(self) -> SensorSample | None:
        """Return the cached force-sensor sample, if supported."""
        return self.sample(SensorType.FORCE)

    @property
    def temperature(self) -> SensorSample | None:
        """Return the cached temperature sample, if supported."""
        return self.sample(SensorType.TEMPERATURE)

    @property
    def current(self) -> SensorSample | None:
        """Return the cached joint-current sample, if supported."""
        return self.sample(SensorType.CURRENT)

    @property
    def faults(self) -> SensorSample | None:
        """Return the cached fault-code sample, if supported."""
        return self.sample(SensorType.FAULTS)


@dataclass(frozen=True, slots=True)
class DataAge:
    """Age of the latest valid sample for one sensor.

    Attributes:
        sensor: Sensor channel whose age was measured.
        age_s: Elapsed monotonic seconds, or ``None`` before the first sample.
    """

    sensor: SensorType
    age_s: float | None


@dataclass(frozen=True, slots=True)
class HealthStatus:
    """Immutable connection, command, and telemetry health summary.

    Attributes:
        connection_state: Detailed lifecycle connection state.
        connected: Whether the session is open and currently connected.
        last_successful_send_at: Wall-clock time of the latest successful send.
        last_successful_receive_at: Wall-clock time of the latest valid receive.
        consecutive_errors: Errors since the last successful I/O operation.
        recent_error: Most recently recorded error, if any.
        reconnect_count: Number of completed reconnections.
        connection_generation: Generation assigned to the active connection.
        superseded_commands: Total continuous targets replaced before dispatch.
        pending_commands: Commands accepted but not yet completed.
        data_ages: Latest sample age for every supported sensor.
    """

    connection_state: ConnectionState
    connected: bool
    last_successful_send_at: float | None
    last_successful_receive_at: float | None
    consecutive_errors: int
    recent_error: ErrorInfo | None
    reconnect_count: int
    connection_generation: int
    superseded_commands: int
    pending_commands: int
    data_ages: tuple[DataAge, ...]


__all__ = [
    "DataAge",
    "HandSnapshot",
    "HealthStatus",
    "JointState",
    "SensorSample",
    "SensorValue",
    "TactileState",
]
