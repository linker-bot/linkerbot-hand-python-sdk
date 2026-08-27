"""L30 active state-query transactions and declared-range checks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ...api.enums import DataOrigin
from ...api.errors import UnsupportedOperationError
from ...capabilities.types import DeviceCapabilities, TelemetrySemantic
from ..base import ProtocolTransaction, ResponseProgress, SamplePayload
from .codec import (
    PARENT_QUERY,
    SUB_CURRENT,
    SUB_FAULT,
    SUB_POSITION,
    SUB_SPEED,
    SUB_TEMPERATURE,
    decode_i16_response,
    decode_u8_response,
)
from .framing import build_request, matching_response_data
from .tactile import tactile_transaction

_Decoder = Callable[[bytes], tuple[int, ...]]


@dataclass(frozen=True, slots=True)
class _ReadObject:
    subcommand: int
    decoder: _Decoder


_READ_OBJECTS = {
    TelemetrySemantic.ANGLES: _ReadObject(SUB_POSITION, decode_i16_response),
    TelemetrySemantic.MEASURED_SPEEDS: _ReadObject(
        SUB_SPEED,
        decode_i16_response,
    ),
    TelemetrySemantic.CURRENTS: _ReadObject(SUB_CURRENT, decode_i16_response),
    TelemetrySemantic.TEMPERATURES: _ReadObject(
        SUB_TEMPERATURE,
        decode_u8_response,
    ),
    TelemetrySemantic.FAULTS: _ReadObject(SUB_FAULT, decode_u8_response),
}


def read_transaction(
    semantic: TelemetrySemantic,
    capabilities: DeviceCapabilities,
    *,
    node_id: int,
    host_id: int,
) -> ProtocolTransaction:
    """Build one fresh scalar-vector query or atomic five-finger tactile read."""

    telemetry = capabilities.telemetry_channel(semantic)
    if telemetry is None or not telemetry.device_queried:
        raise UnsupportedOperationError(f"L30 has no active query for {semantic.value}")
    if semantic is TelemetrySemantic.FORCE:
        return tactile_transaction(
            capabilities,
            node_id=node_id,
            host_id=host_id,
        )
    try:
        profile = _READ_OBJECTS[semantic]
    except KeyError as error:
        raise UnsupportedOperationError(
            f"L30 has no protocol read for {semantic.value}"
        ) from error

    def consume(message: object) -> ResponseProgress:
        data = matching_response_data(
            message,
            parent=PARENT_QUERY,
            subcommand=profile.subcommand,
            write=False,
            node_id=node_id,
            host_id=host_id,
        )
        if data is None:
            return ResponseProgress(matched=False)
        raw = profile.decoder(data)
        return ResponseProgress(
            matched=True,
            complete=True,
            result=SamplePayload(
                semantic=semantic,
                raw=raw,
                origin=DataOrigin.DEVICE_RESPONSE,
                within_declared_range=_within_declared_range(
                    raw,
                    semantic,
                    capabilities,
                ),
            ),
        )

    return ProtocolTransaction(
        key=("read", semantic),
        correlation_key=("l30_read", node_id, PARENT_QUERY, profile.subcommand),
        request_messages=(
            build_request(
                parent=PARENT_QUERY,
                subcommand=profile.subcommand,
                write=False,
                node_id=node_id,
                host_id=host_id,
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
