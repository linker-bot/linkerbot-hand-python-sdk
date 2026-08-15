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
)
from ..errors import ValidationError


def sensor(sensor_type: SensorType, hz: float) -> SensorCapability:
    """Build standard sensor capability metadata.

    Args:
        sensor_type: Sensor channel implemented by the model.
        hz: Recommended polling frequency in hertz.

    Returns:
        Sensor capability whose staleness threshold is three polling periods.

    Raises:
        ValidationError: If the sensor type or frequency is invalid.
    """
    return SensorCapability(
        sensor=sensor_type,
        recommended_hz=hz,
        stale_after_s=3 / hz,
    )


def classic_joints(
    names: Sequence[str],
    endpoints: Sequence[tuple[int, int]],
    directional_axes: Set[int],
    *,
    motion_types: Sequence[JointMotionType] | None = None,
    direction_endpoints: dict[int, tuple[JointEndpoint, JointEndpoint]] | None = None,
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
            raw_angle_range=NumericRange(
                0, 255, "protocol_raw_u8", integer_only=True, step=1
            ),
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
            speed_range=NumericRange(0, 100, "percent"),
            torque_range=NumericRange(0, 100, "percent"),
            motion_type=motion_types[index],
        )
        for index, name in enumerate(names)
    )


__all__ = ["classic_joints", "sensor"]
