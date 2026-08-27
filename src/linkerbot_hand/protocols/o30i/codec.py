"""O30i object indices and public-20-to-V25 value mapping."""

from __future__ import annotations

from ...api.errors import ProtocolError
from ...api.readings import RawNumber

PUBLIC_JOINT_COUNT = 20
WIRE_SLOT_COUNT = 25
JOINT_SLOT_INDICES = (
    0,
    5,
    6,
    7,
    8,
    9,
    10,
    11,
    12,
    13,
    14,
    16,
    17,
    18,
    19,
    20,
    21,
    22,
    23,
    24,
)

MI_POSITION = 0x01
MI_SPEED = 0x02
MI_ACCELERATION = 0x03
MI_CURRENT = 0x04
MI_VOLTAGE = 0x05
MI_EFFORT = 0x06
MI_TEMPERATURE = 0x07
MI_MOTION_TIME = 0x08
MI_STALL_TIME = 0x09
MI_STALL_THRESHOLD = 0x0A
MI_STALL_HOLD_CURRENT = 0x0B
MI_FAULT = 0x0D
MI_SENSOR_INFO = 0x31
MI_PRODUCT_INFO = 0x41


def encode_v25(values: tuple[RawNumber, ...]) -> bytes:
    """Place twenty public values into one 25-byte object with zero gaps."""

    if len(values) != PUBLIC_JOINT_COUNT or any(
        type(value) is not int for value in values
    ):
        raise ProtocolError(
            f"O30i vector must contain {PUBLIC_JOINT_COUNT} integer values"
        )
    normalized = tuple(int(value) for value in values)
    if any(value < 0 or value > 0xFF for value in normalized):
        raise ProtocolError("O30i vector values must be within 0..255")
    slots = bytearray(WIRE_SLOT_COUNT)
    for public_index, wire_slot in enumerate(JOINT_SLOT_INDICES):
        slots[wire_slot] = normalized[public_index]
    return bytes(slots)


def decode_v25(data: bytes) -> tuple[int, ...]:
    """Project one exact V25 object onto the twenty installed joints."""

    if not isinstance(data, bytes) or len(data) != WIRE_SLOT_COUNT:
        raise ProtocolError(f"O30i V25 response must contain {WIRE_SLOT_COUNT} bytes")
    return tuple(data[wire_slot] for wire_slot in JOINT_SLOT_INDICES)


__all__ = [
    "JOINT_SLOT_INDICES",
    "MI_ACCELERATION",
    "MI_CURRENT",
    "MI_EFFORT",
    "MI_FAULT",
    "MI_MOTION_TIME",
    "MI_POSITION",
    "MI_PRODUCT_INFO",
    "MI_SENSOR_INFO",
    "MI_SPEED",
    "MI_STALL_HOLD_CURRENT",
    "MI_STALL_THRESHOLD",
    "MI_STALL_TIME",
    "MI_TEMPERATURE",
    "MI_VOLTAGE",
    "PUBLIC_JOINT_COUNT",
    "WIRE_SLOT_COUNT",
    "decode_v25",
    "encode_v25",
]
