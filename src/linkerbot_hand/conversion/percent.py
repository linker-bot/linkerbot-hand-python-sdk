"""Strict percent/raw angle conversion driven only by capabilities."""

from __future__ import annotations

import math
from collections.abc import Iterable

from ..api.enums import Quantization
from ..api.errors import UnsupportedOperationError, ValidationError
from ..api.readings import RawNumber
from ..capabilities.ranges import is_finite_number
from ..capabilities.types import DeviceCapabilities


def percent_to_raw_angles(
    values: Iterable[RawNumber],
    capabilities: DeviceCapabilities,
) -> tuple[RawNumber, ...]:
    """Convert strict ``0..100`` joint values to exact protocol values.

    Args:
        values: One finite percent value per joint in capability order.
        capabilities: Selected immutable device capability profile.

    Returns:
        Quantized protocol values in capability order.

    Raises:
        ValidationError: If values have invalid count, type, or range.
        UnsupportedOperationError: If any joint lacks a percent mapping.
    """

    normalized = _coerce_values(values, len(capabilities.joints), "percent angles")
    result: list[RawNumber] = []
    for joint, value in zip(capabilities.joints, normalized, strict=True):
        if not 0 <= value <= 100:
            raise ValidationError(
                f"percent angle for {joint.name} must be 0 through 100"
            )
        mapping = joint.percent_mapping
        if mapping is None:
            raise UnsupportedOperationError(
                f"joint {joint.name} has no percent angle mapping",
                operation="set_percent_angles",
                model=capabilities.model,
            )
        raw = mapping.raw_at_zero + value / 100 * (
            mapping.raw_at_hundred - mapping.raw_at_zero
        )
        if joint.raw_angle_range.integer_only:
            raw = _quantize(raw, mapping.quantization)
        result.append(raw)
    return tuple(result)


def validate_raw_angles(
    values: Iterable[RawNumber],
    capabilities: DeviceCapabilities,
) -> tuple[RawNumber, ...]:
    """Validate exact protocol angle values without conversion or clamping."""

    raw = _coerce_values(values, len(capabilities.joints), "raw angles")
    for joint, value in zip(capabilities.joints, raw, strict=True):
        if not joint.raw_angle_range.contains(value):
            raise ValidationError(
                f"raw angle for {joint.name} is outside the declared range"
            )
    return raw


def raw_angles_to_percent(
    values: Iterable[RawNumber],
    capabilities: DeviceCapabilities,
) -> tuple[float | None, ...]:
    """Map device feedback to percent without rejecting or clamping evidence."""

    raw = _coerce_values(values, len(capabilities.joints), "raw angles")
    result: list[float | None] = []
    for joint, value in zip(capabilities.joints, raw, strict=True):
        mapping = joint.percent_mapping
        if mapping is None:
            result.append(None)
            continue
        result.append(
            100
            * (value - mapping.raw_at_zero)
            / (mapping.raw_at_hundred - mapping.raw_at_zero)
        )
    return tuple(result)


def raw_angles_to_physical_rad(
    values: Iterable[RawNumber],
    capabilities: DeviceCapabilities,
) -> tuple[float | None, ...]:
    """Map feedback only where a profile provides calibrated radians."""

    raw = _coerce_values(values, len(capabilities.joints), "raw angles")
    result: list[float | None] = []
    for joint, value in zip(capabilities.joints, raw, strict=True):
        mapping = joint.physical_angle_mapping
        if mapping is None:
            result.append(None)
            continue
        result.append(
            mapping.radians_at_first
            + (value - mapping.raw_at_first)
            * (mapping.radians_at_second - mapping.radians_at_first)
            / (mapping.raw_at_second - mapping.raw_at_first)
        )
    return tuple(result)


def raw_angles_within_declared_range(
    values: Iterable[RawNumber],
    capabilities: DeviceCapabilities,
) -> bool:
    """Return whether all feedback values fall inside declared raw ranges."""

    raw = _coerce_values(values, len(capabilities.joints), "raw angles")
    return all(
        joint.raw_angle_range.contains(value)
        for joint, value in zip(capabilities.joints, raw, strict=True)
    )


def _coerce_values(
    values: Iterable[RawNumber],
    expected_count: int,
    label: str,
) -> tuple[RawNumber, ...]:
    if isinstance(values, (str, bytes)):
        raise ValidationError(f"{label} must be a numeric iterable")
    try:
        result = tuple(values)
    except TypeError as error:
        raise ValidationError(f"{label} must be iterable", cause=error) from error
    if len(result) != expected_count:
        raise ValidationError(
            f"{label} requires {expected_count} values, got {len(result)}"
        )
    for value in result:
        if not is_finite_number(value):
            raise ValidationError(f"{label} values must be finite numbers")
    return result


def _quantize(value: float, rule: Quantization) -> int:
    if rule is Quantization.NEAREST_TIES_AWAY_FROM_ZERO:
        return math.floor(value + 0.5) if value >= 0 else math.ceil(value - 0.5)
    if rule is Quantization.FLOOR:
        return math.floor(value)
    if rule is Quantization.CEIL:
        return math.ceil(value)
    raise ValidationError(f"unsupported quantization rule {rule!r}")


__all__ = [
    "percent_to_raw_angles",
    "raw_angles_to_percent",
    "raw_angles_to_physical_rad",
    "raw_angles_within_declared_range",
    "validate_raw_angles",
]
