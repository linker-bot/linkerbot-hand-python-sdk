"""Atomic O20 five-finger tactile transaction assembly."""

from __future__ import annotations

from ...api.enums import DataOrigin
from ...api.errors import ProtocolError, UnsupportedOperationError
from ...api.readings import TactileMatrix
from ...capabilities.types import DeviceCapabilities, TelemetrySemantic
from ..base import ProtocolTransaction, ResponseProgress, SamplePayload
from .codec import (
    REG_TACTILE_FIRST,
    REG_TACTILE_LAST,
    TACTILE_BACK_LENGTH,
    TACTILE_COLUMNS,
    TACTILE_FRONT_LENGTH,
    TACTILE_ROWS,
    build_read_message,
    matching_read_data,
    meaningful_prefix,
)

_FINGERS = ("thumb", "index", "middle", "ring", "pinky")
_REGISTERS = tuple(range(REG_TACTILE_FIRST, REG_TACTILE_LAST + 1))


def tactile_transaction(
    capabilities: DeviceCapabilities,
    *,
    device_id: int,
) -> ProtocolTransaction:
    """Build one ten-register transaction publishing all five fingers at once."""

    telemetry = capabilities.telemetry_channel(TelemetrySemantic.FORCE)
    if telemetry is None or not telemetry.device_queried:
        raise UnsupportedOperationError("O20 has no active tactile query")
    fragments: dict[int, bytes] = {}

    def consume(message: object) -> ResponseProgress:
        matched = matching_read_data(message, device_id=device_id)
        if matched is None or matched[0] not in _REGISTERS:
            return ResponseProgress(matched=False)
        register, data = matched
        expected = (
            TACTILE_FRONT_LENGTH
            if (register - REG_TACTILE_FIRST) % 2 == 0
            else TACTILE_BACK_LENGTH
        )
        fragment = meaningful_prefix(data, expected, "O20 tactile fragment")
        previous = fragments.get(register)
        if previous is not None and previous != fragment:
            raise ProtocolError("O20 tactile fragment conflicts with a duplicate")
        fragments[register] = fragment
        if len(fragments) != len(_REGISTERS):
            return ResponseProgress(matched=True)
        return ResponseProgress(
            matched=True,
            complete=True,
            result=SamplePayload(
                semantic=TelemetrySemantic.FORCE,
                tactile=_build_matrices(fragments),
                origin=DataOrigin.DEVICE_RESPONSE,
            ),
        )

    return ProtocolTransaction(
        key=("read", TelemetrySemantic.FORCE),
        correlation_key=("o20_tactile", device_id),
        request_messages=tuple(
            build_read_message(device_id, register) for register in _REGISTERS
        ),
        response_timeout_s=0.1,
        estimated_duration_s=telemetry.estimated_transaction_s,
        response_consumer=consume,
        late_response_guard_s=0.02,
    )


def _build_matrices(fragments: dict[int, bytes]) -> tuple[TactileMatrix, ...]:
    matrices: list[TactileMatrix] = []
    for index, name in enumerate(_FINGERS):
        front_register = REG_TACTILE_FIRST + index * 2
        front = fragments[front_register]
        online_value = front[0]
        if online_value not in {0, 1}:
            raise ProtocolError(f"O20 {name} tactile online flag must be zero or one")
        raw = front[1:] + fragments[front_register + 1]
        rows = tuple(
            tuple(raw[offset : offset + TACTILE_COLUMNS])
            for offset in range(0, len(raw), TACTILE_COLUMNS)
        )
        matrices.append(
            TactileMatrix(
                name=name,
                rows=TACTILE_ROWS,
                columns=TACTILE_COLUMNS,
                raw=rows,
                online=bool(online_value),
            )
        )
    return tuple(matrices)


__all__ = ["tactile_transaction"]
