"""L25 capability declaration."""

from dataclasses import replace

from ...contracts import (
    DeviceCapabilities,
    DeviceFeatures,
    HandSide,
    JointCapability,
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
_POSITION_MAXIMA = (
    80,
    90,
    47.9,
    155.7,
    30,
    75,
    178.8,
    30,
    75,
    75,
    30,
    75,
    75,
    30,
    75,
    75,
)
_CURRENT_RANGES = (
    (400, 700),
    (400, 700),
    (0, 1024),
    (0, 1024),
    (450, 800),
    (450, 800),
    (450, 800),
    (450, 800),
    (450, 800),
    (450, 800),
    (450, 800),
    (450, 800),
    (450, 800),
    (450, 800),
    (450, 800),
    (450, 800),
)
_SPEED_RANGES: tuple[tuple[float, float, str] | None, ...] = (
    (0, 180, "rpm"),
    (0, 180, "rpm"),
    (0, 30000, "rpm"),
    (0, 30000, "rpm"),
    (0.02, 40, "millimeter_per_second"),
    None,
    (0.02, 40, "millimeter_per_second"),
    (0.02, 40, "millimeter_per_second"),
    None,
    (0.02, 40, "millimeter_per_second"),
    (0.02, 40, "millimeter_per_second"),
    None,
    (0.02, 40, "millimeter_per_second"),
    (0.02, 40, "millimeter_per_second"),
    None,
    (0.02, 40, "millimeter_per_second"),
)


def _joints() -> tuple[JointCapability, ...]:
    base = classic_joints(
        (
            "thumb_abd",
            "thumb_yaw",
            "thumb_root1",
            "thumb_tip",
            "index_abd",
            "index_root1",
            "index_tip",
            "middle_abd",
            "middle_root1",
            "middle_tip",
            "ring_abd",
            "ring_root1",
            "ring_tip",
            "pinky_abd",
            "pinky_root1",
            "pinky_tip",
        ),
        ((255, 0),) * 16,
        {0, 1, 4, 7, 10, 13},
        motion_types=(
            JointMotionType.ROTATION,
            JointMotionType.ROLL,
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
            JointMotionType.ABDUCTION,
            JointMotionType.FLEXION,
            JointMotionType.FLEXION,
        ),
        direction_endpoints={
            0: (
                JointEndpoint.TOWARD_INDEX_FINGER,
                JointEndpoint.AWAY_FROM_INDEX_FINGER,
            ),
            1: (
                JointEndpoint.PERPENDICULAR_TO_PALM,
                JointEndpoint.PARALLEL_TO_PALM,
            ),
            4: (
                JointEndpoint.TOWARD_LITTLE_FINGER,
                JointEndpoint.TOWARD_THUMB,
            ),
            7: (
                JointEndpoint.TOWARD_LITTLE_FINGER,
                JointEndpoint.TOWARD_THUMB,
            ),
            10: (
                JointEndpoint.TOWARD_LITTLE_FINGER,
                JointEndpoint.TOWARD_THUMB,
            ),
            13: (
                JointEndpoint.TOWARD_LITTLE_FINGER,
                JointEndpoint.TOWARD_THUMB,
            ),
        },
    )
    result = []
    for index, joint in enumerate(base):
        speed = _SPEED_RANGES[index]
        current_minimum, current_maximum = _CURRENT_RANGES[index]
        result.append(
            replace(
                joint,
                raw_angle_range=NumericRange(
                    0,
                    255,
                    "count",
                    True,
                    1,
                    physical_unit="degree",
                    physical_minimum=0,
                    physical_maximum=_POSITION_MAXIMA[index],
                ),
                speed_range=NumericRange(
                    0,
                    255,
                    "count",
                    True,
                    1,
                    physical_unit=None if speed is None else speed[2],
                    physical_minimum=None if speed is None else speed[0],
                    physical_maximum=None if speed is None else speed[1],
                ),
                torque_range=NumericRange(
                    0,
                    255,
                    "count",
                    True,
                    1,
                    physical_unit="mA",
                    physical_minimum=current_minimum,
                    physical_maximum=current_maximum,
                ),
            )
        )
    return tuple(result)


CAPABILITIES = DeviceCapabilities(
    model="L25",
    firmware=None,
    side=HandSide.UNKNOWN,
    joints=_joints(),
    sensors=(
        sensor(
            SensorType.ANGLES,
            60,
            unit="count",
            encoding="u8",
            value_range=_COUNT_RANGE,
            physical_unit="degree",
        ),
        sensor(
            SensorType.SPEEDS,
            30,
            unit="count",
            encoding="u8",
            value_range=_COUNT_RANGE,
        ),
        sensor(
            SensorType.TORQUES,
            30,
            unit="count",
            encoding="u8",
            value_range=_COUNT_RANGE,
            physical_unit="mA",
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
