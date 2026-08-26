"""Shared validation and explicit percentage conversion for protocol vectors."""

from __future__ import annotations

import math
from collections.abc import Sequence

from linkerbot_transport.errors import ValidationError


def validate_u8_vector(
    values: Sequence[int], *, count: int, name: str
) -> tuple[int, ...]:
    """Validate an exact-length unsigned-byte protocol vector."""
    return validate_int_vector(values, count=count, name=name, minimum=0, maximum=255)


def validate_int_vector(
    values: Sequence[int],
    *,
    count: int,
    name: str,
    minimum: int,
    maximum: int,
) -> tuple[int, ...]:
    """Validate an exact-length bounded integer protocol vector."""
    if len(values) != count:
        raise ValidationError(f"{name} must contain {count} values")
    result = tuple(values)
    for index, value in enumerate(result):
        if type(value) is not int:
            raise ValidationError(f"{name}[{index}] must be int")
        if not minimum <= value <= maximum:
            raise ValidationError(
                f"{name}[{index}] must be between {minimum} and {maximum}"
            )
    return result


def percentage_to_u8_vector(
    values: Sequence[float | int], *, count: int, name: str
) -> tuple[int, ...]:
    """Convert an exact-length 0..100 vector to unsigned protocol bytes."""
    if len(values) != count:
        raise ValidationError(f"{name} must contain {count} values")
    result: list[int] = []
    for index, value in enumerate(values):
        if isinstance(value, bool) or not isinstance(value, (float, int)):
            raise ValidationError(f"{name}[{index}] must be numeric")
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValidationError(f"{name}[{index}] must be finite")
        if not 0 <= numeric <= 100:
            raise ValidationError(f"{name}[{index}] must be between 0 and 100")
        result.append(round(numeric * 255 / 100))
    return tuple(result)


def u8_vector_to_percentage(values: Sequence[int], *, count: int) -> tuple[float, ...]:
    """Convert validated unsigned protocol bytes to 0..100 values."""
    return tuple(
        value * 100 / 255
        for value in validate_u8_vector(values, count=count, name="values")
    )


def protocol_vector_to_scaled(
    values: Sequence[int],
    *,
    count: int,
    name: str,
    minimum: int,
    maximum: int,
    scale: float,
) -> tuple[float, ...]:
    """Convert a bounded integer protocol vector with a linear unit scale."""
    protocol_values = validate_int_vector(
        values,
        count=count,
        name=name,
        minimum=minimum,
        maximum=maximum,
    )
    return tuple(value * scale for value in protocol_values)


def scaled_vector_to_protocol(
    values: Sequence[float | int],
    *,
    count: int,
    name: str,
    minimum: int,
    maximum: int,
    scale: float,
) -> tuple[int, ...]:
    """Quantize linearly scaled values into a bounded protocol vector."""
    if len(values) != count:
        raise ValidationError(f"{name} must contain {count} values")
    result: list[int] = []
    for index, value in enumerate(values):
        if isinstance(value, bool) or not isinstance(value, (float, int)):
            raise ValidationError(f"{name}[{index}] must be numeric")
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValidationError(f"{name}[{index}] must be finite")
        protocol_value = round(numeric / scale)
        if not minimum <= protocol_value <= maximum:
            raise ValidationError(
                f"{name}[{index}] must map between {minimum} and {maximum}"
            )
        result.append(protocol_value)
    return tuple(result)


__all__ = [
    "percentage_to_u8_vector",
    "protocol_vector_to_scaled",
    "scaled_vector_to_protocol",
    "u8_vector_to_percentage",
    "validate_int_vector",
    "validate_u8_vector",
]
