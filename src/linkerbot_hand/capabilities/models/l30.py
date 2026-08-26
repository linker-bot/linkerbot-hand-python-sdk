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
    SensorValueKind,
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
                "count",
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
                1,
                250,
                "count",
                integer_only=True,
                step=1,
                physical_unit="rpm",
                physical_minimum=0.732,
                physical_maximum=183,
                scale=0.732,
                offset=0,
            ),
            # The protocol calls this field torque, but it is a raw motor
            # current-limit count. Physical conversion is 6.5 mA/LSB.
            torque_range=NumericRange(
                60,
                800,
                "count",
                integer_only=True,
                step=1,
                physical_unit="mA",
                physical_minimum=390,
                physical_maximum=5200,
                scale=6.5,
                offset=0,
            ),
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
        sensor(SensorType.ANGLES, 60, unit="count", encoding="i16_be"),
        sensor(
            SensorType.SPEEDS,
            30,
            unit="count",
            encoding="i16_be",
            value_range=NumericRange(
                -32768,
                32767,
                "count",
                True,
                1,
                physical_unit="rpm",
                physical_minimum=-23986.176,
                physical_maximum=23985.444,
                scale=0.732,
                offset=0,
            ),
            physical_unit="rpm",
            scale=0.732,
            offset=0,
        ),
        sensor(
            SensorType.FORCE,
            30,
            unit="count",
            encoding="u8",
            value_range=NumericRange(0, 255, "count", True, 1),
            value_kind=SensorValueKind.TACTILE_MATRIX,
            matrix_shape=(12, 6),
        ),
        sensor(
            SensorType.TEMPERATURE,
            1,
            unit="degree_celsius",
            encoding="u8",
            value_range=NumericRange(
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
            ),
            physical_unit="degree_celsius",
            scale=1,
            offset=0,
        ),
        sensor(
            SensorType.CURRENT,
            10,
            unit="count",
            encoding="i16_be",
            value_range=NumericRange(
                -32768,
                32767,
                "count",
                True,
                1,
                physical_unit="mA",
                physical_minimum=-212992,
                physical_maximum=212985.5,
                scale=6.5,
                offset=0,
            ),
            physical_unit="mA",
            scale=6.5,
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
