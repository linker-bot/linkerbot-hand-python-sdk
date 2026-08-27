"""Immutable motion and sensor samples returned by Hand SDK 1.0."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TypeAlias

from .enums import DataOrigin
from .errors import ValidationError

RawNumber: TypeAlias = int | float


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
class FaultSample(_RawVectorSample):
    """Device fault codes or bitmasks in protocol order."""

    def __post_init__(self) -> None:
        _RawVectorSample.__post_init__(self)
        if any(type(value) is not int for value in self.raw):
            raise ValidationError("fault raw values must be integers")


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
class ForceSample:
    """Scalar force values and/or complete tactile matrices from one transaction."""

    raw: tuple[RawNumber, ...]
    tactile: tuple[TactileMatrix, ...]
    meta: SampleMeta

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
        if self.meta.valid and not self.raw and not self.tactile:
            raise ValidationError("a valid force sample cannot be empty")


__all__ = [
    "AngleSample",
    "ConfiguredAccelerationSample",
    "ConfiguredSpeedSample",
    "CurrentLimitSample",
    "CurrentSample",
    "EffortLimitSample",
    "FaultSample",
    "ForceSample",
    "MeasuredSpeedSample",
    "MotionTimeSample",
    "RawNumber",
    "SampleMeta",
    "TactileMatrix",
    "TemperatureSample",
    "TorqueSample",
    "VoltageSample",
]
