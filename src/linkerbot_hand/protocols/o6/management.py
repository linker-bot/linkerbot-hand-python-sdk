"""O6 response-gated configuration and parameter-save transactions."""

from __future__ import annotations

from ...api.enums import DataOrigin
from ...api.errors import ProtocolError, UnsupportedOperationError
from ...api.readings import RawNumber
from ...capabilities.types import (
    CommandSemantic,
    DeviceCapabilities,
    TelemetrySemantic,
)
from ..base import ProtocolTransaction, ResponseProgress, SamplePayload
from ..common.classic_can import (
    build_classic_message,
    classic_route,
    decode_u8_vector,
    matching_data,
)

_SAVE_PARAMETERS_OPCODE = 0xCF
_SAVE_PARAMETERS_PAYLOAD = bytes((_SAVE_PARAMETERS_OPCODE,)) * 8


def configuration_transaction(
    semantic: CommandSemantic,
    telemetry_semantic: TelemetrySemantic,
    opcode: int,
    values: tuple[RawNumber, ...],
    capabilities: DeviceCapabilities,
) -> ProtocolTransaction:
    """Write one O6 raw vector and require the documented exact response."""

    command = capabilities.command(semantic)
    if command is None:
        raise UnsupportedOperationError(
            f"{capabilities.model} does not support {semantic.value}"
        )
    raw = _validated_u8(values, len(command.channels), semantic.value)
    if any(
        not command.range_for(index).contains(value) for index, value in enumerate(raw)
    ):
        raise ProtocolError(f"{semantic.value} values exceed declared ranges")
    route = classic_route(capabilities.side)

    def consume(message: object) -> ResponseProgress:
        echoed = decode_u8_vector(
            message,
            route=route,
            opcode=opcode,
            count=len(command.channels),
        )
        if echoed is None:
            return ResponseProgress(matched=False)
        if echoed != raw:
            raise ProtocolError(f"O6 {semantic.value} response did not echo values")
        return ResponseProgress(
            matched=True,
            complete=True,
            result=SamplePayload(
                semantic=telemetry_semantic,
                raw=echoed,
                origin=DataOrigin.DEVICE_CONFIG_RESPONSE,
            ),
            acknowledged=True,
        )

    return ProtocolTransaction(
        key=("command", semantic),
        correlation_key=("opcode", route, opcode),
        request_messages=(build_classic_message(route, bytes((opcode, *raw))),),
        response_timeout_s=0.1,
        estimated_duration_s=0.005,
        response_consumer=consume,
        response_acknowledges_command=True,
        late_response_guard_s=0.005,
    )


def save_parameters_transaction(
    capabilities: DeviceCapabilities,
) -> ProtocolTransaction:
    """Persist O6 configuration only after the explicit ``CF 01`` response."""

    if not capabilities.operations.save_parameters:
        raise UnsupportedOperationError(
            f"{capabilities.model} does not support save_parameters"
        )
    route = classic_route(capabilities.side)

    def consume(message: object) -> ResponseProgress:
        data = matching_data(message, route)
        if data is None or not data or data[0] != _SAVE_PARAMETERS_OPCODE:
            return ResponseProgress(matched=False)
        if data != b"\xcf\x01":
            raise ProtocolError("O6 save-parameters response was not CF 01")
        return ResponseProgress(
            matched=True,
            complete=True,
            acknowledged=True,
        )

    return ProtocolTransaction(
        key=("operation", "save_parameters"),
        correlation_key=("opcode", route, _SAVE_PARAMETERS_OPCODE),
        request_messages=(build_classic_message(route, _SAVE_PARAMETERS_PAYLOAD),),
        response_timeout_s=0.2,
        estimated_duration_s=0.1,
        response_consumer=consume,
        response_acknowledges_command=True,
        late_response_guard_s=0.02,
    )


def _validated_u8(
    values: tuple[RawNumber, ...],
    count: int,
    label: str,
) -> tuple[int, ...]:
    if len(values) != count or any(type(value) is not int for value in values):
        raise ProtocolError(f"{label} must contain {count} integer protocol bytes")
    raw = tuple(int(value) for value in values)
    if any(not 0 <= value <= 0xFF for value in raw):
        raise ProtocolError(f"{label} protocol bytes must be in 0..255")
    return raw


__all__ = ["configuration_transaction", "save_parameters_transaction"]
