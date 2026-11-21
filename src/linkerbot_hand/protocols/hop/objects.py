"""Standard HOP object indices shared by supported model layouts."""

from ...api.errors import ProtocolError
from ...api.readings import JointFaultStatus

MI_PRODUCT_INFO = 0x41

MI_SENSOR_INFO = 0x31
MI_SENSOR_DATA_1 = 0x32
MI_SENSOR_DATA_2 = 0x33
MI_SENSOR_DATA_3 = 0x34


def decode_fault_statuses(raw: tuple[int, ...]) -> tuple[JointFaultStatus, ...]:
    """Decode shared HOP fault bits, keeping the original byte and reserved bits."""
    if any(type(value) is not int or not 0 <= value <= 255 for value in raw):
        raise ProtocolError("HOP faults require unsigned bytes")
    return tuple(
        JointFaultStatus(
            raw=v,
            hand_layer_stall=bool(v & 1),
            actuator_layer_stall=bool(v & 2),
            over_current=bool(v & 4),
            over_temperature=bool(v & 8),
            actuator_abnormal=bool(v & 64),
            actuator_offline=bool(v & 128),
            reserved_bits=v & 0x30,
        )
        for v in raw
    )
