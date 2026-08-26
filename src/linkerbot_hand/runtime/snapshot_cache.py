"""I/O-free cache for sensor samples, snapshots, and data ages."""

from __future__ import annotations

from dataclasses import replace

from ..backend import BackendReading
from ..contracts import (
    DataAge,
    DeviceCapabilities,
    HandSnapshot,
    JointState,
    SensorSample,
    SensorType,
    SensorValueKind,
)

_JOINT_VECTOR_SENSORS = frozenset(
    {
        SensorType.ANGLES,
        SensorType.SPEEDS,
        SensorType.TORQUES,
        SensorType.TEMPERATURE,
        SensorType.CURRENT,
        SensorType.FAULTS,
        SensorType.VOLTAGE,
        SensorType.ACCELERATION,
        SensorType.MOTION_TIME,
    }
)


class SnapshotCache:
    """Mutable, I/O-free cache protected by the session condition lock.

    The cache does not acquire its own lock. Every method must be called while
    the owning session holds its condition lock.
    """

    def __init__(self, capabilities: DeviceCapabilities) -> None:
        """Initialize invalid samples for all supported sensor channels.

        Args:
            capabilities: Initial model capability description.
        """
        self._capabilities = capabilities
        self._snapshot_sequence = 0
        self._sensor_sequences = {sensor: 0 for sensor in SensorType}
        self._samples: dict[SensorType, SensorSample] = {}
        self._sample_monotonic: dict[SensorType, float] = {}
        self.reset(capabilities, generation=0)

    def reset(self, capabilities: DeviceCapabilities, *, generation: int) -> None:
        """Invalidate cached data for a new connection generation.

        Per-sensor sequence counters are retained so observers can distinguish
        later publications across reconnects.

        Args:
            capabilities: Capabilities verified for the new connection.
            generation: New connection generation identifier.
        """
        self._capabilities = capabilities
        self._sample_monotonic.clear()
        self._samples = {
            sensor: SensorSample(
                sensor=sensor,
                values=(),
                unit=self._sensor_unit(sensor),
                encoding=self._sensor_encoding(sensor),
                physical_unit=self._sensor_physical_unit(sensor),
                scale=self._sensor_scale(sensor),
                offset=self._sensor_offset(sensor),
                value_kind=self._sensor_value_kind(sensor),
                matrix_shape=self._sensor_matrix_shape(sensor),
                collected_at=None,
                sequence=self._sensor_sequences[sensor],
                valid=False,
                stale=True,
                connection_generation=generation,
            )
            for sensor in self.sensor_types
        }
        self._snapshot_sequence += 1

    @property
    def sensor_types(self) -> tuple[SensorType, ...]:
        """Return supported sensor channels in capability order."""
        return tuple(item.sensor for item in self._capabilities.sensors)

    def publish(
        self,
        reading: BackendReading,
        *,
        generation: int,
        collected_monotonic: float,
        collected_wall: float,
    ) -> bool:
        """Publish one backend reading and advance cache sequences.

        Args:
            reading: Backend reading to cache.
            generation: Connection generation that produced the reading.
            collected_monotonic: Monotonic collection time used for data age.
            collected_wall: Wall-clock collection time exposed to callers.

        Returns:
            Whether the reading has a valid shape for its sensor channel.
        """
        sensor = reading.sensor
        valid = self.reading_is_valid(reading)
        self._sensor_sequences[sensor] += 1
        self._samples[sensor] = SensorSample(
            sensor=sensor,
            values=tuple(reading.values),
            unit=self._sensor_unit(sensor),
            encoding=self._sensor_encoding(sensor),
            physical_unit=self._sensor_physical_unit(sensor),
            scale=self._sensor_scale(sensor),
            offset=self._sensor_offset(sensor),
            value_kind=self._sensor_value_kind(sensor),
            matrix_shape=self._sensor_matrix_shape(sensor),
            collected_at=collected_wall,
            sequence=self._sensor_sequences[sensor],
            valid=valid,
            stale=not valid,
            connection_generation=generation,
        )
        if valid:
            self._sample_monotonic[sensor] = collected_monotonic
        else:
            self._sample_monotonic.pop(sensor, None)
        self._snapshot_sequence += 1
        return valid

    def invalidate(self) -> None:
        """Mark all cached samples invalid and stale without discarding values."""
        self._samples = {
            sensor: replace(sample, valid=False, stale=True)
            for sensor, sample in self._samples.items()
        }
        self._snapshot_sequence += 1

    def snapshot(
        self,
        *,
        captured_at: float,
        captured_monotonic: float,
        generation: int,
    ) -> HandSnapshot:
        """Assemble an immutable snapshot without performing device I/O.

        Args:
            captured_at: Wall-clock snapshot timestamp.
            captured_monotonic: Monotonic time used to evaluate staleness.
            generation: Active connection generation.

        Returns:
            Snapshot containing refreshed validity and staleness metadata.
        """
        samples = tuple(
            self._sample_for_read(sensor, captured_monotonic, generation)
            for sensor in self.sensor_types
        )
        return HandSnapshot(
            captured_at=captured_at,
            snapshot_sequence=self._snapshot_sequence,
            connection_generation=generation,
            joints=self._build_joint_states(samples),
            samples=samples,
        )

    def data_ages(self, now_monotonic: float) -> tuple[DataAge, ...]:
        """Calculate ages for the latest valid sensor publications.

        Args:
            now_monotonic: Current monotonic timestamp.

        Returns:
            One data-age entry per supported sensor channel.
        """
        return tuple(
            DataAge(
                sensor=sensor,
                age_s=(
                    None
                    if sensor not in self._sample_monotonic
                    else max(0.0, now_monotonic - self._sample_monotonic[sensor])
                ),
            )
            for sensor in self.sensor_types
        )

    def reading_is_valid(self, reading: BackendReading) -> bool:
        """Check whether a backend reading has the expected non-empty shape.

        Args:
            reading: Reading to validate against current capabilities.

        Returns:
            Whether the reading can be exposed as valid current telemetry.
        """
        if not reading.values:
            return False
        return not (
            reading.sensor in _JOINT_VECTOR_SENSORS
            and len(reading.values) != self._capabilities.joint_count
        )

    def _sample_for_read(
        self,
        sensor: SensorType,
        now_monotonic: float,
        generation: int,
    ) -> SensorSample:
        sample = self._samples.get(sensor)
        if sample is None:
            sample = SensorSample(
                sensor=sensor,
                values=(),
                unit=self._sensor_unit(sensor),
                encoding=self._sensor_encoding(sensor),
                physical_unit=self._sensor_physical_unit(sensor),
                scale=self._sensor_scale(sensor),
                offset=self._sensor_offset(sensor),
                value_kind=self._sensor_value_kind(sensor),
                matrix_shape=self._sensor_matrix_shape(sensor),
                collected_at=None,
                sequence=self._sensor_sequences[sensor],
                valid=False,
                stale=True,
                connection_generation=generation,
            )
        capability = self._capabilities.sensor(sensor)
        sampled_at = self._sample_monotonic.get(sensor)
        stale = (
            not sample.valid
            or sample.connection_generation != generation
            or sampled_at is None
            or capability is None
            or now_monotonic - sampled_at > capability.stale_after_s
        )
        valid = sample.valid and sample.connection_generation == generation
        return replace(sample, valid=valid, stale=stale)

    def _build_joint_states(
        self, samples: tuple[SensorSample, ...]
    ) -> tuple[JointState, ...]:
        by_sensor = {sample.sensor: sample for sample in samples}
        angles = by_sensor.get(SensorType.ANGLES)
        speeds = by_sensor.get(SensorType.SPEEDS)
        torques = by_sensor.get(SensorType.TORQUES)
        faults = by_sensor.get(SensorType.FAULTS)
        return tuple(
            JointState(
                name=capability.name,
                angle=self._sample_value(angles, index),
                speed=self._sample_value(speeds, index),
                torque=self._sample_value(torques, index),
                fault=self._fault_sample_value(faults, index),
            )
            for index, capability in enumerate(self._capabilities.joints)
        )

    @staticmethod
    def _sample_value(sample: SensorSample | None, index: int) -> float | int | None:
        if sample is None or not sample.valid or index >= len(sample.values):
            return None
        value = sample.values[index]
        return (
            value
            if isinstance(value, (float, int)) and not isinstance(value, bool)
            else None
        )

    @classmethod
    def _fault_sample_value(cls, sample: SensorSample | None, index: int) -> int | None:
        value = cls._sample_value(sample, index)
        return None if value is None else int(value)

    def _sensor_unit(self, sensor: SensorType) -> str | None:
        capability = self._capabilities.sensor(sensor)
        return None if capability is None else capability.unit

    def _sensor_encoding(self, sensor: SensorType) -> str | None:
        capability = self._capabilities.sensor(sensor)
        return None if capability is None else capability.encoding

    def _sensor_physical_unit(self, sensor: SensorType) -> str | None:
        capability = self._capabilities.sensor(sensor)
        return None if capability is None else capability.physical_unit

    def _sensor_scale(self, sensor: SensorType) -> float | None:
        capability = self._capabilities.sensor(sensor)
        return None if capability is None else capability.scale

    def _sensor_offset(self, sensor: SensorType) -> float | None:
        capability = self._capabilities.sensor(sensor)
        return None if capability is None else capability.offset

    def _sensor_value_kind(self, sensor: SensorType) -> SensorValueKind:
        capability = self._capabilities.sensor(sensor)
        if capability is None:  # pragma: no cover - only supported sensors are cached
            raise RuntimeError(f"missing capability for sensor {sensor.value}")
        return capability.value_kind

    def _sensor_matrix_shape(self, sensor: SensorType) -> tuple[int, int] | None:
        capability = self._capabilities.sensor(sensor)
        return None if capability is None else capability.matrix_shape


__all__ = ["SnapshotCache"]
