"""O6 capability declaration."""

from ...contracts import (
    DeviceCapabilities,
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
_SPEED_RANGE = NumericRange(
    0,
    255,
    "count",
    True,
    1,
    physical_unit="rpm",
    physical_minimum=0,
    physical_maximum=180,
)
_TORQUE_RANGE = NumericRange(
    0,
    255,
    "count",
    True,
    1,
    physical_unit="mA",
    physical_minimum=0,
    physical_maximum=1000,
)
_ACCELERATION_RANGE = NumericRange(
    0,
    254,
    "count",
    True,
    1,
    physical_unit="degree_per_second_squared",
)

CAPABILITIES = DeviceCapabilities(
    model="O6",
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
        speed_range=_SPEED_RANGE,
        torque_range=_TORQUE_RANGE,
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
            SensorType.SPEEDS,
            30,
            unit="count",
            encoding="u8",
            value_range=_SPEED_RANGE,
            physical_unit="rpm",
        ),
        sensor(
            SensorType.ACCELERATION,
            10,
            unit="count",
            encoding="u8",
            value_range=_ACCELERATION_RANGE,
            physical_unit="degree_per_second_squared",
        ),
        sensor(
            SensorType.TORQUES,
            30,
            unit="count",
            encoding="u8",
            value_range=_TORQUE_RANGE,
            physical_unit="mA",
        ),
        sensor(
            SensorType.FORCE,
            30,
            unit="count",
            encoding="u8",
            value_range=_COUNT_RANGE,
            value_kind=SensorValueKind.TACTILE_MATRIX,
            matrix_shape=(10, 4),
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
            SensorType.FAULTS,
            5,
            unit="dimensionless",
            encoding="u8",
            value_range=NumericRange(0, 255, "dimensionless", True, 1),
            value_kind=SensorValueKind.BITMASK,
        ),
    ),
    transport=TransportType.CAN,
)

__all__ = ["CAPABILITIES"]
