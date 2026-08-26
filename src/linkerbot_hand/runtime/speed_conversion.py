"""Pure conversions between percentage and protocol-native joint speeds."""

from __future__ import annotations

from collections.abc import Iterable

from ..commands.validation import coerce_values, finite_number
from ..contracts import DeviceCapabilities
from ..errors import UnsupportedOperationError, ValidationError


def percentage_speeds_to_raw(
    values: Iterable[float | int], capabilities: DeviceCapabilities
) -> tuple[float | int, ...]:
    """Convert explicit 0..100 speed values to protocol-native values."""
    percentages = coerce_values(values)
    _validate_count(percentages, capabilities)
    result: list[float | int] = []
    for value, joint in zip(percentages, capabilities.joints, strict=True):
        bounds = joint.speed_range
        if not joint.percentage_speed_supported or bounds is None:
            raise UnsupportedOperationError(
                f"joint {joint.name} has no verified percentage speed mapping"
            )
        numeric = finite_number(value, "speed percentage")
        if not 0.0 <= numeric <= 100.0:
            raise ValidationError("speed percentages must be in [0, 100]")
        protocol_value = bounds.minimum + numeric / 100.0 * (
            bounds.maximum - bounds.minimum
        )
        result.append(round(protocol_value) if bounds.integer_only else protocol_value)
    return tuple(result)


def raw_speeds_to_percentage(
    values: Iterable[float | int], capabilities: DeviceCapabilities
) -> tuple[float, ...]:
    """Convert protocol-native speeds through an explicitly declared mapping."""
    raw_values = coerce_values(values)
    _validate_count(raw_values, capabilities)
    result: list[float] = []
    for value, joint in zip(raw_values, capabilities.joints, strict=True):
        bounds = joint.speed_range
        if not joint.percentage_speed_supported or bounds is None:
            raise UnsupportedOperationError(
                f"joint {joint.name} has no verified percentage speed mapping"
            )
        numeric = finite_number(value, "raw speed")
        span = bounds.maximum - bounds.minimum
        result.append(100.0 * (numeric - bounds.minimum) / span)
    return tuple(result)


def _validate_count(
    values: tuple[float | int, ...], capabilities: DeviceCapabilities
) -> None:
    if len(values) != capabilities.joint_count:
        raise ValidationError(f"expected {capabilities.joint_count} joint values")


__all__ = ["percentage_speeds_to_raw", "raw_speeds_to_percentage"]
