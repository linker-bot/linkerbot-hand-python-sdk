"""ACK-gated O30 V25 command transactions."""

from __future__ import annotations

from ...api.errors import ProtocolError, UnsupportedOperationError
from ...api.readings import RawNumber
from ...capabilities.types import (
    CommandSemantic,
    DeviceCapabilities,
)
from ..base import ProtocolTransaction, ResponseProgress
from .codec import (
    MI_ACCELERATION,
    MI_EFFORT,
    MI_MOTION_TIME,
    MI_POSITION,
    MI_SPEED,
    MI_STALL_HOLD_CURRENT,
    MI_STALL_THRESHOLD,
    MI_STALL_TIME,
    WIRE_SLOT_COUNT,
    encode_v25,
)
from .framing import RESPONSE_ID, build_write_message
from .response import HOPRangeAssembler

_COMMAND_OBJECTS = {
    CommandSemantic.ANGLES: MI_POSITION,
    CommandSemantic.SPEEDS: MI_SPEED,
    CommandSemantic.ACCELERATIONS: MI_ACCELERATION,
    CommandSemantic.EFFORT_LIMITS: MI_EFFORT,
    CommandSemantic.MOTION_TIMES: MI_MOTION_TIME,
    CommandSemantic.STALL_DETECTION_TIMES: MI_STALL_TIME,
    CommandSemantic.STALL_THRESHOLDS: MI_STALL_THRESHOLD,
    CommandSemantic.STALL_HOLD_CURRENTS: MI_STALL_HOLD_CURRENT,
}


def command_transaction(
    semantic: CommandSemantic,
    values: tuple[RawNumber, ...],
    capabilities: DeviceCapabilities,
) -> ProtocolTransaction:
    """Build one compact write whose correlated HOP response is the ACK."""

    try:
        main_index = _COMMAND_OBJECTS[semantic]
    except KeyError as error:
        raise UnsupportedOperationError(
            f"O30 has no protocol command for {semantic.value}"
        ) from error
    raw = _validated_values(values, semantic, capabilities)
    payload = encode_v25(raw)
    assembler = HOPRangeAssembler(
        main_index,
        sub_index=0,
        length=WIRE_SLOT_COUNT,
        rts=False,
    )

    def consume(message: object) -> ResponseProgress:
        matched, complete = assembler.consume(message)
        return ResponseProgress(
            matched=matched,
            complete=complete is not None,
            acknowledged=complete is not None,
        )

    return ProtocolTransaction(
        key=("command", semantic),
        correlation_key=("o30_hop", RESPONSE_ID),
        request_messages=(
            build_write_message(main_index, sub_index=0, payload=payload),
        ),
        response_timeout_s=0.1,
        estimated_duration_s=0.003,
        response_consumer=consume,
        response_acknowledges_command=True,
        late_response_guard_s=0.01,
    )


def _validated_values(
    values: tuple[RawNumber, ...],
    semantic: CommandSemantic,
    capabilities: DeviceCapabilities,
) -> tuple[RawNumber, ...]:
    command = capabilities.command(semantic)
    if command is None:
        raise UnsupportedOperationError(f"O30 does not support {semantic.value}")
    if len(values) != len(command.channels) or any(
        type(value) is not int for value in values
    ):
        raise ProtocolError(
            f"O30 {semantic.value} must contain {len(command.channels)} integers"
        )
    if any(
        not command.range_for(index).contains(value)
        for index, value in enumerate(values)
    ):
        raise ProtocolError(f"O30 {semantic.value} exceed declared ranges")
    return values


__all__ = ["command_transaction"]
