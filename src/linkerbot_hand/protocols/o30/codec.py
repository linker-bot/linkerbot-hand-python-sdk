"""O30 object indices, versioned layouts, and installed-joint mappings."""

from __future__ import annotations

from ...api.errors import ProtocolError
from ...api.readings import JointFaultStatus, RawNumber

PUBLIC_JOINT_COUNT = 20
WIRE_SLOT_COUNT = 25
# ``JOINT_SLOT_INDICES`` maps public SDK order to the compact V25 objects used
# by the verified whole-hand control path. It is not a logical joint-ID table.
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
LOGICAL_SLOT_COUNT = 36
# All valid logical IDs, in their protocol order. This is deliberately kept
# separate from the public-vector projection below: logical IDs are not wire
# slots and are not necessarily contiguous on a particular product.
LOGICAL_JOINT_IDS = (
    0,
    1,
    2,
    4,
    6,
    7,
    8,
    9,
    11,
    12,
    13,
    14,
    16,
    17,
    18,
    19,
    21,
    22,
    23,
    24,
)
# Projection from the public SDK joint order to logical IDs. The tuple is
# intentionally named as a mapping to make misuse with V25 wire slots obvious.
PUBLIC_JOINT_LOGICAL_IDS = (
    0,
    1,
    6,
    11,
    16,
    21,
    2,
    7,
    12,
    17,
    22,
    8,
    13,
    18,
    23,
    4,
    9,
    14,
    19,
    24,
)

MI_POSITION = 0x01
MI_SPEED = 0x02
MI_ACCELERATION = 0x03
MI_CURRENT = 0x04
MI_VOLTAGE = 0x05
MI_TORQUE = 0x06
MI_TEMPERATURE = 0x07
MI_MOTION_TIME = 0x08
MI_STALL_TIME = 0x09
MI_STALL_THRESHOLD = 0x0A
MI_STALL_HOLD_CURRENT = 0x0B
MI_FAULT = 0x0C
MI_VALID_JOINT = 0x0D
MI_SENSOR_INFO = 0x31
MI_SENSOR_DATA_1 = 0x32
MI_SENSOR_DATA_2 = 0x33
MI_SENSOR_DATA_3 = 0x34
MI_PRODUCT_INFO = 0x41

FAULT_HAND_LAYER_STALL = 0x01
FAULT_ACTUATOR_LAYER_STALL = 0x02
FAULT_OVER_CURRENT = 0x04
FAULT_OVER_TEMPERATURE = 0x08
FAULT_RESERVED = 0x30
FAULT_ACTUATOR_ABNORMAL = 0x40
FAULT_ACTUATOR_OFFLINE = 0x80


def encode_v25(values: tuple[RawNumber, ...]) -> bytes:
    """Place twenty public values into one 25-byte object with zero gaps."""

    if len(values) != PUBLIC_JOINT_COUNT or any(
        type(value) is not int for value in values
    ):
        raise ProtocolError(
            f"O30 vector must contain {PUBLIC_JOINT_COUNT} integer values"
        )
    normalized = tuple(int(value) for value in values)
    if any(value < 0 or value > 0xFF for value in normalized):
        raise ProtocolError("O30 vector values must be within 0..255")
    slots = bytearray(WIRE_SLOT_COUNT)
    for public_index, wire_slot in enumerate(JOINT_SLOT_INDICES):
        slots[wire_slot] = normalized[public_index]
    return bytes(slots)


def decode_v25(data: bytes) -> tuple[int, ...]:
    """Project one exact V25 object onto the twenty installed joints."""

    if not isinstance(data, bytes) or len(data) != WIRE_SLOT_COUNT:
        raise ProtocolError(f"O30 V25 response must contain {WIRE_SLOT_COUNT} bytes")
    return tuple(data[wire_slot] for wire_slot in JOINT_SLOT_INDICES)


def decode_logical_v36(data: bytes) -> tuple[int, ...]:
    """Project one 36-slot logical object onto the public twenty-joint order."""

    if not isinstance(data, bytes) or len(data) != LOGICAL_SLOT_COUNT:
        raise ProtocolError(
            f"O30 logical response must contain {LOGICAL_SLOT_COUNT} bytes"
        )
    return tuple(data[logical_id] for logical_id in PUBLIC_JOINT_LOGICAL_IDS)


def decode_fault_statuses(raw: tuple[int, ...]) -> tuple[JointFaultStatus, ...]:
    """Decode documented O30 fault bits while preserving each raw byte."""

    if len(raw) != PUBLIC_JOINT_COUNT or any(
        type(value) is not int or not 0 <= value <= 0xFF for value in raw
    ):
        raise ProtocolError(
            f"O30 faults must contain {PUBLIC_JOINT_COUNT} unsigned bytes"
        )
    return tuple(
        JointFaultStatus(
            raw=value,
            hand_layer_stall=bool(value & FAULT_HAND_LAYER_STALL),
            actuator_layer_stall=bool(value & FAULT_ACTUATOR_LAYER_STALL),
            over_current=bool(value & FAULT_OVER_CURRENT),
            over_temperature=bool(value & FAULT_OVER_TEMPERATURE),
            actuator_abnormal=bool(value & FAULT_ACTUATOR_ABNORMAL),
            actuator_offline=bool(value & FAULT_ACTUATOR_OFFLINE),
            reserved_bits=value & FAULT_RESERVED,
        )
        for value in raw
    )


__all__ = [
    "JOINT_SLOT_INDICES",
    "LOGICAL_JOINT_IDS",
    "LOGICAL_SLOT_COUNT",
    "MI_ACCELERATION",
    "MI_CURRENT",
    "MI_FAULT",
    "MI_MOTION_TIME",
    "MI_POSITION",
    "MI_PRODUCT_INFO",
    "MI_SENSOR_DATA_1",
    "MI_SENSOR_DATA_2",
    "MI_SENSOR_DATA_3",
    "MI_SENSOR_INFO",
    "MI_SPEED",
    "MI_STALL_HOLD_CURRENT",
    "MI_STALL_THRESHOLD",
    "MI_STALL_TIME",
    "MI_TEMPERATURE",
    "MI_TORQUE",
    "MI_VALID_JOINT",
    "MI_VOLTAGE",
    "PUBLIC_JOINT_COUNT",
    "PUBLIC_JOINT_LOGICAL_IDS",
    "WIRE_SLOT_COUNT",
    "decode_fault_statuses",
    "decode_logical_v36",
    "decode_v25",
    "encode_v25",
]
