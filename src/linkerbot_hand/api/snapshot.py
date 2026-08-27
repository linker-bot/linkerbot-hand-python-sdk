"""Atomic, immutable cached hand state."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .device_info import DeviceInfo
from .errors import ValidationError
from .readings import (
    AngleSample,
    ConfiguredSpeedSample,
    CurrentLimitSample,
    CurrentSample,
    EffortLimitSample,
    FaultSample,
    ForceSample,
    MeasuredSpeedSample,
    RawNumber,
    TemperatureSample,
    TorqueSample,
    VoltageSample,
)
from .stall import (
    StallDetectionTimeSample,
    StallHoldCurrentSample,
    StallThresholdSample,
)


@dataclass(frozen=True, slots=True)
class JointState:
    """Convenient per-joint view derived from canonical vector samples."""

    index: int
    name: str
    angle_raw: RawNumber | None = None
    angle_percent: float | None = None
    physical_rad: float | None = None
    measured_speed_raw: RawNumber | None = None
    configured_speed_raw: RawNumber | None = None
    current_raw: RawNumber | None = None
    current_limit_raw: RawNumber | None = None
    effort_limit_raw: RawNumber | None = None
    torque_raw: RawNumber | None = None
    temperature_raw: RawNumber | None = None
    fault_raw: int | None = None

    def __post_init__(self) -> None:
        if type(self.index) is not int or self.index < 0:
            raise ValidationError("joint index must be a non-negative int")
        if not isinstance(self.name, str) or not self.name:
            raise ValidationError("joint name must be a non-empty str")
        for name in (
            "angle_raw",
            "angle_percent",
            "physical_rad",
            "measured_speed_raw",
            "configured_speed_raw",
            "current_raw",
            "current_limit_raw",
            "effort_limit_raw",
            "torque_raw",
            "temperature_raw",
        ):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValidationError(f"{name} must be finite numeric or None")
        if self.fault_raw is not None and type(self.fault_raw) is not int:
            raise ValidationError("fault_raw must be int or None")


@dataclass(frozen=True, slots=True)
class HandSnapshot:
    """One copy-on-write view of every cached channel for a device."""

    captured_at: float
    snapshot_sequence: int
    connection_generation: int
    joints: tuple[JointState, ...]
    device_info: DeviceInfo | None = None
    angles: AngleSample | None = None
    measured_speeds: MeasuredSpeedSample | None = None
    configured_speeds: ConfiguredSpeedSample | None = None
    currents: CurrentSample | None = None
    current_limits: CurrentLimitSample | None = None
    effort_limits: EffortLimitSample | None = None
    torques: TorqueSample | None = None
    temperatures: TemperatureSample | None = None
    force: ForceSample | None = None
    faults: FaultSample | None = None
    voltages: VoltageSample | None = None
    stall_detection_times: StallDetectionTimeSample | None = None
    stall_thresholds: StallThresholdSample | None = None
    stall_hold_currents: StallHoldCurrentSample | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.captured_at, bool)
            or not isinstance(self.captured_at, (int, float))
            or not math.isfinite(self.captured_at)
        ):
            raise ValidationError("captured_at must be finite numeric")
        for name in ("snapshot_sequence", "connection_generation"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValidationError(f"{name} must be a non-negative int")
        if not isinstance(self.joints, tuple) or any(
            not isinstance(joint, JointState) for joint in self.joints
        ):
            raise ValidationError("joints must be a tuple of JointState")
        if tuple(joint.index for joint in self.joints) != tuple(
            range(len(self.joints))
        ):
            raise ValidationError("joint indices must be contiguous and ordered")
        if len({joint.name for joint in self.joints}) != len(self.joints):
            raise ValidationError("joint names must be unique")


__all__ = ["HandSnapshot", "JointState"]
