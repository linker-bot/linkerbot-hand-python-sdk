"""O20 capability declaration."""

from ...contracts import (
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
    ("thumb_mcp", 0, 120),
    ("thumb_ip", 0, 150),
    ("thumb_abd", 0, 180),
    ("thumb_cmc", 0, 130),
    ("index_abd", -30, 30),
    ("index_mcp", 0, 180),
    ("index_pip", 0, 180),
    ("middle_abd", -30, 30),
    ("middle_mcp", 0, 180),
    ("middle_pip", 0, 180),
    ("ring_abd", -20, 20),
    ("ring_mcp", 0, 180),
    ("ring_pip", 0, 180),
    ("pinky_abd", -20, 20),
    ("pinky_mcp", 0, 180),
    ("pinky_dip", 0, 180),
)
_DIRECTIONAL_AXES = {2, 3, 4, 7, 10, 13}
_MOTION_TYPES = (
    JointMotionType.FLEXION,
    JointMotionType.FLEXION,
    JointMotionType.ROLL,
    JointMotionType.ROTATION,
    JointMotionType.ABDUCTION,
    JointMotionType.FLEXION,
    JointMotionType.FLEXION,
    JointMotionType.ABDUCTION,
    JointMotionType.FLEXION,
    JointMotionType.FLEXION,
    JointMotionType.ABDUCTION,
    JointMotionType.FLEXION,
    JointMotionType.FLEXION,
    JointMotionType.ABDUCTION,
    JointMotionType.FLEXION,
    JointMotionType.FLEXION,
)
_DIRECTION_ENDPOINTS = {
    2: (JointEndpoint.PARALLEL_TO_PALM, JointEndpoint.PERPENDICULAR_TO_PALM),
    3: (JointEndpoint.AWAY_FROM_INDEX_FINGER, JointEndpoint.TOWARD_INDEX_FINGER),
    4: (JointEndpoint.AWAY_FROM_THUMB, JointEndpoint.TOWARD_THUMB),
    7: (JointEndpoint.AWAY_FROM_THUMB, JointEndpoint.TOWARD_THUMB),
    10: (JointEndpoint.AWAY_FROM_THUMB, JointEndpoint.TOWARD_THUMB),
    13: (JointEndpoint.AWAY_FROM_THUMB, JointEndpoint.TOWARD_THUMB),
}


def _joints() -> tuple[JointCapability, ...]:
    return tuple(
        JointCapability(
            index=index,
            name=name,
            raw_angle_range=NumericRange(
                minimum, maximum, "degree", integer_only=True, step=1
            ),
            percentage_mapping=PercentageMapping(
                raw_open=maximum if index in _DIRECTIONAL_AXES else minimum,
                raw_closed=minimum if index in _DIRECTIONAL_AXES else maximum,
                physical_angle=NumericRange(minimum, maximum, "degree"),
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
                0, 100, "protocol_unit", integer_only=True, step=1
            ),
            torque_range=NumericRange(0, 1000, "6.5_mA", integer_only=True, step=1),
            motion_type=_MOTION_TYPES[index],
            coupled_with=(
                ("thumb_cmc",) if index == 2 else (("thumb_abd",) if index == 3 else ())
            ),
        )
        for index, (name, minimum, maximum) in enumerate(_JOINTS)
    )


CAPABILITIES = DeviceCapabilities(
    model="O20",
    firmware=None,
    side=HandSide.UNKNOWN,
    joints=_joints(),
    sensors=(
        sensor(SensorType.ANGLES, 30),
        sensor(SensorType.SPEEDS, 30),
        sensor(SensorType.FORCE, 30),
        sensor(SensorType.TEMPERATURE, 1),
        sensor(SensorType.CURRENT, 10),
        sensor(SensorType.FAULTS, 5),
    ),
    transport=TransportType.CAN_FD,
    features=DeviceFeatures(clear_faults=True),
)

__all__ = ["CAPABILITIES"]
