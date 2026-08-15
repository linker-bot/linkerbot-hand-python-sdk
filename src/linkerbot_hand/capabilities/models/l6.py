"""L6 capability declaration."""

from ...contracts import (
    DeviceCapabilities,
    DeviceFeatures,
    HandSide,
    JointEndpoint,
    JointMotionType,
    SensorType,
    TransportType,
)
from ..builders import classic_joints, sensor

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
    ),
    sensors=(
        sensor(SensorType.ANGLES, 60),
        sensor(SensorType.TORQUES, 30),
        sensor(SensorType.FORCE, 30),
        sensor(SensorType.TEMPERATURE, 1),
        sensor(SensorType.CURRENT, 10),
        sensor(SensorType.FAULTS, 5),
    ),
    transport=TransportType.CAN,
    features=DeviceFeatures(clear_faults=True),
)

__all__ = ["CAPABILITIES"]
