"""Reusable builders for model capability declarations."""

from collections.abc import Sequence, Set

from ..contracts import (
    JointCapability,
    JointEndpoint,
    JointMotionType,
    NumericRange,
    PercentageMapping,
    PercentageSemantics,
    SensorCapability,
    SensorType,
    SensorValueKind,
)
from ..errors import ValidationError


def sensor(
    sensor_type: SensorType,
    hz: float,
    *,
    unit: str | None = None,
    encoding: str | None = None,
    value_range: NumericRange | None = None,
    physical_unit: str | None = None,
    scale: float | None = None,
    offset: float | None = None,
    value_kind: SensorValueKind = SensorValueKind.NUMERIC,
    matrix_shape: tuple[int, int] | None = None,
) -> SensorCapability:
    """Build standard sensor capability metadata.

    Args:
        sensor_type: Sensor channel implemented by the model.
        hz: Recommended polling frequency in hertz.
        unit: Unit used by values published for this sensor, when known.
        encoding: Scalar protocol encoding, when known.
        value_range: Shared decoded scalar range, when known.
        physical_unit: Corresponding physical unit, when known.
        scale: Verified raw-to-physical multiplier, when available.
        offset: Verified raw-to-physical offset, when available.
        value_kind: Semantic kind of published values.
        matrix_shape: Per-finger tactile matrix shape, when applicable.

    Returns:
        Sensor capability whose staleness threshold is three polling periods.

    Raises:
        ValidationError: If the sensor type or frequency is invalid.
    """
    return SensorCapability(
        sensor=sensor_type,
        recommended_hz=hz,
        stale_after_s=3 / hz,
        unit=unit,
        encoding=encoding,
        value_range=value_range,
        physical_unit=physical_unit,
        scale=scale,
        offset=offset,
        value_kind=value_kind,
        matrix_shape=matrix_shape,
    )


def classic_joints(
    names: Sequence[str],
    endpoints: Sequence[tuple[int, int]],
    directional_axes: Set[int],
    *,
    motion_types: Sequence[JointMotionType] | None = None,
    direction_endpoints: dict[int, tuple[JointEndpoint, JointEndpoint]] | None = None,
    speed_range: NumericRange | None = None,
    torque_range: NumericRange | None = None,
    percentage_speed_supported: bool = True,
) -> tuple[JointCapability, ...]:
    """Build capabilities for classic unsigned-byte hand protocols.

    Args:
        names: Stable joint names in protocol order.
        endpoints: ``(raw_at_100, raw_at_0)`` pairs in joint order.
        directional_axes: Indices whose normalized values mean position instead
            of flexion openness.
        motion_types: Optional mechanical motion type for each joint. Defaults
            to flexion for every axis.
        direction_endpoints: Physical endpoint names for every directional axis.
        speed_range: Shared protocol speed command range. Defaults to unsigned
            byte values.
        torque_range: Shared protocol torque command range. Defaults to unsigned
            byte values.
        percentage_speed_supported: Whether an explicit 0..100 speed mapping
            is explicitly available as a conversion helper.

    Returns:
        Ordered joint capabilities with raw, normalized, speed, and torque ranges.

    Raises:
        ValidationError: If sequence lengths, indices, or endpoint declarations
            are inconsistent.
    """
    if motion_types is None:
        motion_types = (JointMotionType.FLEXION,) * len(names)
    if direction_endpoints is None:
        direction_endpoints = {}
    if speed_range is None:
        speed_range = NumericRange(0, 255, "count", integer_only=True, step=1)
    if torque_range is None:
        torque_range = NumericRange(0, 255, "count", integer_only=True, step=1)
    if len(endpoints) != len(names) or len(motion_types) != len(names):
        raise ValidationError(
            "classic joint names, endpoints, and motion types must have equal lengths"
        )
    valid_indices = set(range(len(names)))
    if not directional_axes <= valid_indices:
        raise ValidationError("directional axis index is outside the joint list")
    if set(direction_endpoints) != set(directional_axes):
        raise ValidationError(
            "direction endpoints must be declared for every directional axis"
        )
    return tuple(
        JointCapability(
            index=index,
            name=name,
            raw_angle_range=NumericRange(0, 255, "count", integer_only=True, step=1),
            percentage_mapping=PercentageMapping(
                raw_open=endpoints[index][0],
                raw_closed=endpoints[index][1],
                semantics=(
                    PercentageSemantics.POSITION
                    if index in directional_axes
                    else PercentageSemantics.OPENNESS
                ),
                zero_endpoint=direction_endpoints.get(
                    index,
                    (JointEndpoint.FLEXED, JointEndpoint.EXTENDED),
                )[0],
                hundred_endpoint=direction_endpoints.get(
                    index,
                    (JointEndpoint.FLEXED, JointEndpoint.EXTENDED),
                )[1],
            ),
            speed_range=speed_range,
            torque_range=torque_range,
            percentage_speed_supported=percentage_speed_supported,
            motion_type=motion_types[index],
        )
        for index, name in enumerate(names)
    )


__all__ = ["classic_joints", "sensor"]
