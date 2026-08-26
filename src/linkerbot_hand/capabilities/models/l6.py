"""L6 capability declaration."""

from ...contracts import (
    DeviceCapabilities,
    DeviceFeatures,
    HandSide,
    JointEndpoint,
    JointMotionType,
    NumericRange,
    SensorType,
    SensorValueKind,
    TransportType,
)
from ..builders import classic_joints, sensor

_COUNT_RANGE = NumericRange(0, 255, "count", True, 1)
_TEMPERATURE_RANGE = NumericRange(
    0,
    255,
    "degree_celsius",
    True,
    1,
    physical_unit="degree_celsius",
    physical_minimum=0,
    physical_maximum=255,
    scale=1,
    offset=0,
)
_CURRENT_RANGE = NumericRange(
    0,
    255,
    "count",
    True,
    1,
    physical_unit="mA",
    physical_minimum=0,
    physical_maximum=1400,
    scale=1400 / 255,
    offset=0,
)

CAPABILITIES = DeviceCapabilities(
    model="L6",
    firmware=None,
    side=HandSide.UNKNOWN,
    joints=classic_joints(
        ("thumb_flex", "thumb_abd", "index", "middle", "ring", "pinky"),
        ((255, 0), (255, 0), (255, 0), (255, 0), (255, 0), (255, 0)),
        {1},
        motion_types=(
            JointMotionType.FLEXION,
            JointMotionType.ABDUCTION,
            JointMotionType.FLEXION,
            JointMotionType.FLEXION,
            JointMotionType.FLEXION,
            JointMotionType.FLEXION,
        ),
        direction_endpoints={
            1: (
                JointEndpoint.TOWARD_THUMB_WEB,
                JointEndpoint.AWAY_FROM_THUMB_WEB,
            )
        },
        torque_range=_CURRENT_RANGE,
    ),
    sensors=(
        sensor(
            SensorType.ANGLES,
            60,
            unit="count",
            encoding="u8",
            value_range=_COUNT_RANGE,
        ),
        sensor(
            SensorType.TORQUES,
            30,
            unit="count",
            encoding="u8",
            value_range=_CURRENT_RANGE,
            physical_unit="mA",
            scale=1400 / 255,
            offset=0,
        ),
        sensor(
            SensorType.FORCE,
            30,
            unit="count",
            encoding="u8",
            value_range=_COUNT_RANGE,
            value_kind=SensorValueKind.TACTILE_MATRIX,
            matrix_shape=(12, 6),
        ),
        sensor(
            SensorType.TEMPERATURE,
            1,
            unit="degree_celsius",
            encoding="u8",
            value_range=_TEMPERATURE_RANGE,
            physical_unit="degree_celsius",
            scale=1,
            offset=0,
        ),
        sensor(
            SensorType.CURRENT,
            10,
            unit="count",
            encoding="u8",
            value_range=_CURRENT_RANGE,
            physical_unit="mA",
            scale=1400 / 255,
            offset=0,
        ),
        sensor(
            SensorType.FAULTS,
            5,
            unit="dimensionless",
            encoding="u8",
            value_range=NumericRange(0, 255, "dimensionless", True, 1),
            value_kind=SensorValueKind.BITMASK,
        ),
    ),
    transport=TransportType.CAN,
    features=DeviceFeatures(clear_faults=True),
)

__all__ = ["CAPABILITIES"]
