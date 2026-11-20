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
    MI_MOTION_TIME,
    MI_POSITION,
    MI_SPEED,
    MI_STALL_HOLD_CURRENT,
    MI_STALL_THRESHOLD,
    MI_STALL_TIME,
    MI_TORQUE,
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
    CommandSemantic.TORQUES_LIMITS: _CommandObject(MI_TORQUE),
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
    """Build one FD write or five response-gated classic CAN range writes."""

    try:
        command = _COMMAND_OBJECTS[semantic]
    except KeyError as error:
        raise UnsupportedOperationError(
            f"O30 has no protocol command for {semantic.value}"
        ) from error
    main_index = command.main_index
    raw = _validated_values(values, semantic, capabilities)
    payload = encode_v25(raw)
    fragment_size = WIRE_SLOT_COUNT if addressing.can_fd else 5
    feedback_bytes = bytearray(WIRE_SLOT_COUNT)

    def stage(start: int) -> ProtocolTransaction:
        end = min(start + fragment_size, WIRE_SLOT_COUNT)
        final = end == WIRE_SLOT_COUNT
        assembler = HOPRangeAssembler(
            main_index,
            sub_index=start,
            length=end - start,
            rts=command.rts,
            response_id=addressing.response_id,
        )

        def consume(message: object) -> ResponseProgress:
            matched, complete = assembler.consume(message)
            if complete is None:
                return ResponseProgress(matched=matched)
            feedback_bytes[start:end] = complete
            feedback = None
            if final and semantic is CommandSemantic.ANGLES:
                # Publish a complete vector only after all ranges acknowledge.
                # Segmented writes and the returned states are not simultaneous.
                returned = decode_v25(bytes(feedback_bytes))
                feedback = SamplePayload(
                    semantic=TelemetrySemantic.ANGLES,
                    raw=returned,
                    origin=DataOrigin.DEVICE_COMMAND_FEEDBACK,
                    within_declared_range=all(
                        joint.raw_angle_range.contains(value)
                        for joint, value in zip(
                            capabilities.joints, returned, strict=True
                        )
                    ),
                )
            return ResponseProgress(
                matched=True, complete=True, acknowledged=True, result=feedback
            )

        return ProtocolTransaction(
            key=("command", semantic),
            correlation_key=("hop", addressing.response_id),
            request_messages=(
                build_write_message(
                    main_index,
                    sub_index=start,
                    payload=payload[start:end],
                    rts=command.rts,
                    request_id=addressing.request_id,
                    brs=addressing.brs,
                    can_fd=addressing.can_fd,
                ),
            ),
            response_timeout_s=0.1,
            estimated_duration_s=0.003,
            response_consumer=consume,
            response_acknowledges_command=True,
            late_response_guard_s=0.01,
            next_transaction_factory=None if final else lambda: stage(end),
        )

    return stage(0)


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
