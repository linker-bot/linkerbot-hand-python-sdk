"""Deterministic zero-I/O state created before the first device open."""

from __future__ import annotations

from typing import TypeVar

from ..capabilities.types import (
    DeviceCapabilities,
    TelemetryCapability,
    TelemetrySemantic,
)
from .commands import CommandFamily
from .enums import DataOrigin
from .errors import LifecycleError
from .health import (
    ChannelMetrics,
    CommandFamilyMetrics,
    ConnectionState,
    DataAge,
    HealthStatus,
    LatencyMetrics,
    LifecycleState,
    TransportMetrics,
)
from .readings import (
    AngleSample,
    ConfiguredAccelerationSample,
    ConfiguredSpeedSample,
    CurrentLimitSample,
    CurrentSample,
    EffortLimitSample,
    FaultSample,
    ForceSample,
    MeasuredSpeedSample,
    MotionTimeSample,
    SampleMeta,
    TemperatureSample,
    TorqueSample,
    VoltageSample,
    _RawVectorSample,
)
from .snapshot import HandSnapshot, JointState
from .stall import (
    LimitCompensationSample,
    StallDetectionTimeSample,
    StallHoldCurrentSample,
    StallThresholdSample,
)

_RawSampleT = TypeVar("_RawSampleT", bound=_RawVectorSample)


def make_initial_snapshot(capabilities: DeviceCapabilities) -> HandSnapshot:
    """Build a shaped, entirely invalid snapshot without reading a clock."""

    angles = capabilities.telemetry_channel(TelemetrySemantic.ANGLES)
    if angles is None:
        raise LifecycleError("capability profile has no angle telemetry")
    return HandSnapshot(
        captured_at=0.0,
        snapshot_sequence=0,
        connection_generation=0,
        joints=tuple(
            JointState(index=joint.index, name=joint.name)
            for joint in capabilities.joints
        ),
        angles=AngleSample.unavailable(len(angles.channels)),
        measured_speeds=_unavailable_vector(
            capabilities,
            TelemetrySemantic.MEASURED_SPEEDS,
            MeasuredSpeedSample,
        ),
        configured_speeds=_unavailable_vector(
            capabilities,
            TelemetrySemantic.CONFIGURED_SPEEDS,
            ConfiguredSpeedSample,
        ),
        configured_accelerations=_unavailable_vector(
            capabilities,
            TelemetrySemantic.CONFIGURED_ACCELERATIONS,
            ConfiguredAccelerationSample,
        ),
        currents=_unavailable_vector(
            capabilities,
            TelemetrySemantic.CURRENTS,
            CurrentSample,
        ),
        current_limits=_unavailable_vector(
            capabilities,
            TelemetrySemantic.CURRENT_LIMITS,
            CurrentLimitSample,
        ),
        effort_limits=_unavailable_vector(
            capabilities,
            TelemetrySemantic.EFFORT_LIMITS,
            EffortLimitSample,
        ),
        torques=_unavailable_vector(
            capabilities,
            TelemetrySemantic.TORQUES,
            TorqueSample,
        ),
        temperatures=_unavailable_vector(
            capabilities,
            TelemetrySemantic.TEMPERATURES,
            TemperatureSample,
        ),
        force=_unavailable_force(capabilities),
        faults=_unavailable_vector(
            capabilities,
            TelemetrySemantic.FAULTS,
            FaultSample,
        ),
        voltages=_unavailable_vector(
            capabilities,
            TelemetrySemantic.VOLTAGES,
            VoltageSample,
        ),
        motion_times=_unavailable_vector(
            capabilities,
            TelemetrySemantic.MOTION_TIMES,
            MotionTimeSample,
        ),
        stall_detection_times=_unavailable_vector(
            capabilities,
            TelemetrySemantic.STALL_DETECTION_TIMES,
            StallDetectionTimeSample,
        ),
        stall_thresholds=_unavailable_vector(
            capabilities,
            TelemetrySemantic.STALL_THRESHOLDS,
            StallThresholdSample,
        ),
        stall_hold_currents=_unavailable_vector(
            capabilities,
            TelemetrySemantic.STALL_HOLD_CURRENTS,
            StallHoldCurrentSample,
        ),
        limit_compensations=_unavailable_vector(
            capabilities,
            TelemetrySemantic.LIMIT_COMPENSATIONS,
            LimitCompensationSample,
        ),
    )


def _unavailable_vector(
    capabilities: DeviceCapabilities,
    semantic: TelemetrySemantic,
    sample_type: type[_RawSampleT],
) -> _RawSampleT | None:
    telemetry = capabilities.telemetry_channel(semantic)
    if telemetry is None:
        return None
    fill = _placeholder_value(telemetry)
    return sample_type(
        raw=(fill,) * len(telemetry.channels),
        meta=SampleMeta.unavailable(),
    )


def _placeholder_value(telemetry: TelemetryCapability) -> int | float:
    if telemetry.raw_range is None:
        return 0
    return telemetry.raw_range.minimum


def _unavailable_force(capabilities: DeviceCapabilities) -> ForceSample | None:
    if capabilities.telemetry_channel(TelemetrySemantic.FORCE) is None:
        return None
    return ForceSample(raw=(), tactile=(), meta=SampleMeta.unavailable())


def make_initial_health(capabilities: DeviceCapabilities) -> HealthStatus:
    """Build complete zero counters and per-channel unavailable ages."""

    channels = tuple(
        ChannelMetrics(
            channel=telemetry.semantic.value,
            requested_hz=0.0,
            achieved_hz=0.0,
            missed_deadlines=0,
            timeouts=0,
            completed_reads=0,
            command_feedback_samples=0,
            feedback_samples=0,
            feedback_policy=None,
            data_age=DataAge(
                channel=telemetry.semantic.value,
                age_s=None,
                valid=False,
                stale=True,
                sequence=0,
                connection_generation=0,
                origin=DataOrigin.NONE,
            ),
        )
        for telemetry in capabilities.telemetry
    )
    return HealthStatus(
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
        command_families=tuple(
            CommandFamilyMetrics(family=family) for family in CommandFamily
        ),
        discrete_queue_depth=0,
        channels=channels,
        transaction_latency=LatencyMetrics(),
        transport=TransportMetrics(),
    )


__all__ = ["make_initial_health", "make_initial_snapshot"]
