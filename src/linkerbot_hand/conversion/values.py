"""Strict validation and percent conversion for writable protocol vectors."""

from __future__ import annotations

import math
from collections.abc import Iterable

from ..api.enums import Quantization
from ..api.errors import UnsupportedOperationError, ValidationError
from ..api.readings import RawNumber
from ..capabilities.ranges import is_finite_number
from ..capabilities.values import CommandCapability


def validate_command_values(
    values: Iterable[RawNumber],
    capability: CommandCapability,
    *,
    label: str | None = None,
) -> tuple[RawNumber, ...]:
    """Validate exact protocol values without conversion or clamping.

    Args:
        values: One protocol value per declared command channel.
        capability: Capability metadata for the command being submitted.
        label: Optional name used in validation errors.

    Returns:
        The validated values as an immutable tuple.

    Raises:
        ValidationError: If the input shape, type, or range is invalid.
    """

    name = capability.semantic.value if label is None else label
    result = _coerce_values(values, len(capability.channels), name)
    for index, (channel, value) in enumerate(
        zip(capability.channels, result, strict=True)
    ):
        if not capability.range_for(index).contains(value):
            raise ValidationError(
                f"{name} value for {channel} is outside the declared range"
            )
    return result


def percent_to_raw_values(
    values: Iterable[RawNumber],
    capability: CommandCapability,
    *,
    operation: str,
    model: str,
    label: str | None = None,
) -> tuple[RawNumber, ...]:
    """Convert a strict percent vector using immutable capability mappings.

    Args:
        values: One finite value in ``0..100`` per command channel.
        capability: Writable command metadata containing percent mappings.
        operation: Public operation name used in structured errors.
        model: Canonical device model used in structured errors.
        label: Optional name used in validation errors.

    Returns:
        Quantized exact protocol values in capability channel order.

    Raises:
        ValidationError: If input values are malformed or outside ``0..100``.
        UnsupportedOperationError: If any channel lacks a percent mapping.
    """

    name = f"percent {capability.semantic.value}" if label is None else label
    result = _coerce_values(values, len(capability.channels), name)
    for channel, value in zip(capability.channels, result, strict=True):
        if not 0 <= value <= 100:
            raise ValidationError(f"{name} value for {channel} must be 0 through 100")
    mappings = capability.percent_mappings
    if mappings is None or any(mapping is None for mapping in mappings):
        raise UnsupportedOperationError(
            f"{capability.semantic.value} do not support percent values",
            operation=operation,
            model=model,
        )

    converted: list[RawNumber] = []
    for index, (channel, value, mapping) in enumerate(
        zip(capability.channels, result, mappings, strict=True)
    ):
        if mapping is None:  # Kept explicit for static type narrowing.
            raise UnsupportedOperationError(
                f"{channel} has no percent mapping",
                operation=operation,
                model=model,
            )
        raw = mapping.raw_at_zero + value / 100 * (
            mapping.raw_at_hundred - mapping.raw_at_zero
        )
        raw_range = capability.range_for(index)
        if raw_range.integer_only:
            raw = _quantize(raw, mapping.quantization)
        if not raw_range.contains(raw):
            raise ValidationError(
                f"percent mapping for {channel} produced an out-of-range value"
            )
        converted.append(raw)
    return tuple(converted)


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
    if any(not is_finite_number(value) for value in result):
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


__all__ = ["percent_to_raw_values", "validate_command_values"]
