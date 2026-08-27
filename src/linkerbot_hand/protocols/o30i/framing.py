"""Transport-neutral HOP framing for the O30i CAN FD endpoint."""

from __future__ import annotations

from dataclasses import dataclass

from linkerbot_transport import CANRouteKey
from linkerbot_transport.canfd import CANFDMessage

from ...api.errors import ProtocolError, ValidationError

REQUEST_ID = 0x001
RESPONSE_ID = 0x401
FRAME_TYPE = 0x04
ERROR_MAIN_INDEX = 0x4F
RTS_MASK = 0x80
MAIN_INDEX_MASK = 0x7F
MAX_FRAGMENT_LENGTH = 61


@dataclass(frozen=True, slots=True)
class HOPFrame:
    """One decoded HOP response with CAN FD padding removed."""

    main_index: int
    sub_index: int
    payload: bytes
    rts: bool


def response_route() -> CANRouteKey:
    """Return the single standard-ID route consumed by an O30i endpoint."""

    return CANRouteKey(RESPONSE_ID, False)


def build_read_message(
    main_index: int,
    *,
    sub_index: int,
    length: int,
    rts: bool = False,
) -> CANFDMessage:
    """Build one three-byte HOP object-range read."""

    _validate_object_range(main_index, sub_index, length)
    if not isinstance(rts, bool):
        raise ValidationError("rts must be bool")
    control = main_index | (RTS_MASK if rts else 0)
    return CANFDMessage(
        arbitration_id=REQUEST_ID,
        data=bytes((control, sub_index, length)),
        is_extended_id=False,
        frame_type=FRAME_TYPE,
    )


def build_write_message(
    main_index: int,
    *,
    sub_index: int,
    payload: bytes,
) -> CANFDMessage:
    """Build one single-frame HOP object-range write."""

    if not isinstance(payload, bytes) or not payload:
        raise ValidationError("HOP write payload must be non-empty bytes")
    if len(payload) > MAX_FRAGMENT_LENGTH:
        raise ValidationError(
            f"HOP write payload cannot exceed {MAX_FRAGMENT_LENGTH} bytes"
        )
    _validate_object_range(main_index, sub_index, len(payload))
    return CANFDMessage(
        arbitration_id=REQUEST_ID,
        data=bytes((main_index, sub_index, len(payload))) + payload,
        is_extended_id=False,
        frame_type=FRAME_TYPE,
    )


def matching_response_frame(message: object) -> HOPFrame | None:
    """Decode one exact endpoint response or ignore unrelated CAN traffic."""

    if (
        not isinstance(message, CANFDMessage)
        or message.is_extended_id
        or message.arbitration_id != RESPONSE_ID
    ):
        return None
    return decode_frame(message.data)


def decode_frame(data: bytes) -> HOPFrame:
    """Decode one response fragment using EDL instead of padded DLC length."""

    if not isinstance(data, bytes) or len(data) < 3:
        raise ProtocolError("O30i HOP response is shorter than its header")
    control, sub_index, effective_length = data[:3]
    required = 3 + effective_length
    if len(data) < required:
        raise ProtocolError(
            f"O30i HOP response declares {effective_length} payload bytes but "
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
    "MAIN_INDEX_MASK",
    "REQUEST_ID",
    "RESPONSE_ID",
    "RTS_MASK",
    "HOPFrame",
    "build_read_message",
    "build_write_message",
    "decode_frame",
    "matching_response_frame",
    "response_route",
]
