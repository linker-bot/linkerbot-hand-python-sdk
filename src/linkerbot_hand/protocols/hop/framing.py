"""Shared HOP framing for standard-ID classic CAN and CAN FD endpoints."""

from __future__ import annotations

from dataclasses import dataclass

import can

from linkerbot_transport import CANRouteKey
from linkerbot_transport.canfd import CANFDMessage

from ...api.enums import HandSide
from ...api.errors import ProtocolError, ValidationError

# O30 defaults to a standard-frame CAN ID pair per side. The response
# ID is the request ID with the top standard-frame bit (0x400) set.
RIGHT_REQUEST_ID = 0x001
RIGHT_RESPONSE_ID = 0x401
LEFT_REQUEST_ID = 0x002
LEFT_RESPONSE_ID = 0x402
# Backwards-friendly aliases for the right-hand pair (used by discovery/tests).
REQUEST_ID = RIGHT_REQUEST_ID
RESPONSE_ID = RIGHT_RESPONSE_ID

FRAME_TYPE = 0x04
ERROR_MAIN_INDEX = 0x4F
RTS_MASK = 0x80
MAIN_INDEX_MASK = 0x7F
MAX_FRAGMENT_LENGTH = 61
_STANDARD_ID_MAX = 0x7FF


@dataclass(frozen=True, slots=True)
class HOPAddressing:
    """One HOP endpoint's request/response standard-frame CAN ID pair."""

    request_id: int
    response_id: int
    brs: bool = False
    can_fd: bool = True

    def __post_init__(self) -> None:
        if type(self.can_fd) is not bool:
            raise ValidationError("HOP can_fd must be bool")
        if not self.can_fd and self.brs:
            raise ValidationError("classic HOP CAN cannot enable BRS")
        if not isinstance(self.brs, bool):
            raise ValidationError("HOP brs must be bool")
        for value, name in (
            (self.request_id, "request_id"),
            (self.response_id, "response_id"),
        ):
            if type(value) is not int or not 0 <= value <= _STANDARD_ID_MAX:
                raise ValidationError(f"HOP {name} must be a standard CAN ID")
        if self.request_id > 0x3FE:
            raise ValidationError("HOP unicast request_id must be within 0x000..0x3FE")
        if self.response_id != self.request_id | 0x400:
            raise ValidationError("HOP response_id must be request_id | 0x400")


RIGHT_ADDRESSING = HOPAddressing(RIGHT_REQUEST_ID, RIGHT_RESPONSE_ID)
LEFT_ADDRESSING = HOPAddressing(LEFT_REQUEST_ID, LEFT_RESPONSE_ID)


def addressing_for_side(side: HandSide) -> HOPAddressing:
    """Return the default CAN ID pair for ``side``; IDs may be configured."""

    if side is HandSide.RIGHT:
        return RIGHT_ADDRESSING
    if side is HandSide.LEFT:
        return LEFT_ADDRESSING
    raise ValidationError("O30 addressing requires a left or right side")


@dataclass(frozen=True, slots=True)
class HOPFrame:
    """One decoded HOP response with CAN FD padding removed."""

    main_index: int
    sub_index: int
    payload: bytes
    rts: bool


def response_route(response_id: int) -> CANRouteKey:
    """Return the single standard-ID route consumed by an O30 endpoint."""

    return CANRouteKey(response_id, False)


def build_read_message(
    main_index: int,
    *,
    sub_index: int,
    length: int,
    request_id: int,
    rts: bool = False,
    brs: bool = False,
    can_fd: bool = True,
) -> CANFDMessage | can.Message:
    """Build one three-byte HOP object-range read."""

    _validate_object_range(main_index, sub_index, length)
    if not isinstance(brs, bool):
        raise ValidationError("brs must be bool")
    if not isinstance(rts, bool):
        raise ValidationError("rts must be bool")
    control = main_index | (RTS_MASK if rts else 0)
    if type(can_fd) is not bool or (not can_fd and brs):
        raise ValidationError("classic HOP CAN requires can_fd=False and BRS off")
    if not can_fd:
        return can.Message(
            arbitration_id=request_id,
            data=bytes((control, sub_index, length)),
            is_extended_id=False,
            check=True,
        )
    return CANFDMessage(
        arbitration_id=request_id,
        data=bytes((control, sub_index, length)),
        is_extended_id=False,
        frame_type=FRAME_TYPE | (0x08 if brs else 0),
    )


def build_write_message(
    main_index: int,
    *,
    sub_index: int,
    payload: bytes,
    request_id: int,
    rts: bool = False,
    brs: bool = False,
    can_fd: bool = True,
) -> CANFDMessage | can.Message:
    """Build one single-frame HOP object-range write."""

    if not isinstance(payload, bytes) or not payload:
        raise ValidationError("HOP write payload must be non-empty bytes")
    if len(payload) > MAX_FRAGMENT_LENGTH:
        raise ValidationError(
            f"HOP write payload cannot exceed {MAX_FRAGMENT_LENGTH} bytes"
        )
    if not isinstance(brs, bool):
        raise ValidationError("brs must be bool")
    if not isinstance(rts, bool):
        raise ValidationError("rts must be bool")
    _validate_object_range(main_index, sub_index, len(payload))
    control = main_index | (RTS_MASK if rts else 0)
    if type(can_fd) is not bool or (not can_fd and brs):
        raise ValidationError("classic HOP CAN requires can_fd=False and BRS off")
    if not can_fd:
        if len(payload) > 5:
            raise ValidationError("classic HOP CAN write payload cannot exceed 5 bytes")
        return can.Message(
            arbitration_id=request_id,
            data=bytes((control, sub_index, len(payload))) + payload,
            is_extended_id=False,
            check=True,
        )
    return CANFDMessage(
        arbitration_id=request_id,
        data=bytes((control, sub_index, len(payload))) + payload,
        is_extended_id=False,
        frame_type=FRAME_TYPE | (0x08 if brs else 0),
    )


def matching_response_frame(message: object, response_id: int) -> HOPFrame | None:
    """Decode one exact endpoint response or ignore unrelated CAN traffic."""

    if (
        not isinstance(message, (CANFDMessage, can.Message))
        or message.is_extended_id
        or message.arbitration_id != response_id
    ):
        return None
    if isinstance(message, can.Message):
        if message.is_error_frame or message.is_remote_frame:
            return None
        if message.is_fd or message.bitrate_switch or len(message.data) > 8:
            return None
    return decode_frame(bytes(message.data))


def decode_frame(data: bytes) -> HOPFrame:
    """Decode one response fragment using EDL instead of padded DLC length."""

    if not isinstance(data, bytes) or len(data) < 3:
        raise ProtocolError("HOP response is shorter than its header")
    control, sub_index, effective_length = data[:3]
    required = 3 + effective_length
    if len(data) < required:
        raise ProtocolError(
            f"HOP response declares {effective_length} payload bytes but "
            f"contains {max(0, len(data) - 3)}"
        )
    return HOPFrame(
        main_index=control & MAIN_INDEX_MASK,
        sub_index=sub_index,
        payload=data[3:required],
        rts=bool(control & RTS_MASK),
    )


def _validate_object_range(main_index: int, sub_index: int, length: int) -> None:
    for value, name, minimum, maximum in (
        (main_index, "main_index", 0, MAIN_INDEX_MASK),
        (sub_index, "sub_index", 0, 0xFF),
        (length, "length", 1, 0xFF),
    ):
        if type(value) is not int:
            raise ValidationError(f"{name} must be int")
        if value < minimum or value > maximum:
            raise ValidationError(f"{name} must be within {minimum}..{maximum}")
    if sub_index + length > 0x100:
        raise ValidationError("sub_index + length cannot exceed 256")


__all__ = [
    "ERROR_MAIN_INDEX",
    "FRAME_TYPE",
    "LEFT_ADDRESSING",
    "LEFT_REQUEST_ID",
    "LEFT_RESPONSE_ID",
    "MAIN_INDEX_MASK",
    "MAX_FRAGMENT_LENGTH",
    "REQUEST_ID",
    "RESPONSE_ID",
    "RIGHT_ADDRESSING",
    "RIGHT_REQUEST_ID",
    "RIGHT_RESPONSE_ID",
    "RTS_MASK",
    "HOPAddressing",
    "HOPFrame",
    "addressing_for_side",
    "build_read_message",
    "build_write_message",
    "decode_frame",
    "matching_response_frame",
    "response_route",
]
