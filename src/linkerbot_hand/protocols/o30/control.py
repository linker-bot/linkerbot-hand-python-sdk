"""ACK-gated O30 V25 command transactions."""

from __future__ import annotations

from dataclasses import dataclass

from ...api.enums import DataOrigin
from ...api.errors import ProtocolError, UnsupportedOperationError
from ...api.readings import RawNumber
from ...capabilities.types import (
    CommandSemantic,
    DeviceCapabilities,
    TelemetrySemantic,
)
from ..base import ProtocolTransaction, ResponseProgress, SamplePayload
from .codec import (
    MI_ACCELERATION,
    MI_CURRENT,
    MI_EFFORT,
    MI_MOTION_TIME,
    MI_POSITION,
    MI_SPEED,
    MI_STALL_HOLD_CURRENT,
    MI_STALL_THRESHOLD,
    MI_STALL_TIME,
    WIRE_SLOT_COUNT,
    decode_v25,
    encode_v25,
)
from .framing import HOPAddressing, build_write_message
from .response import HOPRangeAssembler


@dataclass(frozen=True, slots=True)
class _CommandObject:
    """One writable V25 object and the view requested in its response."""

    main_index: int
    # RTS selects returned actual state (0) or configured values (1); the
    # payload performs the write in either case. Only verified angle feedback
    # is published as current state; config acknowledgements are kept separate.
    rts: bool = False


_COMMAND_OBJECTS = {
    CommandSemantic.ANGLES: _CommandObject(MI_POSITION),
    CommandSemantic.SPEEDS: _CommandObject(MI_SPEED),
    CommandSemantic.ACCELERATIONS: _CommandObject(MI_ACCELERATION),
    CommandSemantic.EFFORT_LIMITS: _CommandObject(MI_EFFORT),
    CommandSemantic.CURRENT_LIMITS: _CommandObject(MI_CURRENT, rts=True),
    CommandSemantic.MOTION_TIMES: _CommandObject(MI_MOTION_TIME),
    CommandSemantic.STALL_DETECTION_TIMES: _CommandObject(MI_STALL_TIME),
    CommandSemantic.STALL_THRESHOLDS: _CommandObject(MI_STALL_THRESHOLD),
    CommandSemantic.STALL_HOLD_CURRENTS: _CommandObject(MI_STALL_HOLD_CURRENT),
}


def command_transaction(
    semantic: CommandSemantic,
    values: tuple[RawNumber, ...],
    capabilities: DeviceCapabilities,
    addressing: HOPAddressing,
) -> ProtocolTransaction:
    """Build one compact write whose correlated HOP response is the ACK."""

    try:
        command = _COMMAND_OBJECTS[semantic]
    except KeyError as error:
        raise UnsupportedOperationError(
            f"O30 has no protocol command for {semantic.value}"
        ) from error
    main_index = command.main_index
    raw = _validated_values(values, semantic, capabilities)
    payload = encode_v25(raw)
    assembler = HOPRangeAssembler(
        main_index,
        sub_index=0,
        length=WIRE_SLOT_COUNT,
        rts=command.rts,
        response_id=addressing.response_id,
    )

    def consume(message: object) -> ResponseProgress:
        matched, complete = assembler.consume(message)
        if complete is None:
            return ResponseProgress(matched=matched)
        feedback = None
        if semantic is CommandSemantic.ANGLES:
            # RTS=0 selects actual state, which can differ from the target
            # while the hand is moving. ACK is not a mechanical arrival test.
            raw = decode_v25(complete)
            feedback = SamplePayload(
                semantic=TelemetrySemantic.ANGLES,
                raw=raw,
                origin=DataOrigin.DEVICE_COMMAND_FEEDBACK,
                within_declared_range=all(
                    joint.raw_angle_range.contains(value)
                    for joint, value in zip(capabilities.joints, raw, strict=True)
                ),
            )
        return ResponseProgress(
            matched=True,
            complete=True,
            acknowledged=True,
            result=feedback,
        )

    return ProtocolTransaction(
        key=("command", semantic),
        correlation_key=("o30_hop", addressing.response_id),
        request_messages=(
            build_write_message(
                main_index,
                sub_index=0,
                payload=payload,
                rts=command.rts,
                request_id=addressing.request_id,
            ),
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
