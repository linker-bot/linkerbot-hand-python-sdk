"""O30i capability declaration."""

from ...contracts import (
    CommandKind,
    DeviceCapabilities,
    DeviceFeatures,
    HandSide,
    JointCapability,
    JointEndpoint,
    JointMotionType,
    NumericRange,
    PercentageMapping,
    PercentageSemantics,
    SensorType,
    TransportType,
)
from ..builders import sensor

_JOINTS = (
    "roll_0",
    "yaw_0",
    "yaw_1",
    "yaw_2",
    "yaw_3",
    "yaw_4",
    "root1_0",
    "root1_1",
    "root1_2",
    "root1_3",
    "root1_4",
    "root2_1",
    "root2_2",
    "root2_3",
    "root2_4",
    "tip_0",
    "tip_1",
    "tip_2",
    "tip_3",
    "tip_4",
)
_DIRECTION_ENDPOINTS = (
    (JointEndpoint.AWAY_FROM_INDEX_FINGER, JointEndpoint.TOWARD_INDEX_FINGER),
    (JointEndpoint.PARALLEL_TO_PALM, JointEndpoint.PERPENDICULAR_TO_PALM),
    (JointEndpoint.TOWARD_THUMB, JointEndpoint.AWAY_FROM_THUMB),
    (JointEndpoint.TOWARD_THUMB, JointEndpoint.AWAY_FROM_THUMB),
    (JointEndpoint.TOWARD_THUMB, JointEndpoint.AWAY_FROM_THUMB),
    (JointEndpoint.TOWARD_THUMB, JointEndpoint.AWAY_FROM_THUMB),
)
_MOTION_TYPES = (
    JointMotionType.ROTATION,
    JointMotionType.ROLL,
    JointMotionType.ABDUCTION,
    JointMotionType.ABDUCTION,
    JointMotionType.ABDUCTION,
    JointMotionType.ABDUCTION,
) + (JointMotionType.FLEXION,) * 14


def _joints() -> tuple[JointCapability, ...]:
    return tuple(
        JointCapability(
            index=index,
            name=name,
            raw_angle_range=NumericRange(
                0, 255, "protocol_raw_u8", integer_only=True, step=1
            ),
            percentage_mapping=PercentageMapping(
                raw_open=255 if index < 6 else 0,
                raw_closed=0 if index < 6 else 255,
                semantics=(
                    PercentageSemantics.POSITION
                    if index < 6
                    else PercentageSemantics.OPENNESS
                ),
                zero_endpoint=(
                    _DIRECTION_ENDPOINTS[index][0]
                    if index < 6
                    else JointEndpoint.FLEXED
                ),
                hundred_endpoint=(
                    _DIRECTION_ENDPOINTS[index][1]
                    if index < 6
                    else JointEndpoint.EXTENDED
                ),
            ),
            speed_range=NumericRange(
                0, 255, "protocol_raw_u8", integer_only=True, step=1
            ),
            torque_range=NumericRange(
                0, 255, "protocol_raw_u8", integer_only=True, step=1
            ),
            motion_type=_MOTION_TYPES[index],
        )
        for index, name in enumerate(_JOINTS)
    )


CAPABILITIES = DeviceCapabilities(
    model="O30i",
    firmware=None,
    side=HandSide.UNKNOWN,
    joints=_joints(),
    sensors=(
        *(
            sensor(
                sensor_type,
                hz,
                unit=unit,
                encoding="u8",
                value_range=NumericRange(0, 255, unit, True, 1),
            )
            for sensor_type, hz, unit in (
                (SensorType.ANGLES, 30, "protocol_raw"),
                (SensorType.SPEEDS, 30, "protocol_raw"),
                (SensorType.ACCELERATION, 10, "protocol_raw"),
                (SensorType.TORQUES, 30, "protocol_raw"),
                (SensorType.TEMPERATURE, 1, "protocol_raw"),
                (SensorType.CURRENT, 10, "protocol_raw"),
                (SensorType.FAULTS, 5, "bitmask"),
                (SensorType.VOLTAGE, 1, "protocol_raw"),
                (SensorType.MOTION_TIME, 10, "protocol_tick"),
            )
        ),
    ),
    transport=TransportType.CAN_FD,
    features=DeviceFeatures(
        device_ack=True,
        acknowledged_commands=(
            CommandKind.ANGLES,
            CommandKind.SPEEDS,
            CommandKind.TORQUES,
        ),
    ),
)

__all__ = ["CAPABILITIES"]
