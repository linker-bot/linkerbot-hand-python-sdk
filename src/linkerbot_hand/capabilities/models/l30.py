"""L30 capability declaration."""

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
    ("j1", 0, 900),
    ("j2", 0, 1200),
    ("j3", 0, 900),
    ("j4", 0, 800),
    ("j5", -200, 200),
    ("j6", 0, 1500),
    ("j7", 0, 1600),
    ("j8", 0, 1600),
    ("j9", 0, 1500),
    ("j10", 0, 1600),
    ("j11", 0, 1500),
    ("j12", -200, 200),
    ("j13", -200, 200),
    ("j14", -200, 200),
    ("j15", 0, 1600),
    ("j16", 0, 1500),
    ("j17", -1000, 1000),
)
_DIRECTIONAL_AXES = {2, 3, 4, 11, 12, 13, 16}
_DIRECTION_ENDPOINTS = {
    2: (JointEndpoint.AWAY_FROM_THUMB_WEB, JointEndpoint.TOWARD_THUMB_WEB),
    3: (JointEndpoint.AWAY_FROM_PALM, JointEndpoint.TOWARD_PALM),
    4: (JointEndpoint.TOWARD_LITTLE_FINGER, JointEndpoint.TOWARD_INDEX_FINGER),
    11: (JointEndpoint.TOWARD_LITTLE_FINGER, JointEndpoint.TOWARD_INDEX_FINGER),
    12: (JointEndpoint.TOWARD_LITTLE_FINGER, JointEndpoint.TOWARD_INDEX_FINGER),
    13: (JointEndpoint.TOWARD_LITTLE_FINGER, JointEndpoint.TOWARD_INDEX_FINGER),
    16: (JointEndpoint.FORWARD, JointEndpoint.BACKWARD),
}
_MOTION_TYPES = (
    JointMotionType.FLEXION,
    JointMotionType.FLEXION,
    JointMotionType.ABDUCTION,
    JointMotionType.ROTATION,
    JointMotionType.ABDUCTION,
    JointMotionType.FLEXION,
    JointMotionType.FLEXION,
    JointMotionType.FLEXION,
    JointMotionType.FLEXION,
    JointMotionType.FLEXION,
    JointMotionType.FLEXION,
    JointMotionType.ABDUCTION,
    JointMotionType.ABDUCTION,
    JointMotionType.ABDUCTION,
    JointMotionType.FLEXION,
    JointMotionType.FLEXION,
    JointMotionType.WRIST,
)


def _joints() -> tuple[JointCapability, ...]:
    return tuple(
        JointCapability(
            index=index,
            name=name,
            raw_angle_range=NumericRange(
                minimum,
                maximum,
                "protocol_position",
                integer_only=True,
                step=1,
            ),
            percentage_mapping=PercentageMapping(
                raw_open=maximum if index in _DIRECTIONAL_AXES else minimum,
                raw_closed=minimum if index in _DIRECTIONAL_AXES else maximum,
                semantics=(
                    PercentageSemantics.POSITION
                    if index in _DIRECTIONAL_AXES
                    else PercentageSemantics.OPENNESS
                ),
                zero_endpoint=_DIRECTION_ENDPOINTS.get(
                    index,
                    (JointEndpoint.FLEXED, JointEndpoint.EXTENDED),
                )[0],
                hundred_endpoint=_DIRECTION_ENDPOINTS.get(
                    index,
                    (JointEndpoint.FLEXED, JointEndpoint.EXTENDED),
                )[1],
            ),
            speed_range=NumericRange(
                1, 250, "protocol_unit", integer_only=True, step=1
            ),
            # The protocol calls this field torque, but it is the motor
            # current limit. Public commands use mA and are quantized to the
            # 60..800 protocol range by the legacy adapter.
            torque_range=NumericRange(60 * 6.5, 800 * 6.5, "mA"),
            motion_type=_MOTION_TYPES[index],
        )
        for index, (name, minimum, maximum) in enumerate(_JOINTS)
    )


CAPABILITIES = DeviceCapabilities(
    model="L30",
    firmware=None,
    side=HandSide.UNKNOWN,
    joints=_joints(),
    sensors=(
        sensor(SensorType.ANGLES, 60),
        sensor(SensorType.SPEEDS, 30, unit="protocol_unit"),
        sensor(SensorType.FORCE, 30),
        sensor(SensorType.TEMPERATURE, 1),
        sensor(SensorType.CURRENT, 10, unit="mA"),
        sensor(SensorType.FAULTS, 5),
    ),
    transport=TransportType.CAN_FD,
    features=DeviceFeatures(
        device_ack=True,
        enable=True,
        calibration=True,
        acknowledged_commands=(
            CommandKind.ENABLE,
            CommandKind.DISABLE,
            CommandKind.CALIBRATE,
        ),
    ),
)

__all__ = ["CAPABILITIES"]
