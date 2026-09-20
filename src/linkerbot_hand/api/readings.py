"""Immutable motion and sensor samples returned by Hand SDK 1.0."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TypeAlias

from ._force_names import validate_force_field
from .enums import DataOrigin
from .errors import UnsupportedOperationError, ValidationError

RawNumber: TypeAlias = int | float
TactileValue: TypeAlias = RawNumber | tuple[RawNumber, RawNumber, RawNumber]


def _is_finite_number(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
    )


@dataclass(frozen=True, slots=True)
class SampleMeta:
    """Collection metadata owned independently by every state channel."""

    sequence: int
    collected_at: float | None
    age_s: float | None
    valid: bool
    stale: bool
    connection_generation: int
    origin: DataOrigin
    within_declared_range: bool

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or self.sequence < 0:
            raise ValidationError("sequence must be a non-negative int")
        if (
            type(self.connection_generation) is not int
            or self.connection_generation < 0
        ):
            raise ValidationError("connection_generation must be a non-negative int")
        if not isinstance(self.valid, bool):
            raise ValidationError("valid must be bool")
        if not isinstance(self.stale, bool):
            raise ValidationError("stale must be bool")
        if not isinstance(self.within_declared_range, bool):
            raise ValidationError("within_declared_range must be bool")
        if not isinstance(self.origin, DataOrigin):
            raise ValidationError("origin must be DataOrigin")
        if self.valid:
            if self.sequence <= 0:
                raise ValidationError("a valid sample must have positive sequence")
            if not _is_finite_number(self.collected_at):
                raise ValidationError("a valid sample must have a finite collected_at")
            age_s = self.age_s
            if age_s is None or not _is_finite_number(age_s) or age_s < 0:
                raise ValidationError(
                    "a valid sample must have a non-negative finite age_s"
                )
            if self.origin is DataOrigin.NONE:
                raise ValidationError("a valid sample must have a data origin")
        else:
            if self.collected_at is not None or self.age_s is not None:
                raise ValidationError(
                    "an invalid sample cannot claim collection metadata"
                )
            if self.origin is not DataOrigin.NONE:
                raise ValidationError("an invalid sample must use origin NONE")
            if not self.stale:
                raise ValidationError("an invalid sample must be stale")

    @classmethod
    def unavailable(cls, *, connection_generation: int = 0) -> SampleMeta:
        """Create metadata for a channel without a current-generation sample."""

        return cls(
            sequence=0,
            collected_at=None,
            age_s=None,
            valid=False,
            stale=True,
            connection_generation=connection_generation,
            origin=DataOrigin.NONE,
            within_declared_range=True,
        )


@dataclass(frozen=True, slots=True)
class AngleSample:
    """One angle transaction with raw, percent, and calibrated representations."""

    raw: tuple[RawNumber, ...]
    percent: tuple[float | None, ...]
    physical_rad: tuple[float | None, ...]
    meta: SampleMeta

    def __post_init__(self) -> None:
        if not isinstance(self.raw, tuple) or any(
            not _is_finite_number(value) for value in self.raw
        ):
            raise ValidationError("angle raw must be a tuple of finite numbers")
        if not isinstance(self.percent, tuple) or any(
            value is not None and not _is_finite_number(value) for value in self.percent
        ):
            raise ValidationError(
                "angle percent must be a tuple of finite numbers or None"
            )
        if not isinstance(self.physical_rad, tuple) or any(
            value is not None and not _is_finite_number(value)
            for value in self.physical_rad
        ):
            raise ValidationError(
                "physical_rad must be a tuple of finite numbers or None"
            )
        if not isinstance(self.meta, SampleMeta):
            raise ValidationError("meta must be SampleMeta")
        if len(self.raw) != len(self.percent) or len(self.raw) != len(
            self.physical_rad
        ):
            raise ValidationError("angle representations must have equal lengths")
        if self.meta.valid and not self.raw:
            raise ValidationError("a valid angle sample cannot be empty")

    @classmethod
    def unavailable(
        cls,
        channel_count: int,
        *,
        connection_generation: int = 0,
    ) -> AngleSample:
        """Create an invalid angle sample with the declared public shape."""

        if type(channel_count) is not int or channel_count <= 0:
            raise ValidationError("channel_count must be a positive int")
        return cls(
            raw=tuple(0 for _ in range(channel_count)),
            percent=tuple(None for _ in range(channel_count)),
            physical_rad=tuple(None for _ in range(channel_count)),
            meta=SampleMeta.unavailable(connection_generation=connection_generation),
        )


@dataclass(frozen=True, slots=True)
class _RawVectorSample:
    raw: tuple[RawNumber, ...]
    meta: SampleMeta

    def __post_init__(self) -> None:
        if not isinstance(self.raw, tuple) or any(
            not _is_finite_number(value) for value in self.raw
        ):
            raise ValidationError("raw must be a tuple of finite protocol values")
        if not isinstance(self.meta, SampleMeta):
            raise ValidationError("meta must be SampleMeta")
        if self.meta.valid and not self.raw:
            raise ValidationError("a valid sample cannot be empty")


@dataclass(frozen=True, slots=True)
class MeasuredSpeedSample(_RawVectorSample):
    """Device-measured speed protocol values."""


@dataclass(frozen=True, slots=True)
class ConfiguredSpeedSample(_RawVectorSample):
    """Configured speed protocol values, never implied to be measured."""


@dataclass(frozen=True, slots=True)
class ConfiguredAccelerationSample(_RawVectorSample):
    """Configured acceleration protocol values, never implied to be measured."""


@dataclass(frozen=True, slots=True)
class CurrentSample(_RawVectorSample):
    """Device-measured current protocol values."""


@dataclass(frozen=True, slots=True)
class CurrentLimitSample(_RawVectorSample):
    """Configured current-limit protocol values."""


@dataclass(frozen=True, slots=True)
class EffortLimitSample(_RawVectorSample):
    """Configured, physically unqualified effort-limit protocol values."""


@dataclass(frozen=True, slots=True)
class TorqueSample(_RawVectorSample):
    """Device fields confirmed to represent true torque protocol values."""


@dataclass(frozen=True, slots=True)
class TemperatureSample(_RawVectorSample):
    """Temperature protocol values returned without SDK conversion."""


@dataclass(frozen=True, slots=True)
class VoltageSample(_RawVectorSample):
    """Voltage protocol values returned without SDK conversion."""


@dataclass(frozen=True, slots=True)
class MotionTimeSample(_RawVectorSample):
    """Configured motion-time protocol values returned without conversion."""


@dataclass(frozen=True, slots=True)
class JointFaultStatus:
    """Decoded O30 joint-fault flags alongside the exact protocol byte."""

    raw: int
    hand_layer_stall: bool
    actuator_layer_stall: bool
    over_current: bool
    over_temperature: bool
    actuator_abnormal: bool
    actuator_offline: bool
    reserved_bits: int

    def __post_init__(self) -> None:
        if type(self.raw) is not int or not 0 <= self.raw <= 0xFF:
            raise ValidationError("joint fault raw must be an unsigned byte")
        for name in (
            "hand_layer_stall",
            "actuator_layer_stall",
            "over_current",
            "over_temperature",
            "actuator_abnormal",
            "actuator_offline",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ValidationError(f"{name} must be bool")
        if (
            type(self.reserved_bits) is not int
            or self.reserved_bits < 0
            or self.reserved_bits & ~0x30
        ):
            raise ValidationError("reserved_bits may contain only fault bits 4 and 5")
        decoded = (
            int(self.hand_layer_stall) * 0x01
            | int(self.actuator_layer_stall) * 0x02
            | int(self.over_current) * 0x04
            | int(self.over_temperature) * 0x08
            | self.reserved_bits
            | int(self.actuator_abnormal) * 0x40
            | int(self.actuator_offline) * 0x80
        )
        if decoded != self.raw:
            raise ValidationError("decoded joint fault flags do not match raw")


@dataclass(frozen=True, slots=True)
class FaultSample(_RawVectorSample):
    """Device fault bytes plus optional protocol-specific decoded statuses."""

    statuses: tuple[JointFaultStatus, ...] = ()

    def __post_init__(self) -> None:
        _RawVectorSample.__post_init__(self)
        if any(type(value) is not int for value in self.raw):
            raise ValidationError("fault raw values must be integers")
        if not isinstance(self.statuses, tuple) or any(
            not isinstance(value, JointFaultStatus) for value in self.statuses
        ):
            raise ValidationError("fault statuses must contain JointFaultStatus values")
        if self.statuses and len(self.statuses) != len(self.raw):
            raise ValidationError("fault statuses must match raw fault values")
        if self.statuses and any(
            status.raw != raw
            for status, raw in zip(self.statuses, self.raw, strict=True)
        ):
            raise ValidationError("fault statuses must preserve raw fault values")


@dataclass(frozen=True, slots=True)
class TactileFieldDescriptor:
    """One descriptor-defined field in an O30 tactile byte stream."""

    semantic: str
    data_type: str
    element_count: int
    byte_offset: int
    byte_length: int

    def __post_init__(self) -> None:
        for name in ("semantic", "data_type"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValidationError(f"tactile field {name} must be a non-empty str")
        if type(self.element_count) is not int or self.element_count <= 0:
            raise ValidationError("tactile field element_count must be a positive int")
        if type(self.byte_offset) is not int or self.byte_offset < 0:
            raise ValidationError("tactile field byte_offset must be non-negative")
        if type(self.byte_length) is not int or self.byte_length <= 0:
            raise ValidationError("tactile field byte_length must be positive")


@dataclass(frozen=True, slots=True)
class TactileFieldSample:
    """Decoded values for one descriptor field, without physical conversion."""

    semantic: str
    data_type: str
    element_count: int
    byte_offset: int
    byte_length: int
    values: tuple[TactileValue, ...]

    def __post_init__(self) -> None:
        descriptor = TactileFieldDescriptor(
            semantic=self.semantic,
            data_type=self.data_type,
            element_count=self.element_count,
            byte_offset=self.byte_offset,
            byte_length=self.byte_length,
        )
        del descriptor
        if not isinstance(self.values, tuple) or len(self.values) != self.element_count:
            raise ValidationError("tactile field values must match element_count")
        if self.semantic == "xyz":
            if any(
                not isinstance(value, tuple)
                or len(value) != 3
                or any(not _is_finite_number(component) for component in value)
                for value in self.values
            ):
                raise ValidationError(
                    "xyz tactile values must contain finite xyz triples"
                )
        elif any(not _is_finite_number(value) for value in self.values):
            raise ValidationError("tactile field values must contain finite numbers")


@dataclass(frozen=True, slots=True)
class TactileSensorMetadata:
    """Runtime metadata for one selected O30 fingertip sensor."""

    selected_sensor: int
    sensor_name: str
    sensor_type: str
    data_unit: str | None
    data_range: int
    maximum_rows: int
    maximum_columns: int
    requested_rows: int
    requested_columns: int
    valid_data_position_mode: bool
    rectangle_fill: bool
    descriptor_text: str | None
    field_descriptors: tuple[TactileFieldDescriptor, ...]
    total_data_length: int

    def __post_init__(self) -> None:
        if type(self.selected_sensor) is not int or not 1 <= self.selected_sensor <= 5:
            raise ValidationError("selected_sensor must be in 1..5")
        for name in ("sensor_name", "sensor_type"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValidationError(f"{name} must be a non-empty str")
        if self.data_unit is not None and (
            not isinstance(self.data_unit, str) or not self.data_unit
        ):
            raise ValidationError("data_unit must be a non-empty str or None")
        for name in (
            "data_range",
            "maximum_rows",
            "maximum_columns",
            "requested_rows",
            "requested_columns",
        ):
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value <= 0xFF:
                raise ValidationError(f"{name} must be an unsigned byte")
        for name in ("valid_data_position_mode", "rectangle_fill"):
            if not isinstance(getattr(self, name), bool):
                raise ValidationError(f"{name} must be bool")
        if self.descriptor_text is not None and (
            not isinstance(self.descriptor_text, str) or not self.descriptor_text
        ):
            raise ValidationError("descriptor_text must be a non-empty str or None")
        if not isinstance(self.field_descriptors, tuple) or any(
            not isinstance(value, TactileFieldDescriptor)
            for value in self.field_descriptors
        ):
            raise ValidationError(
                "field_descriptors must contain TactileFieldDescriptor values"
            )
        if type(self.total_data_length) is not int or not (
            0 <= self.total_data_length <= 0xFFFF
        ):
            raise ValidationError("total_data_length must be an unsigned 16-bit int")
        if bool(self.descriptor_text) != bool(self.field_descriptors):
            raise ValidationError(
                "descriptor_text and field_descriptors must both be present or absent"
            )
        described_length = sum(value.byte_length for value in self.field_descriptors)
        if self.field_descriptors and described_length != self.total_data_length:
            raise ValidationError(
                "tactile descriptor byte length must match total_data_length"
            )

    @property
    def descriptor_length(self) -> int:
        """Return the descriptor's exact ASCII byte length."""

        return (
            0
            if self.descriptor_text is None
            else len(self.descriptor_text.encode("ascii"))
        )

    @property
    def available(self) -> bool:
        """Return whether metadata describes a readable sensor data stream."""

        return (
            self.sensor_type != "NO_SENSOR"
            and self.total_data_length > 0
            and bool(self.field_descriptors)
        )


@dataclass(frozen=True, slots=True)
class TactileSensorSample:
    """One O30 sensor's exact bytes and descriptor-decoded values."""

    metadata: TactileSensorMetadata
    raw_data: bytes
    fields: tuple[TactileFieldSample, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.metadata, TactileSensorMetadata):
            raise ValidationError("metadata must be TactileSensorMetadata")
        if not isinstance(self.raw_data, bytes):
            raise ValidationError("raw_data must be bytes")
        if not isinstance(self.fields, tuple) or any(
            not isinstance(value, TactileFieldSample) for value in self.fields
        ):
            raise ValidationError("fields must contain TactileFieldSample values")
        if self.metadata.available:
            if len(self.raw_data) != self.metadata.total_data_length:
                raise ValidationError("raw_data length must match sensor metadata")
            descriptors = tuple(
                (
                    field.semantic,
                    field.data_type,
                    field.element_count,
                    field.byte_offset,
                    field.byte_length,
                )
                for field in self.fields
            )
            expected = tuple(
                (
                    field.semantic,
                    field.data_type,
                    field.element_count,
                    field.byte_offset,
                    field.byte_length,
                )
                for field in self.metadata.field_descriptors
            )
            if descriptors != expected:
                raise ValidationError("decoded tactile fields must match metadata")
        elif self.raw_data or self.fields:
            raise ValidationError("an unavailable tactile sensor cannot contain data")


@dataclass(frozen=True, slots=True)
class TactileMatrix:
    """One named row-major tactile matrix containing exact device values."""

    name: str
    rows: int
    columns: int
    raw: tuple[tuple[RawNumber, ...], ...]
    online: bool | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValidationError("tactile name must be a non-empty str")
        if type(self.rows) is not int or self.rows <= 0:
            raise ValidationError("tactile rows must be a positive int")
        if type(self.columns) is not int or self.columns <= 0:
            raise ValidationError("tactile columns must be a positive int")
        if not isinstance(self.raw, tuple) or len(self.raw) != self.rows:
            raise ValidationError("tactile raw rows do not match the shape")
        if any(
            not isinstance(row, tuple)
            or len(row) != self.columns
            or any(not _is_finite_number(value) for value in row)
            for row in self.raw
        ):
            raise ValidationError("tactile raw values do not match the shape")
        if self.online is not None and not isinstance(self.online, bool):
            raise ValidationError("tactile online must be bool or None")


@dataclass(frozen=True, slots=True)
class ForceFieldSample:
    """One tactile quantity in finger order, retaining raw values and validity.

    Every finger has a tuple, including scalar fields (length one). ``None``
    means this sensor did not supply the field; it is never a synthetic zero.
    Matrix geometry and descriptor offsets remain in ``ForceSample.tactile``
    and ``ForceSample.sensors``. ``meta`` belongs to this physical query only.
    """

    name: str
    sensor_names: tuple[str, ...]
    raw: tuple[tuple[RawNumber, ...] | None, ...]
    units: tuple[str | None, ...]
    encodings: tuple[str | None, ...]
    valid_values: tuple[tuple[bool, ...] | None, ...]
    meta: SampleMeta
    query_channel: str = "force"
    scale_note: str | None = None

    def __post_init__(self) -> None:
        validate_force_field(self.name)
        if not isinstance(self.meta, SampleMeta):
            raise ValidationError("force field meta must be SampleMeta")
        if (
            not isinstance(self.sensor_names, tuple)
            or not self.sensor_names
            or any(not isinstance(name, str) or not name for name in self.sensor_names)
        ):
            raise ValidationError("force field requires ordered sensor_names")
        if len(set(self.sensor_names)) != len(self.sensor_names):
            raise ValidationError("force field sensor_names must be unique")
        for name in ("raw", "units", "encodings", "valid_values"):
            value = getattr(self, name)
            if not isinstance(value, tuple) or len(value) != len(self.sensor_names):
                raise ValidationError(f"force field {name} must match sensor_names")
        for raw, valid in zip(self.raw, self.valid_values, strict=True):
            if raw is None:
                if valid is not None:
                    raise ValidationError("missing field values cannot claim validity")
                continue
            if (
                not isinstance(raw, tuple)
                or not raw
                or any(not _is_finite_number(value) for value in raw)
            ):
                raise ValidationError("force field raw must contain finite tuples")
            if (
                not isinstance(valid, tuple)
                or len(valid) != len(raw)
                or any(not isinstance(value, bool) for value in valid)
            ):
                raise ValidationError("force field validity must match raw values")
        for value in (*self.units, *self.encodings, self.scale_note):
            if value is not None and (not isinstance(value, str) or not value):
                raise ValidationError("force field metadata must be nonempty or None")
        if not isinstance(self.query_channel, str) or not self.query_channel:
            raise ValidationError("force field query_channel must be nonempty")


@dataclass(frozen=True, slots=True)
class ForceSample:
    """Raw tactile results with independently timestamped public quantities.

    ``meta`` describes the last query, not simultaneous acquisition of every
    field. Snapshot fields may have different ages; getters contain only their
    requested queries and any fields supplied by the same native response.
    """

    raw: tuple[RawNumber, ...]
    tactile: tuple[TactileMatrix, ...]
    meta: SampleMeta
    sensors: tuple[TactileSensorSample, ...] = ()
    fields: tuple[ForceFieldSample, ...] = ()
    requested_fields: tuple[str, ...] = ()
    unsupported_fields: tuple[str, ...] = ()
    query_channel: str = "force"

    def __post_init__(self) -> None:
        if not isinstance(self.raw, tuple) or any(
            not _is_finite_number(value) for value in self.raw
        ):
            raise ValidationError("force raw must be a tuple of finite values")
        if not isinstance(self.tactile, tuple) or any(
            not isinstance(value, TactileMatrix) for value in self.tactile
        ):
            raise ValidationError("tactile must be a tuple of TactileMatrix")
        if not isinstance(self.meta, SampleMeta):
            raise ValidationError("meta must be SampleMeta")
        if not isinstance(self.sensors, tuple) or any(
            not isinstance(value, TactileSensorSample) for value in self.sensors
        ):
            raise ValidationError("sensors must contain TactileSensorSample values")
        if not isinstance(self.fields, tuple) or any(
            not isinstance(value, ForceFieldSample) for value in self.fields
        ):
            raise ValidationError("force fields must contain ForceFieldSample")
        if len({value.name for value in self.fields}) != len(self.fields):
            raise ValidationError("force field names must be unique")
        for names in (self.requested_fields, self.unsupported_fields):
            if not isinstance(names, tuple) or len(set(names)) != len(names):
                raise ValidationError("force field selectors must be unique tuples")
            for name in names:
                validate_force_field(name)
        if set(self.unsupported_fields) & {value.name for value in self.fields}:
            raise ValidationError("available force fields cannot be unsupported")
        if not isinstance(self.query_channel, str) or not self.query_channel:
            raise ValidationError("force query_channel must be nonempty")
        if self.meta.valid and not (
            self.raw or self.tactile or self.sensors or self.fields
        ):
            raise ValidationError("a valid force sample cannot be empty")

    def field(self, name: str) -> ForceFieldSample | None:
        """Look up a lowercase quantity without device I/O."""
        validate_force_field(name)
        return next((field for field in self.fields if field.name == name), None)

    def status(self, name: str) -> str:
        """Distinguish available, invalidated, unsupported and unread fields.

        Failed reads raise; the snapshot retains its preceding complete sample.
        A valid numeric zero is ``available``; inspect ``valid_values`` for
        per-element sentinels such as an undefined direction.
        """
        value = self.field(name)
        if value is not None:
            return "available" if value.meta.valid else "invalidated"
        if name in self.unsupported_fields:
            return "unsupported"
        return "not_read"

    @property
    def fn(self) -> ForceFieldSample | None:
        return self.field("fn")

    @property
    def fns(self) -> ForceFieldSample | None:
        return self.field("fns")

    @property
    def ft(self) -> ForceFieldSample | None:
        return self.field("ft")

    @property
    def fts(self) -> ForceFieldSample | None:
        return self.field("fts")

    @property
    def fd(self) -> ForceFieldSample | None:
        return self.field("fd")

    @property
    def fds(self) -> ForceFieldSample | None:
        return self.field("fds")

    @property
    def px(self) -> ForceFieldSample | None:
        return self.field("px")


@dataclass(frozen=True, slots=True)
class NormalForceResultantSample:
    """One raw O30 FnS value and device-reported unit for every fingertip."""

    sensor_names: tuple[str, ...]
    raw: tuple[RawNumber, ...]
    units: tuple[str | None, ...]
    meta: SampleMeta

    def __post_init__(self) -> None:
        if not isinstance(self.sensor_names, tuple) or any(
            not isinstance(value, str) or not value for value in self.sensor_names
        ):
            raise ValidationError("sensor_names must contain non-empty strings")
        if len(set(self.sensor_names)) != len(self.sensor_names):
            raise ValidationError("sensor_names must be unique")
        if not isinstance(self.raw, tuple) or any(
            not _is_finite_number(value) for value in self.raw
        ):
            raise ValidationError("resultant raw must contain finite protocol values")
        if not isinstance(self.units, tuple) or any(
            value is not None and (not isinstance(value, str) or not value)
            for value in self.units
        ):
            raise ValidationError("resultant units must contain strings or None")
        if not isinstance(self.meta, SampleMeta):
            raise ValidationError("meta must be SampleMeta")
        if len(self.sensor_names) != len(self.raw) or len(self.raw) != len(self.units):
            raise ValidationError("resultant names, raw values, and units must align")
        if self.meta.valid and not self.raw:
            raise ValidationError("a valid resultant sample cannot be empty")

    @classmethod
    def from_force_sample(cls, sample: ForceSample) -> NormalForceResultantSample:
        """Extract one scalar ``FnS`` value per fingertip without conversion."""

        if not isinstance(sample, ForceSample):
            raise ValidationError("sample must be ForceSample")
        expected_names = ("thumb", "index", "middle", "ring", "pinky")
        if (
            tuple(sensor.metadata.sensor_name for sensor in sample.sensors)
            != expected_names
        ):
            raise UnsupportedOperationError(
                "force sample does not contain all five O30 fingertip sensors"
            )

        raw: list[RawNumber] = []
        units: list[str | None] = []
        for sensor in sample.sensors:
            fields = tuple(field for field in sensor.fields if field.semantic == "FnS")
            if len(fields) != 1 or len(fields[0].values) != 1:
                raise UnsupportedOperationError(
                    f"O30 {sensor.metadata.sensor_name} sensor does not expose "
                    "one scalar FnS field"
                )
            value = fields[0].values[0]
            if isinstance(value, tuple):
                raise ValidationError("FnS must contain scalar protocol values")
            raw.append(value)
            units.append(sensor.metadata.data_unit)
        return cls(
            sensor_names=expected_names,
            raw=tuple(raw),
            units=tuple(units),
            meta=sample.meta,
        )


__all__ = [
    "AngleSample",
    "ConfiguredAccelerationSample",
    "ConfiguredSpeedSample",
    "CurrentLimitSample",
    "CurrentSample",
    "EffortLimitSample",
    "FaultSample",
    "ForceFieldSample",
    "ForceSample",
    "JointFaultStatus",
    "MeasuredSpeedSample",
    "MotionTimeSample",
    "NormalForceResultantSample",
    "RawNumber",
    "SampleMeta",
    "TactileFieldDescriptor",
    "TactileFieldSample",
    "TactileMatrix",
    "TactileSensorMetadata",
    "TactileSensorSample",
    "TactileValue",
    "TemperatureSample",
    "TorqueSample",
    "VoltageSample",
]
