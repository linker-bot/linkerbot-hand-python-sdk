"""Command, telemetry, and stall-protection capability contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..api.commands import AckSupport
from ..api.enums import Confidence, ReadMode
from ..api.errors import ValidationError
from .ranges import NumericRange, PercentMapping, is_finite_number


class CommandSemantic(str, Enum):
    """Meaning of one writable device protocol object."""

    ANGLES = "angles"
    SPEEDS = "speeds"
    ACCELERATIONS = "accelerations"
    CURRENT_LIMITS = "current_limits"
    EFFORT_LIMITS = "effort_limits"
    TORQUES = "torques"
    MOTION_TIMES = "motion_times"
    STALL_DETECTION_TIMES = "stall_detection_times"
    STALL_THRESHOLDS = "stall_thresholds"
    STALL_HOLD_CURRENTS = "stall_hold_currents"
    LIMIT_COMPENSATIONS = "limit_compensations"


class TelemetrySemantic(str, Enum):
    """Meaning of one readable or cached device state channel."""

    ANGLES = "angles"
    MEASURED_SPEEDS = "measured_speeds"
    CONFIGURED_SPEEDS = "configured_speeds"
    CONFIGURED_ACCELERATIONS = "configured_accelerations"
    CURRENTS = "currents"
    CURRENT_LIMITS = "current_limits"
    EFFORT_LIMITS = "effort_limits"
    TORQUES = "torques"
    TEMPERATURES = "temperatures"
    FORCE = "force"
    FAULTS = "faults"
    VOLTAGES = "voltages"
    MOTION_TIMES = "motion_times"
    STALL_DETECTION_TIMES = "stall_detection_times"
    STALL_THRESHOLDS = "stall_thresholds"
    STALL_HOLD_CURRENTS = "stall_hold_currents"
    LIMIT_COMPENSATIONS = "limit_compensations"


def _validate_channels(channels: tuple[str, ...]) -> None:
    if not isinstance(channels, tuple) or not channels:
        raise ValidationError("channels must be a non-empty tuple")
    if any(not isinstance(channel, str) or not channel for channel in channels):
        raise ValidationError("channels must contain non-empty strings")
    if len(set(channels)) != len(channels):
        raise ValidationError("channels must be unique")


def _validate_unit_metadata(
    raw_unit: str | None,
    raw_scale_note: str | None,
    unit_confidence: Confidence,
) -> None:
    for name, value in (("raw_unit", raw_unit), ("raw_scale_note", raw_scale_note)):
        if value is not None and (not isinstance(value, str) or not value):
            raise ValidationError(f"{name} must be a non-empty str or None")
    if not isinstance(unit_confidence, Confidence):
        raise ValidationError("unit_confidence must be Confidence")


@dataclass(frozen=True, slots=True)
class CommandCapability:
    """Protocol encoding and ranges for one writable semantic."""

    semantic: CommandSemantic
    channels: tuple[str, ...]
    raw_ranges: tuple[NumericRange, ...]
    raw_encoding: str
    raw_unit: str | None
    raw_scale_note: str | None
    unit_confidence: Confidence
    ack_support: AckSupport = AckSupport.UNSUPPORTED
    returns_current_state: bool = False
    percent_mappings: tuple[PercentMapping | None, ...] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.semantic, CommandSemantic):
            raise ValidationError("semantic must be CommandSemantic")
        _validate_channels(self.channels)
        if (
            not isinstance(self.raw_ranges, tuple)
            or not self.raw_ranges
            or any(not isinstance(value, NumericRange) for value in self.raw_ranges)
        ):
            raise ValidationError("raw_ranges must contain NumericRange values")
        if len(self.raw_ranges) not in {1, len(self.channels)}:
            raise ValidationError("raw_ranges must be shared or match channels")
        if not isinstance(self.raw_encoding, str) or not self.raw_encoding:
            raise ValidationError("raw_encoding must be a non-empty str")
        _validate_unit_metadata(
            self.raw_unit,
            self.raw_scale_note,
            self.unit_confidence,
        )
        if not isinstance(self.ack_support, AckSupport):
            raise ValidationError("ack_support must be AckSupport")
        if not isinstance(self.returns_current_state, bool):
            raise ValidationError("returns_current_state must be bool")
        if self.percent_mappings is not None:
            if not isinstance(self.percent_mappings, tuple) or len(
                self.percent_mappings
            ) != len(self.channels):
                raise ValidationError("percent_mappings must match channels")
            if any(
                value is not None and not isinstance(value, PercentMapping)
                for value in self.percent_mappings
            ):
                raise ValidationError(
                    "percent_mappings must contain PercentMapping or None"
                )

    def range_for(self, index: int) -> NumericRange:
        """Return the shared or channel-specific raw range."""

        return (
            self.raw_ranges[0] if len(self.raw_ranges) == 1 else self.raw_ranges[index]
        )


@dataclass(frozen=True, slots=True)
class TelemetryCapability:
    """Read mode, raw metadata, and scheduler costs for one state channel."""

    semantic: TelemetrySemantic
    channels: tuple[str, ...]
    read_mode: ReadMode
    raw_encoding: str
    shape: tuple[int, ...]
    raw_range: NumericRange | None
    raw_unit: str | None
    raw_scale_note: str | None
    unit_confidence: Confidence
    recommended_hz: float
    stale_after_s: float
    estimated_transaction_s: float
    verified_max_hz: float | None = None
    device_measured: bool = True
    device_queried: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.semantic, TelemetrySemantic):
            raise ValidationError("semantic must be TelemetrySemantic")
        _validate_channels(self.channels)
        if not isinstance(self.read_mode, ReadMode):
            raise ValidationError("read_mode must be ReadMode")
        if not isinstance(self.raw_encoding, str) or not self.raw_encoding:
            raise ValidationError("raw_encoding must be a non-empty str")
        if (
            not isinstance(self.shape, tuple)
            or not self.shape
            or any(type(value) is not int or value <= 0 for value in self.shape)
        ):
            raise ValidationError("shape must contain positive ints")
        if self.raw_range is not None and not isinstance(self.raw_range, NumericRange):
            raise ValidationError("raw_range must be NumericRange or None")
        _validate_unit_metadata(
            self.raw_unit,
            self.raw_scale_note,
            self.unit_confidence,
        )
        for name in ("recommended_hz", "estimated_transaction_s"):
            value = getattr(self, name)
            minimum = (
                0
                if self.read_mode in {ReadMode.COMMAND_CACHE, ReadMode.COMMAND_RESPONSE}
                else 1e-300
            )
            if not is_finite_number(value) or value < minimum:
                qualifier = "non-negative" if minimum == 0 else "positive"
                raise ValidationError(f"{name} must be finite and {qualifier}")
        if not is_finite_number(self.stale_after_s) or self.stale_after_s <= 0:
            raise ValidationError("stale_after_s must be finite and positive")
        if self.verified_max_hz is not None and (
            not is_finite_number(self.verified_max_hz) or self.verified_max_hz <= 0
        ):
            raise ValidationError("verified_max_hz must be positive or None")
        if not isinstance(self.device_measured, bool) or not isinstance(
            self.device_queried, bool
        ):
            raise ValidationError("device measurement flags must be bool")
        if (
            self.read_mode
            in {
                ReadMode.COMMAND_CACHE,
                ReadMode.COMMAND_RESPONSE,
            }
            and self.device_queried
        ):
            raise ValidationError("command-derived telemetry cannot be device queried")


@dataclass(frozen=True, slots=True)
class StallFieldCapability:
    """Raw metadata for one stall-protection protocol object."""

    raw_range: NumericRange
    raw_encoding: str
    raw_unit: str | None
    raw_scale_note: str | None
    unit_confidence: Confidence

    def __post_init__(self) -> None:
        if not isinstance(self.raw_range, NumericRange):
            raise ValidationError("raw_range must be NumericRange")
        if not isinstance(self.raw_encoding, str) or not self.raw_encoding:
            raise ValidationError("raw_encoding must be a non-empty str")
        _validate_unit_metadata(
            self.raw_unit,
            self.raw_scale_note,
            self.unit_confidence,
        )


@dataclass(frozen=True, slots=True)
class StallProtectionCapability:
    """Installed-channel mapping and confidence for stall configuration."""

    channels: tuple[str, ...]
    detection_time: StallFieldCapability
    threshold: StallFieldCapability
    hold_current: StallFieldCapability
    physical_semantics_confidence: Confidence
    safety_range_verified: bool
    read_supported: bool
    write_supported: bool

    def __post_init__(self) -> None:
        _validate_channels(self.channels)
        for name in ("detection_time", "threshold", "hold_current"):
            if not isinstance(getattr(self, name), StallFieldCapability):
                raise ValidationError(f"{name} must be StallFieldCapability")
        if not isinstance(self.physical_semantics_confidence, Confidence):
            raise ValidationError("physical_semantics_confidence must be Confidence")
        for name in ("safety_range_verified", "read_supported", "write_supported"):
            if not isinstance(getattr(self, name), bool):
                raise ValidationError(f"{name} must be bool")


__all__ = [
    "CommandCapability",
    "CommandSemantic",
    "StallFieldCapability",
    "StallProtectionCapability",
    "TelemetryCapability",
    "TelemetrySemantic",
]
