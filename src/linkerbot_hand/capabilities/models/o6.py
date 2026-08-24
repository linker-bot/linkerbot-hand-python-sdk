"""O6 capability declaration."""

from ...contracts import (
    DeviceCapabilities,
    HandSide,
    JointEndpoint,
    JointMotionType,
    NumericRange,
    SensorType,
    TransportType,
)
from ..builders import classic_joints, sensor

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
        speed_range=NumericRange(0, 180, "rpm"),
        torque_range=NumericRange(0, 1000, "mA"),
    ),
    sensors=(
        sensor(SensorType.ANGLES, 60),
        sensor(SensorType.SPEEDS, 30, unit="rpm"),
        sensor(SensorType.ACCELERATION, 10, unit="degree_per_second_squared"),
        sensor(SensorType.TORQUES, 30, unit="mA"),
        sensor(SensorType.FORCE, 30),
        sensor(SensorType.TEMPERATURE, 1),
        sensor(SensorType.FAULTS, 5),
    ),
    transport=TransportType.CAN,
)

__all__ = ["CAPABILITIES"]
