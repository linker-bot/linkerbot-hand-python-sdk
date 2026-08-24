"""L20Lite capability declaration."""

from ...contracts import (
    DeviceCapabilities,
    HandSide,
    JointEndpoint,
    JointMotionType,
    SensorType,
    TransportType,
)
from ..builders import classic_joints, sensor

CAPABILITIES = DeviceCapabilities(
    model="L20Lite",
    firmware=None,
    side=HandSide.UNKNOWN,
    joints=classic_joints(
        (
            "thumb_flex",
            "thumb_abd",
            "index_flex",
            "middle_flex",
            "ring_flex",
            "pinky_flex",
            "index_abd",
            "ring_abd",
            "pinky_abd",
            "thumb_yaw",
        ),
        (
            (255, 0),
            (255, 0),
            (255, 0),
            (255, 0),
            (255, 0),
            (255, 0),
            (255, 0),
            (255, 0),
            (255, 0),
            (255, 0),
        ),
        {1, 6, 7, 8, 9},
        motion_types=(
            JointMotionType.FLEXION,
            JointMotionType.ROLL,
            JointMotionType.FLEXION,
            JointMotionType.FLEXION,
            JointMotionType.FLEXION,
            JointMotionType.FLEXION,
            JointMotionType.ABDUCTION,
            JointMotionType.ABDUCTION,
            JointMotionType.ABDUCTION,
            JointMotionType.ROTATION,
        ),
        direction_endpoints={
            1: (
                JointEndpoint.PERPENDICULAR_TO_PALM,
                JointEndpoint.PARALLEL_TO_PALM,
            ),
            6: (
                JointEndpoint.TOWARD_MIDDLE_FINGER,
                JointEndpoint.AWAY_FROM_MIDDLE_FINGER,
            ),
            7: (
                JointEndpoint.TOWARD_MIDDLE_FINGER,
                JointEndpoint.AWAY_FROM_MIDDLE_FINGER,
            ),
            8: (
                JointEndpoint.TOWARD_MIDDLE_FINGER,
                JointEndpoint.AWAY_FROM_MIDDLE_FINGER,
            ),
            9: (
                JointEndpoint.TOWARD_INDEX_FINGER,
                JointEndpoint.AWAY_FROM_INDEX_FINGER,
            ),
        },
    ),
    sensors=(
        sensor(SensorType.ANGLES, 60),
        sensor(SensorType.SPEEDS, 30, unit="percent"),
        sensor(SensorType.TORQUES, 30, unit="percent"),
        sensor(SensorType.FORCE, 30),
        sensor(SensorType.TEMPERATURE, 1),
        sensor(SensorType.FAULTS, 5),
    ),
    transport=TransportType.CAN,
)

__all__ = ["CAPABILITIES"]
