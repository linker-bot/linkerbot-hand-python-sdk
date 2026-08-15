"""Pure conversions between public, raw, and physical joint angles."""

from __future__ import annotations

from collections.abc import Iterable

from ..commands.validation import coerce_values, finite_number
from ..contracts import DeviceCapabilities
from ..errors import UnsupportedOperationError, ValidationError


def percent_to_raw(
    values: Iterable[float | int], capabilities: DeviceCapabilities
) -> tuple[int, ...]:
    """Convert normalized joint positions to exact protocol values.

    Args:
        values: One normalized value in [0, 100] per joint.
        capabilities: Mapping metadata for the active model and side.

    Returns:
        Rounded protocol values in joint order.

    Raises:
        ValidationError: If value count or numeric range is invalid.
        UnsupportedOperationError: If any joint lacks a verified mapping.
    """
    percentages = coerce_values(values)
    _validate_count(percentages, capabilities)
    result: list[int] = []
    for value, joint in zip(percentages, capabilities.joints):
        numeric = finite_number(value, "angle percentage")
        if not 0.0 <= numeric <= 100.0:
            raise ValidationError("angle percentages must be in [0, 100]")
        mapping = joint.percentage_mapping
        if mapping is None:
            raise UnsupportedOperationError(
                f"joint {joint.name} has no verified percentage angle mapping"
            )
        raw = mapping.raw_closed + (numeric / 100.0) * (
            mapping.raw_open - mapping.raw_closed
        )
        result.append(round(raw))
    return tuple(result)


def raw_to_percent(
    values: Iterable[float | int], capabilities: DeviceCapabilities
) -> tuple[float, ...]:
    """Convert protocol joint positions to normalized values.

    Conversion is reversible and deliberately does not clamp out-of-range
    protocol readings, allowing callers to detect unexpected device values.

    Args:
        values: One protocol value per joint.
        capabilities: Mapping metadata for the active model and side.

    Returns:
        Normalized positions in joint order.

    Raises:
        ValidationError: If value count or numeric types are invalid.
        UnsupportedOperationError: If any joint lacks a verified mapping.
    """
    raw_values = coerce_values(values)
    _validate_count(raw_values, capabilities)
    result: list[float] = []
    for value, joint in zip(raw_values, capabilities.joints):
        numeric = finite_number(value, "raw angle")
        mapping = joint.percentage_mapping
        if mapping is None:
            raise UnsupportedOperationError(
                f"joint {joint.name} has no verified percentage angle mapping"
            )
        span = mapping.raw_open - mapping.raw_closed
        result.append(100.0 * (numeric - mapping.raw_closed) / span)
    return tuple(result)


def raw_to_physical_angles(
    values: Iterable[float | int], capabilities: DeviceCapabilities
) -> tuple[float, ...]:
    """Convert raw positions to calibrated physical angles.

    Args:
        values: One protocol position per joint.
        capabilities: Raw and physical ranges for the active model and side.

    Returns:
        Physical angles in each joint's declared unit.

    Raises:
        ValidationError: If count or values are outside raw ranges.
        UnsupportedOperationError: If raw ranges or physical calibration are
            unavailable for any joint.
    """
    raw_values = coerce_values(values)
    _validate_count(raw_values, capabilities)
    result: list[float] = []
    for value, joint in zip(raw_values, capabilities.joints):
        mapping = joint.percentage_mapping
        raw = joint.raw_angle_range
        if mapping is None or raw is None:
            raise UnsupportedOperationError(
                f"joint {joint.name} has no verified raw angle range"
            )
        physical = mapping.physical_angle
        if physical is None:
            raise UnsupportedOperationError(
                f"joint {joint.name} has no calibrated physical angle mapping"
            )
        numeric = finite_number(value, "raw angle")
        if not raw.minimum <= numeric <= raw.maximum:
            raise ValidationError(
                f"raw angle must be in [{raw.minimum}, {raw.maximum}]"
            )
        raw_span = raw.maximum - raw.minimum
        ratio = 0.0 if raw_span == 0 else (numeric - raw.minimum) / raw_span
        result.append(physical.minimum + ratio * (physical.maximum - physical.minimum))
    return tuple(result)


def physical_angles_to_raw(
    values: Iterable[float | int], capabilities: DeviceCapabilities
) -> tuple[int, ...]:
    """Convert calibrated physical angles to raw protocol positions.

    Args:
        values: One physical angle per joint in capability-declared units.
        capabilities: Raw and physical ranges for the active model and side.

    Returns:
        Rounded protocol positions in joint order.

    Raises:
        ValidationError: If count or values are outside physical ranges.
        UnsupportedOperationError: If raw ranges or physical calibration are
            unavailable for any joint.
    """
    physical_values = coerce_values(values)
    _validate_count(physical_values, capabilities)
    result: list[int] = []
    for value, joint in zip(physical_values, capabilities.joints):
        mapping = joint.percentage_mapping
        raw = joint.raw_angle_range
        if mapping is None or raw is None:
            raise UnsupportedOperationError(
                f"joint {joint.name} has no verified raw angle range"
            )
        physical = mapping.physical_angle
        if physical is None:
            raise UnsupportedOperationError(
                f"joint {joint.name} has no calibrated physical angle mapping"
            )
        numeric = finite_number(value, "physical angle")
        if not physical.minimum <= numeric <= physical.maximum:
            raise ValidationError(
                "physical angle must be in "
                f"[{physical.minimum}, {physical.maximum}] {physical.unit}"
            )
        physical_span = physical.maximum - physical.minimum
        ratio = (
            0.0 if physical_span == 0 else (numeric - physical.minimum) / physical_span
        )
        result.append(round(raw.minimum + ratio * (raw.maximum - raw.minimum)))
    return tuple(result)


def _validate_count(
    values: tuple[float | int, ...], capabilities: DeviceCapabilities
) -> None:
    if len(values) != capabilities.joint_count:
        raise ValidationError(f"expected {capabilities.joint_count} joint values")


__all__ = [
    "percent_to_raw",
    "physical_angles_to_raw",
    "raw_to_percent",
    "raw_to_physical_angles",
]
