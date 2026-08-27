"""Pure L30 CAN FD identifier, payload, and response codecs."""

from __future__ import annotations

from ...api.errors import DeviceFaultError, ProtocolError
from ...api.readings import RawNumber

JOINT_COUNT = 17
PARENT_CONTROL = 0x1
PARENT_TACTILE = 0x2
PARENT_CONFIG = 0x3
PARENT_QUERY = 0x5

SUB_POSITION = 0x01
SUB_CURRENT_LIMIT = 0x02
SUB_CURRENT = 0x02
SUB_SPEED = 0x03
SUB_TEMPERATURE = 0x04
SUB_FAULT = 0x05
SUB_ENABLE = 0x07
SUB_DISABLE = 0x08
SUB_CONFIG_UNLOCK = 0x01
SUB_DEVICE_INFO = 0x02
SUB_PRODUCT_CODE = 0x03
SUB_CALIBRATE = 0x05

DEVICE_INFO_LENGTH = 18
TACTILE_ROWS = 12
TACTILE_COLUMNS = 6
TACTILE_LENGTH = TACTILE_ROWS * TACTILE_COLUMNS
TACTILE_FIRST_LENGTH = 61
TACTILE_SECOND_LENGTH = 11
CONFIG_UNLOCK_PASSWORD = bytes.fromhex("12 34 56 78 9A BC")

_STATUS_MESSAGES = {
    0x10: "DLC or data length mismatch",
    0x11: "parameter out of range or illegal value",
    0x12: "unsupported command",
    0x13: "invalid subcommand",
    0x14: "data format error",
    0x20: "permission denied",
    0x21: "zero point not calibrated",
    0x22: "motor not enabled",
    0x23: "operation not allowed in current state",
    0x24: "device not configured",
    0x30: "motor data read error",
    0x31: "motor data write error",
    0x32: "multi-frame transfer timeout",
    0x33: "multi-frame data incomplete",
    0x34: "periodic report configuration failed",
    0x40: "communication timeout",
    0x41: "communication lost",
    0xF0: "system busy",
}


def encode_i16_vector(values: tuple[RawNumber, ...]) -> bytes:
    """Encode 17 signed protocol values in J1 through J17 order."""

    return _encode_vector(values, signed=True, minimum=-0x8000, maximum=0x7FFF)


def encode_u16_vector(values: tuple[RawNumber, ...]) -> bytes:
    """Encode 17 unsigned protocol values in J1 through J17 order."""

    return _encode_vector(values, signed=False, minimum=0, maximum=0xFFFF)


def decode_i16_response(data: bytes) -> tuple[int, ...]:
    """Decode a status-checked, padded 17-value signed response."""

    payload = decode_response_payload(data, expected_length=JOINT_COUNT * 2)
    return tuple(
        int.from_bytes(payload[offset : offset + 2], "big", signed=True)
        for offset in range(0, len(payload), 2)
    )


def decode_u8_response(data: bytes) -> tuple[int, ...]:
    """Decode a status-checked, padded 17-value byte response."""

    return tuple(decode_response_payload(data, expected_length=JOINT_COUNT))


def decode_response_payload(data: bytes, *, expected_length: int) -> bytes:
    """Validate a single-frame response and return only declared useful bytes."""

    if not isinstance(data, bytes) or len(data) < 3:
        raise ProtocolError("L30 response is too short for its status header")
    if data[1] != 0:
        raise ProtocolError(
            f"L30 single-frame response has transaction 0x{data[1]:02X}"
        )
    _raise_for_status(data[2])
    if data[0] != expected_length:
        raise ProtocolError(
            f"L30 response length is {data[0]}; expected {expected_length}"
        )
    end = 3 + expected_length
    if len(data) < end:
        raise ProtocolError(
            f"L30 response declares {expected_length} bytes but only "
            f"{max(0, len(data) - 3)} are present"
        )
    return data[3:end]


def decode_ascii_response(data: bytes) -> tuple[str, bytes]:
    """Decode one variable-length, status-checked ASCII response."""

    if not isinstance(data, bytes) or not data:
        raise ProtocolError("L30 ASCII response has no length byte")
    raw = decode_response_payload(data, expected_length=data[0])
    try:
        text = raw.rstrip(b"\x00").decode("ascii")
    except UnicodeDecodeError as error:
        raise ProtocolError("L30 response payload is not ASCII") from error
    if not text:
        raise ProtocolError("L30 ASCII response is empty")
    return text, raw


def decode_ack(data: bytes) -> None:
    """Validate one explicit zero-length device ACK."""

    decode_response_payload(data, expected_length=0)


def decode_tactile_fragment(data: bytes) -> tuple[int, bytes]:
    """Return ``(sequence, bytes)`` from one of the two tactile frames."""

    if not isinstance(data, bytes) or len(data) < 3:
        raise ProtocolError("L30 tactile response is too short")
    total = data[1] >> 4
    sequence = data[1] & 0x0F
    if total != 1 or sequence not in {0, 1}:
        raise ProtocolError(
            f"L30 tactile transaction control 0x{data[1]:02X} is invalid"
        )
    expected_length = TACTILE_FIRST_LENGTH if sequence == 0 else TACTILE_SECOND_LENGTH
    if data[0] != expected_length:
        raise ProtocolError(
            f"L30 tactile fragment {sequence} length is {data[0]}; "
            f"expected {expected_length}"
        )
    _raise_for_status(data[2])
    end = 3 + expected_length
    if len(data) < end:
        raise ProtocolError(f"L30 tactile fragment {sequence} is truncated")
    return sequence, data[3:end]


def _encode_vector(
    values: tuple[RawNumber, ...],
    *,
    signed: bool,
    minimum: int,
    maximum: int,
) -> bytes:
    if len(values) != JOINT_COUNT or any(type(value) is not int for value in values):
        raise ProtocolError(f"L30 vector must contain {JOINT_COUNT} integers")
    normalized = tuple(int(value) for value in values)
    if any(value < minimum or value > maximum for value in normalized):
        raise ProtocolError(f"L30 vector values must be within {minimum}..{maximum}")
    return b"".join(value.to_bytes(2, "big", signed=signed) for value in normalized)


def _raise_for_status(status: int) -> None:
    if status == 0:
        return
    description = _STATUS_MESSAGES.get(status, "unknown status")
    raise DeviceFaultError(f"L30 status 0x{status:02X}: {description}")


__all__ = [
    "CONFIG_UNLOCK_PASSWORD",
    "DEVICE_INFO_LENGTH",
    "JOINT_COUNT",
    "PARENT_CONFIG",
    "PARENT_CONTROL",
    "PARENT_QUERY",
    "PARENT_TACTILE",
    "SUB_CALIBRATE",
    "SUB_CONFIG_UNLOCK",
    "SUB_CURRENT",
    "SUB_CURRENT_LIMIT",
    "SUB_DEVICE_INFO",
    "SUB_DISABLE",
    "SUB_ENABLE",
    "SUB_FAULT",
    "SUB_POSITION",
    "SUB_PRODUCT_CODE",
    "SUB_SPEED",
    "SUB_TEMPERATURE",
    "TACTILE_COLUMNS",
    "TACTILE_FIRST_LENGTH",
    "TACTILE_LENGTH",
    "TACTILE_ROWS",
    "TACTILE_SECOND_LENGTH",
    "decode_ack",
    "decode_ascii_response",
    "decode_i16_response",
    "decode_response_payload",
    "decode_tactile_fragment",
    "decode_u8_response",
    "encode_i16_vector",
    "encode_u16_vector",
]
