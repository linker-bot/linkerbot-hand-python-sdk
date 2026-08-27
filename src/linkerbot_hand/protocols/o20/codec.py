"""Pure O20 CAN FD register and vector codecs."""

from __future__ import annotations

from linkerbot_transport import CANRouteKey
from linkerbot_transport.canfd import CANFDMessage

from ...api.enums import HandSide
from ...api.errors import ProtocolError, ValidationError
from ...api.readings import RawNumber

JOINT_COUNT = 16
WIRE_SLOT_COUNT = 17

DEVICE_ID_RIGHT = 0x01
DEVICE_ID_LEFT = 0x02

REG_DEVICE_INFO = 0x00
REG_FAULTS = 0x02
REG_ANGLES = 0x03
REG_MEASURED_SPEEDS = 0x04
REG_TARGET_ANGLES = 0x06
REG_CONFIGURED_SPEEDS = 0x07
REG_EFFORT_LIMITS = 0x08
REG_TACTILE_FIRST = 0x09
REG_TACTILE_LAST = 0x12
REG_TEMPERATURES = 0x13
REG_CURRENTS = 0x15

DEVICE_INFO_LENGTH = 62
TACTILE_FRONT_LENGTH = 64
TACTILE_BACK_LENGTH = 9
TACTILE_ROWS = 12
TACTILE_COLUMNS = 6

_REGISTER_MAX = 0xFF
_RESERVED_ID_MASK = 0xFFF
_I16_LENGTH = WIRE_SLOT_COUNT * 2
_U16_LENGTH = WIRE_SLOT_COUNT * 2
_U8_LENGTH = WIRE_SLOT_COUNT


def device_id_for_side(side: HandSide) -> int:
    """Return the fixed O20 controller ID for a configured hand side.

    Args:
        side: Configured physical hand side.

    Returns:
        The O20 controller ID used in extended arbitration IDs.

    Raises:
        ValidationError: If ``side`` is not left or right.
    """

    if side is HandSide.LEFT:
        return DEVICE_ID_LEFT
    if side is HandSide.RIGHT:
        return DEVICE_ID_RIGHT
    raise ValidationError("O20 requires a left or right side")


def build_can_id(*, device_id: int, register: int, write: bool) -> int:
    """Pack one O20 29-bit arbitration ID with reserved bits cleared."""

    _require_int_range(device_id, "device_id", 0, 0xFF)
    _require_int_range(register, "register", 0, _REGISTER_MAX)
    if not isinstance(write, bool):
        raise ValidationError("write must be bool")
    return (device_id << 21) | (register << 13) | (int(write) << 12)


def parse_can_id(arbitration_id: int) -> tuple[int, int, bool]:
    """Return ``(device_id, register, write)`` for one valid O20 ID."""

    _require_int_range(arbitration_id, "arbitration_id", 0, 0x1FFFFFFF)
    if arbitration_id & _RESERVED_ID_MASK:
        raise ProtocolError("O20 arbitration ID reserved bits must be zero")
    return (
        (arbitration_id >> 21) & 0xFF,
        (arbitration_id >> 13) & 0xFF,
        bool((arbitration_id >> 12) & 0x01),
    )


def read_route(device_id: int, register: int) -> CANRouteKey:
    """Return the exact route used by a register read response."""

    return CANRouteKey(
        arbitration_id=build_can_id(
            device_id=device_id,
            register=register,
            write=False,
        ),
        is_extended_id=True,
    )


def build_read_message(device_id: int, register: int) -> CANFDMessage:
    """Build one empty O20 register read request."""

    return CANFDMessage(
        arbitration_id=read_route(device_id, register).arbitration_id,
        data=b"",
        dlc=0,
        is_extended_id=True,
    )


def build_write_message(
    device_id: int,
    register: int,
    payload: bytes,
    *,
    dlc: int,
) -> CANFDMessage:
    """Build one O20 register write request with an explicit wire DLC."""

    if not isinstance(payload, bytes):
        raise ValidationError("payload must be bytes")
    return CANFDMessage(
        arbitration_id=build_can_id(
            device_id=device_id,
            register=register,
            write=True,
        ),
        data=payload,
        dlc=dlc,
        is_extended_id=True,
    )


def matching_read_data(
    message: object,
    *,
    device_id: int,
    register: int | None = None,
) -> tuple[int, bytes] | None:
    """Extract one exact O20 read response, ignoring unrelated frames."""

    if not isinstance(message, CANFDMessage) or not message.is_extended_id:
        return None
    try:
        actual_device, actual_register, write = parse_can_id(message.arbitration_id)
    except (ProtocolError, ValidationError):
        return None
    if actual_device != device_id or write:
        return None
    if register is not None and actual_register != register:
        return None
    return actual_register, message.data


def encode_i16_vector(values: tuple[RawNumber, ...]) -> bytes:
    """Encode 16 signed values and append a zeroed reserved wire slot."""

    normalized = _validated_int_vector(values, minimum=-0x8000, maximum=0x7FFF)
    return _encode_words(normalized, signed=True)


def encode_u16_vector(values: tuple[RawNumber, ...]) -> bytes:
    """Encode 16 unsigned values and append a zeroed reserved wire slot."""

    normalized = _validated_int_vector(values, minimum=0, maximum=0xFFFF)
    return _encode_words(normalized, signed=False)


def encode_zero_faults() -> bytes:
    """Encode the documented 17-slot clear-fault payload."""

    return bytes(_U8_LENGTH)


def decode_i16_vector(data: bytes) -> tuple[int, ...]:
    """Decode 16 signed slots and ignore the reserved slot and DLC padding."""

    _require_minimum_length(data, _I16_LENGTH, "i16 vector")
    return tuple(
        int.from_bytes(data[offset : offset + 2], "little", signed=True)
        for offset in range(0, JOINT_COUNT * 2, 2)
    )


def decode_u16_vector(data: bytes) -> tuple[int, ...]:
    """Decode 16 unsigned slots and ignore the reserved slot and padding."""

    _require_minimum_length(data, _U16_LENGTH, "u16 vector")
    return tuple(
        int.from_bytes(data[offset : offset + 2], "little", signed=False)
        for offset in range(0, JOINT_COUNT * 2, 2)
    )


def decode_u8_vector(data: bytes) -> tuple[int, ...]:
    """Decode 16 byte slots and ignore the reserved slot and DLC padding."""

    _require_minimum_length(data, _U8_LENGTH, "u8 vector")
    return tuple(data[:JOINT_COUNT])


def meaningful_prefix(data: bytes, length: int, label: str) -> bytes:
    """Return a protocol-length prefix while accepting CAN FD DLC padding."""

    _require_minimum_length(data, length, label)
    return data[:length]


def _validated_int_vector(
    values: tuple[RawNumber, ...],
    *,
    minimum: int,
    maximum: int,
) -> tuple[int, ...]:
    if len(values) != JOINT_COUNT or any(type(value) is not int for value in values):
        raise ProtocolError(f"O20 vector must contain {JOINT_COUNT} integers")
    normalized = tuple(int(value) for value in values)
    if any(value < minimum or value > maximum for value in normalized):
        raise ProtocolError(f"O20 vector values must be within {minimum}..{maximum}")
    return normalized


def _encode_words(values: tuple[int, ...], *, signed: bool) -> bytes:
    payload = bytearray(_I16_LENGTH)
    for index, value in enumerate(values):
        payload[index * 2 : index * 2 + 2] = value.to_bytes(
            2,
            "little",
            signed=signed,
        )
    return bytes(payload)


def _require_minimum_length(data: object, length: int, label: str) -> None:
    if not isinstance(data, bytes):
        raise ProtocolError(f"{label} payload must be bytes")
    if len(data) < length:
        raise ProtocolError(
            f"{label} response must contain at least {length} bytes; got {len(data)}"
        )


def _require_int_range(value: object, name: str, minimum: int, maximum: int) -> None:
    if type(value) is not int:
        raise ValidationError(f"{name} must be int")
    if value < minimum or value > maximum:
        raise ValidationError(f"{name} must be within {minimum}..{maximum}")


__all__ = [
    "DEVICE_INFO_LENGTH",
    "JOINT_COUNT",
    "REG_ANGLES",
    "REG_CONFIGURED_SPEEDS",
    "REG_CURRENTS",
    "REG_DEVICE_INFO",
    "REG_EFFORT_LIMITS",
    "REG_FAULTS",
    "REG_MEASURED_SPEEDS",
    "REG_TACTILE_FIRST",
    "REG_TACTILE_LAST",
    "REG_TARGET_ANGLES",
    "REG_TEMPERATURES",
    "TACTILE_BACK_LENGTH",
    "TACTILE_COLUMNS",
    "TACTILE_FRONT_LENGTH",
    "TACTILE_ROWS",
    "build_can_id",
    "build_read_message",
    "build_write_message",
    "decode_i16_vector",
    "decode_u8_vector",
    "decode_u16_vector",
    "device_id_for_side",
    "encode_i16_vector",
    "encode_u16_vector",
    "encode_zero_faults",
    "matching_read_data",
    "meaningful_prefix",
    "parse_can_id",
    "read_route",
]
