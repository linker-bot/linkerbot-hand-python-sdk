"""Typed-sample construction and immutable snapshot projection helpers."""

from __future__ import annotations

import math
import threading
from dataclasses import fields, replace
from functools import lru_cache
from typing import TypeVar, cast

from ..api.device_info import DeviceInfo
from ..api.errors import ProtocolError
from ..api.readings import (
    AngleSample,
    ConfiguredAccelerationSample,
    ConfiguredSpeedSample,
    CurrentLimitSample,
    CurrentSample,
    FaultSample,
    ForceFieldSample,
    ForceSample,
    MeasuredSpeedSample,
    MotionTimeSample,
    SampleMeta,
    TemperatureSample,
    TorqueLimitSample,
    TorqueSample,
    VoltageSample,
    _RawVectorSample,
)
from ..api.snapshot import HandSnapshot, JointState
from ..api.stall import (
    LimitCompensationSample,
    StallDetectionTimeSample,
    StallHoldCurrentSample,
    StallThresholdSample,
)
from ..capabilities.force import FORCE_QUERIES
from ..capabilities.types import DeviceCapabilities, TelemetrySemantic
from ..conversion.percent import (
    raw_angles_to_percent,
    raw_angles_to_physical_rad,
    raw_angles_within_declared_range,
)
from ..protocols.base import SamplePayload
from ._force_samples import build_force_sample

SAMPLE_ATTRIBUTES = {
    TelemetrySemantic.ANGLES: "angles",
    TelemetrySemantic.MEASURED_SPEEDS: "measured_speeds",
    TelemetrySemantic.CONFIGURED_SPEEDS: "configured_speeds",
    TelemetrySemantic.CONFIGURED_ACCELERATIONS: "configured_accelerations",
    TelemetrySemantic.CURRENTS: "currents",
    TelemetrySemantic.CURRENT_LIMITS: "current_limits",
    TelemetrySemantic.TORQUES_LIMITS: "torques_limits",
    TelemetrySemantic.TORQUES: "torques",
    TelemetrySemantic.TEMPERATURES: "temperatures",
    TelemetrySemantic.FORCE: "force",
    TelemetrySemantic.FORCE_FNS: "force",
    TelemetrySemantic.FORCE_FT: "force",
    TelemetrySemantic.FORCE_FTS: "force",
    TelemetrySemantic.FORCE_FD: "force",
    TelemetrySemantic.FORCE_FDS: "force",
    TelemetrySemantic.FORCE_PX: "force",
    TelemetrySemantic.FAULTS: "faults",
    TelemetrySemantic.VOLTAGES: "voltages",
    TelemetrySemantic.MOTION_TIMES: "motion_times",
    TelemetrySemantic.STALL_DETECTION_TIMES: "stall_detection_times",
    TelemetrySemantic.STALL_THRESHOLDS: "stall_thresholds",
    TelemetrySemantic.STALL_HOLD_CURRENTS: "stall_hold_currents",
    TelemetrySemantic.LIMIT_COMPENSATIONS: "limit_compensations",
}

_VECTOR_TYPES: dict[TelemetrySemantic, type[_RawVectorSample]] = {
    TelemetrySemantic.MEASURED_SPEEDS: MeasuredSpeedSample,
    TelemetrySemantic.CONFIGURED_SPEEDS: ConfiguredSpeedSample,
    TelemetrySemantic.CONFIGURED_ACCELERATIONS: ConfiguredAccelerationSample,
    TelemetrySemantic.CURRENTS: CurrentSample,
    TelemetrySemantic.CURRENT_LIMITS: CurrentLimitSample,
    TelemetrySemantic.TORQUES_LIMITS: TorqueLimitSample,
    TelemetrySemantic.TORQUES: TorqueSample,
    TelemetrySemantic.TEMPERATURES: TemperatureSample,
    TelemetrySemantic.FAULTS: FaultSample,
    TelemetrySemantic.VOLTAGES: VoltageSample,
    TelemetrySemantic.MOTION_TIMES: MotionTimeSample,
    TelemetrySemantic.STALL_DETECTION_TIMES: StallDetectionTimeSample,
    TelemetrySemantic.STALL_THRESHOLDS: StallThresholdSample,
    TelemetrySemantic.STALL_HOLD_CURRENTS: StallHoldCurrentSample,
    TelemetrySemantic.LIMIT_COMPENSATIONS: LimitCompensationSample,
}


_SAMPLE_TYPES = frozenset(
    (AngleSample, ForceSample, ForceFieldSample, DeviceInfo, *_VECTOR_TYPES.values())
)
_SampleT = TypeVar(
    "_SampleT",
    bound=AngleSample | ForceSample | ForceFieldSample | _RawVectorSample | DeviceInfo,
)


def build_sample(
    payload: SamplePayload,
    meta: SampleMeta,
    capabilities: DeviceCapabilities,
) -> AngleSample | ForceSample | _RawVectorSample:
    """Create the public typed sample for one validated protocol payload."""

    if payload.semantic is TelemetrySemantic.ANGLES:
        return AngleSample(
            raw=payload.raw,
            percent=raw_angles_to_percent(payload.raw, capabilities),
            physical_rad=raw_angles_to_physical_rad(payload.raw, capabilities),
            meta=meta,
        )
    if payload.semantic in FORCE_QUERIES:
        return build_force_sample(payload, meta, capabilities)
    if payload.semantic is TelemetrySemantic.FAULTS:
        return FaultSample(
            raw=payload.raw,
            meta=meta,
            statuses=payload.fault_statuses,
        )
    sample_type = _VECTOR_TYPES[payload.semantic]
    return sample_type(raw=payload.raw, meta=meta)


def validate_payload_shape(
    payload: SamplePayload, capabilities: DeviceCapabilities
) -> None:
    """Check decoded payload shape against the selected profile."""

    telemetry = capabilities.telemetry_channel(payload.semantic)
    if telemetry is None:
        raise ProtocolError("payload has no telemetry declaration")
    if payload.semantic is TelemetrySemantic.FORCE and (
        payload.tactile or payload.sensors
    ):
        if payload.sensors:
            if payload.tactile:
                raise ProtocolError(
                    "force payload cannot mix tactile matrices and sensors"
                )
            if len(telemetry.shape) != 1 or telemetry.shape[0] != len(
                telemetry.channels
            ):
                raise ProtocolError(
                    "descriptor tactile capability must declare sensor count"
                )
            if len(payload.sensors) != telemetry.shape[0]:
                raise ProtocolError("tactile sensor count does not match capability")
            expected_selectors = tuple(range(1, len(payload.sensors) + 1))
            if (
                tuple(sensor.metadata.selected_sensor for sensor in payload.sensors)
                != expected_selectors
            ):
                raise ProtocolError("tactile sensor selectors are not ordered")
            if (
                tuple(sensor.metadata.sensor_name for sensor in payload.sensors)
                != telemetry.channels
            ):
                raise ProtocolError("tactile sensor names do not match capability")
            if not any(sensor.metadata.available for sensor in payload.sensors):
                raise ProtocolError("tactile payload has no available sensor")
            if any(
                sensor.metadata.available
                and len(sensor.raw_data) != sensor.metadata.total_data_length
                for sensor in payload.sensors
            ):
                raise ProtocolError(
                    "tactile sensor data length does not match metadata"
                )
            return
        expected = telemetry.shape
        if len(expected) != 3 or len(payload.tactile) != expected[0]:
            raise ProtocolError("tactile payload does not match declared shape")
        for channel, matrix in zip(telemetry.channels, payload.tactile, strict=True):
            if (
                matrix.name != channel
                or matrix.rows != expected[1]
                or matrix.columns != expected[2]
            ):
                raise ProtocolError("tactile matrix does not match capability")
        return
    expected_values = math.prod(telemetry.shape)
    if len(payload.raw) != expected_values:
        raise ProtocolError(
            f"{payload.semantic.value} returned {len(payload.raw)} values; "
            f"expected {expected_values}"
        )


def payload_within_range(
    payload: SamplePayload, capabilities: DeviceCapabilities
) -> bool:
    """Preserve out-of-range evidence while deriving one metadata flag."""

    if not payload.within_declared_range:
        return False
    if payload.semantic is TelemetrySemantic.ANGLES:
        return raw_angles_within_declared_range(payload.raw, capabilities)
    telemetry = capabilities.telemetry_channel(payload.semantic)
    if telemetry is None or telemetry.raw_range is None:
        return True
    values = payload.raw + tuple(
        value for matrix in payload.tactile for row in matrix.raw for value in row
    )
    return all(telemetry.raw_range.contains(value) for value in values)


def age_sample(
    semantic: TelemetrySemantic,
    sample: object,
    *,
    monotonic_now: float,
    collected_monotonic: float | None,
    capabilities: DeviceCapabilities,
    generation: int,
    force_collected: dict[TelemetrySemantic, float] | None = None,
) -> object:
    """Return one sample with an age derived from monotonic time only."""

    typed = cast(AngleSample | ForceSample | _RawVectorSample, sample)
    if isinstance(typed, ForceSample) and force_collected is not None:
        fields = tuple(
            age_sample(
                TelemetrySemantic(field.query_channel),
                field,
                monotonic_now=monotonic_now,
                collected_monotonic=force_collected.get(
                    TelemetrySemantic(field.query_channel)
                ),
                capabilities=capabilities,
                generation=generation,
            )
            for field in typed.fields
        )
        aged = cast(
            ForceSample,
            age_sample(
                TelemetrySemantic(typed.query_channel),
                typed,
                monotonic_now=monotonic_now,
                collected_monotonic=force_collected.get(
                    TelemetrySemantic(typed.query_channel)
                ),
                capabilities=capabilities,
                generation=generation,
            ),
        )
        if aged is typed:
            aged = _with_meta(typed, typed.meta)
        object.__setattr__(aged, "fields", fields)
        return aged
    if not typed.meta.valid:
        return typed
    if collected_monotonic is None:
        return _with_meta(
            typed,
            SampleMeta.unavailable(connection_generation=generation),
        )
    age = max(0.0, monotonic_now - collected_monotonic)
    telemetry = capabilities.telemetry_channel(semantic)
    stale = telemetry is None or age > telemetry.stale_after_s
    return _with_meta(typed, replace(typed.meta, age_s=age, stale=stale))


def age_device_info(
    device_info: DeviceInfo,
    *,
    monotonic_now: float,
    collected_monotonic: float | None,
    generation: int,
) -> DeviceInfo:
    """Age verified identity without imposing a telemetry stale threshold."""

    if not device_info.meta.valid:
        return device_info
    if collected_monotonic is None:
        return _with_meta(
            device_info,
            SampleMeta.unavailable(connection_generation=generation),
        )
    return _with_meta(
        device_info,
        replace(
            device_info.meta,
            age_s=max(0.0, monotonic_now - collected_monotonic),
            stale=False,
        ),
    )


@lru_cache(maxsize=32)
def _sample_fields(
    cls: type[
        AngleSample | ForceSample | ForceFieldSample | _RawVectorSample | DeviceInfo
    ],
) -> tuple[str, ...]:
    return tuple(field.name for field in fields(cls) if field.name != "meta")


def _with_meta(sample: _SampleT, meta: SampleMeta) -> _SampleT:
    """Age an already validated immutable sample without revalidating its data.

    Only this module's aging paths use this helper: metadata is constructed via
    the normal validator, and validity can only stay valid or become invalid.
    Data fields are shared immutable values, never user-supplied replacements.
    Public constructors/replace retain their full input validation.
    """
    cls = type(sample)
    if cls not in _SAMPLE_TYPES:
        return replace(sample, meta=meta)
    result = object.__new__(cls)
    for name in _sample_fields(cls):
        object.__setattr__(result, name, getattr(sample, name))
    object.__setattr__(result, "meta", meta)
    if isinstance(sample, ForceSample) and not meta.valid:
        object.__setattr__(
            result, "fields", tuple(_with_meta(field, meta) for field in sample.fields)
        )
    return result


def build_joints(
    snapshot: HandSnapshot,
    capabilities: DeviceCapabilities,
    *,
    samples: dict[str, object] | None = None,
) -> tuple[JointState, ...]:
    """Project canonical vector samples into the convenient joint view."""

    def sample(name: str):
        return getattr(snapshot, name) if samples is None else samples[name]

    return tuple(
        JointState(
            index=joint.index,
            name=joint.name,
            angle_raw=_value(sample("angles"), index, "raw"),
            angle_percent=_value(sample("angles"), index, "percent"),
            physical_rad=_value(sample("angles"), index, "physical_rad"),
            measured_speed_raw=_value(sample("measured_speeds"), index),
            configured_speed_raw=_value(sample("configured_speeds"), index),
            current_raw=_value(sample("currents"), index),
            current_limit_raw=_value(sample("current_limits"), index),
            torque_limit_raw=_value(sample("torques_limits"), index),
            torque_raw=_value(sample("torques"), index),
            temperature_raw=_value(sample("temperatures"), index),
            fault_raw=_fault_value(sample("faults"), index),
        )
        for index, joint in enumerate(capabilities.joints)
    )


class JointProjection:
    """One bounded, read-side cache of an immutable per-joint projection.

    Only the vector identities and validity affect JointState, not age, sequence
    or tactile data. Retaining the vectors makes identity comparison safe even
    when a concurrent publisher replaces the canonical snapshot.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sources: tuple[object, ...] = ()
        self._joints: tuple[JointState, ...] = ()

    def get(
        self,
        snapshot: HandSnapshot,
        capabilities: DeviceCapabilities,
        *,
        samples: dict[str, object] | None = None,
    ) -> tuple[JointState, ...]:
        def sample(name):
            return getattr(snapshot, name) if samples is None else samples[name]

        angles = cast(AngleSample | None, sample("angles"))
        sources = (
            capabilities.joints,
            angles.percent if angles is not None and angles.meta.valid else None,
            angles.physical_rad if angles is not None and angles.meta.valid else None,
            *(
                sample.raw if sample is not None and sample.meta.valid else None
                for sample in (
                    angles,
                    sample("measured_speeds"),
                    sample("configured_speeds"),
                    sample("currents"),
                    sample("current_limits"),
                    sample("torques_limits"),
                    sample("torques"),
                    sample("temperatures"),
                    sample("faults"),
                )
            ),
        )
        with self._lock:
            if len(sources) == len(self._sources) and all(
                current is cached for current, cached in zip(sources, self._sources)
            ):
                return self._joints
        # Projection only needs attributes, not another validated HandSnapshot.
        joints = build_joints(snapshot, capabilities, samples=samples)
        with self._lock:
            self._sources, self._joints = sources, joints
        return joints


def snapshot_with_generation(
    snapshot: HandSnapshot, generation: int, captured_at: float
) -> HandSnapshot:
    """Reset a shaped snapshot to one entirely invalid generation."""

    updates: dict[str, object] = {
        "captured_at": captured_at,
        "connection_generation": generation,
        "snapshot_sequence": snapshot.snapshot_sequence + 1,
    }
    for attribute in dict.fromkeys(SAMPLE_ATTRIBUTES.values()):
        sample = getattr(snapshot, attribute)
        if sample is not None:
            updates[attribute] = _with_meta(
                sample, SampleMeta.unavailable(connection_generation=generation)
            )
    return replace(snapshot, **updates)


def invalidate_snapshot(
    snapshot: HandSnapshot, *, generation: int, captured_at: float
) -> HandSnapshot:
    """Invalidate all old data and clear the derived joint projection."""

    updates: dict[str, object] = {
        "captured_at": captured_at,
        "snapshot_sequence": snapshot.snapshot_sequence + 1,
        "joints": tuple(
            JointState(index=joint.index, name=joint.name) for joint in snapshot.joints
        ),
    }
    for attribute in dict.fromkeys(SAMPLE_ATTRIBUTES.values()):
        sample = getattr(snapshot, attribute)
        if sample is not None:
            updates[attribute] = _with_meta(
                sample, SampleMeta.unavailable(connection_generation=generation)
            )
    if snapshot.device_info is not None:
        updates["device_info"] = replace(
            snapshot.device_info,
            meta=SampleMeta.unavailable(connection_generation=generation),
        )
    return replace(snapshot, **updates)


def _value(sample: object | None, index: int, attribute: str = "raw"):
    if sample is None or not sample.meta.valid:  # ty: ignore[unresolved-attribute]
        return None
    values = getattr(sample, attribute)
    return None if index >= len(values) else values[index]


def _fault_value(sample: FaultSample | None, index: int) -> int | None:
    value = _value(sample, index)
    return value if type(value) is int else None


__all__ = [
    "SAMPLE_ATTRIBUTES",
    "JointProjection",
    "age_device_info",
    "age_sample",
    "build_joints",
    "build_sample",
    "invalidate_snapshot",
    "payload_within_range",
    "snapshot_with_generation",
    "validate_payload_shape",
]
