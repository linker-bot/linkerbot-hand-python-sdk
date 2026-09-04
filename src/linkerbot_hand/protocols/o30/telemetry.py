"""O30 actual-state and set-point V25 read transactions."""

from __future__ import annotations

from dataclasses import dataclass

from ...api.enums import DataOrigin
from ...api.errors import UnsupportedOperationError
from ...capabilities.types import DeviceCapabilities, TelemetrySemantic
from ..base import ProtocolTransaction, ResponseProgress, SamplePayload
from .codec import (
    MI_ACCELERATION,
    MI_CURRENT,
    MI_EFFORT,
    MI_FAULT,
    MI_MOTION_TIME,
    MI_POSITION,
    MI_SPEED,
    MI_STALL_HOLD_CURRENT,
    MI_STALL_THRESHOLD,
    MI_STALL_TIME,
    MI_TEMPERATURE,
    MI_VOLTAGE,
    WIRE_SLOT_COUNT,
    decode_v25,
)
from .framing import HOPAddressing, build_read_message
from .response import HOPRangeAssembler


@dataclass(frozen=True, slots=True)
class _ReadObject:
    main_index: int
    rts: bool = False
    origin: DataOrigin = DataOrigin.DEVICE_RESPONSE


_READ_OBJECTS = {
    TelemetrySemantic.ANGLES: _ReadObject(MI_POSITION),
    TelemetrySemantic.MEASURED_SPEEDS: _ReadObject(MI_SPEED),
    TelemetrySemantic.CONFIGURED_SPEEDS: _ReadObject(
        MI_SPEED,
        rts=True,
        origin=DataOrigin.DEVICE_CONFIG_RESPONSE,
    ),
    TelemetrySemantic.CONFIGURED_ACCELERATIONS: _ReadObject(
        MI_ACCELERATION,
        rts=True,
        origin=DataOrigin.DEVICE_CONFIG_RESPONSE,
    ),
    TelemetrySemantic.EFFORT_LIMITS: _ReadObject(
        MI_EFFORT,
        rts=True,
        origin=DataOrigin.DEVICE_CONFIG_RESPONSE,
    ),
    TelemetrySemantic.TORQUES: _ReadObject(MI_EFFORT),
    TelemetrySemantic.CURRENTS: _ReadObject(MI_CURRENT),
    TelemetrySemantic.CURRENT_LIMITS: _ReadObject(
        MI_CURRENT,
        rts=True,
        origin=DataOrigin.DEVICE_CONFIG_RESPONSE,
    ),
    TelemetrySemantic.TEMPERATURES: _ReadObject(MI_TEMPERATURE),
    TelemetrySemantic.FAULTS: _ReadObject(MI_FAULT),
    TelemetrySemantic.VOLTAGES: _ReadObject(MI_VOLTAGE),
    TelemetrySemantic.MOTION_TIMES: _ReadObject(
        MI_MOTION_TIME,
        rts=True,
        origin=DataOrigin.DEVICE_CONFIG_RESPONSE,
    ),
    TelemetrySemantic.STALL_DETECTION_TIMES: _ReadObject(
        MI_STALL_TIME,
        rts=True,
        origin=DataOrigin.DEVICE_CONFIG_RESPONSE,
    ),
    TelemetrySemantic.STALL_THRESHOLDS: _ReadObject(
        MI_STALL_THRESHOLD,
        rts=True,
        origin=DataOrigin.DEVICE_CONFIG_RESPONSE,
    ),
    TelemetrySemantic.STALL_HOLD_CURRENTS: _ReadObject(
        MI_STALL_HOLD_CURRENT,
        rts=True,
        origin=DataOrigin.DEVICE_CONFIG_RESPONSE,
    ),
}


def read_transaction(
    semantic: TelemetrySemantic,
    capabilities: DeviceCapabilities,
    addressing: HOPAddressing,
) -> ProtocolTransaction:
    """Build one compact 25-slot query returning twenty installed channels."""

    telemetry = capabilities.telemetry_channel(semantic)
    if telemetry is None or not telemetry.device_queried:
        raise UnsupportedOperationError(f"O30 has no active query for {semantic.value}")
    try:
        profile = _READ_OBJECTS[semantic]
    except KeyError as error:
        raise UnsupportedOperationError(
            f"O30 has no protocol read for {semantic.value}"
        ) from error
    assembler = HOPRangeAssembler(
        profile.main_index,
        sub_index=0,
        length=WIRE_SLOT_COUNT,
        rts=profile.rts,
        response_id=addressing.response_id,
    )

    def consume(message: object) -> ResponseProgress:
        matched, complete = assembler.consume(message)
        if complete is None:
            return ResponseProgress(matched=matched)
        raw = decode_v25(complete)
        return ResponseProgress(
            matched=True,
            complete=True,
            result=SamplePayload(
                semantic=semantic,
                raw=raw,
                origin=profile.origin,
                within_declared_range=_within_declared_range(
                    raw,
                    semantic,
                    capabilities,
                ),
            ),
        )

    return ProtocolTransaction(
        key=("read", semantic),
        correlation_key=("o30_hop", addressing.response_id),
        request_messages=(
            build_read_message(
                profile.main_index,
                sub_index=0,
                length=WIRE_SLOT_COUNT,
                rts=profile.rts,
                request_id=addressing.request_id,
            ),
        ),
        response_timeout_s=0.1,
        estimated_duration_s=telemetry.estimated_transaction_s,
        response_consumer=consume,
        late_response_guard_s=0.01,
    )


def _within_declared_range(
    raw: tuple[int, ...],
    semantic: TelemetrySemantic,
    capabilities: DeviceCapabilities,
) -> bool:
    if semantic is TelemetrySemantic.ANGLES:
        return all(
            joint.raw_angle_range.contains(value)
            for joint, value in zip(capabilities.joints, raw, strict=True)
        )
    telemetry = capabilities.telemetry_channel(semantic)
    return (
        telemetry is None
        or telemetry.raw_range is None
        or all(telemetry.raw_range.contains(value) for value in raw)
    )


__all__ = ["read_transaction"]
