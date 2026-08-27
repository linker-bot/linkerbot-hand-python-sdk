"""Validated raw, percent, and physical-angle mapping metadata."""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..api.enums import Confidence, Quantization
from ..api.errors import ValidationError
from ..api.readings import RawNumber


def is_finite_number(value: object) -> bool:
    """Return whether ``value`` is a finite non-boolean number."""

    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
    )


@dataclass(frozen=True, slots=True)
class NumericRange:
    """Inclusive range for an exact protocol value."""

    minimum: RawNumber
    maximum: RawNumber
    integer_only: bool = False
    step: float | None = None

    def __post_init__(self) -> None:
        if not is_finite_number(self.minimum) or not is_finite_number(self.maximum):
            raise ValidationError("range endpoints must be finite numbers")
        if self.minimum > self.maximum:
            raise ValidationError("range minimum must not exceed maximum")
        if not isinstance(self.integer_only, bool):
            raise ValidationError("integer_only must be bool")
        if self.step is not None and (
            not is_finite_number(self.step) or self.step <= 0
        ):
            raise ValidationError("range step must be finite and positive")

    def contains(self, value: object) -> bool:
        """Return whether one protocol value satisfies this declaration."""

        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        if not math.isfinite(value):
            return False
        if self.integer_only and type(value) is not int:
            return False
        return self.minimum <= value <= self.maximum


@dataclass(frozen=True, slots=True)
class PercentMapping:
    """Linear mapping between public percent and exact protocol values.

    Endpoint labels describe physical direction. Only flexion axes use the
    conventional labels ``flexed`` at zero and ``extended`` at one hundred.
    """

    raw_at_zero: RawNumber
    raw_at_hundred: RawNumber
    zero_label: str
    hundred_label: str
    confidence: Confidence
    quantization: Quantization = Quantization.NEAREST_TIES_AWAY_FROM_ZERO

    def __post_init__(self) -> None:
        if not is_finite_number(self.raw_at_zero) or not is_finite_number(
            self.raw_at_hundred
        ):
            raise ValidationError("percent raw endpoints must be finite numbers")
        if self.raw_at_zero == self.raw_at_hundred:
            raise ValidationError("percent raw endpoints must be distinct")
        for name in ("zero_label", "hundred_label"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValidationError(f"{name} must be a non-empty str")
        if self.zero_label == self.hundred_label:
            raise ValidationError("percent endpoint labels must be distinct")
        if not isinstance(self.confidence, Confidence):
            raise ValidationError("confidence must be Confidence")
        if self.confidence in {Confidence.CONFLICTING, Confidence.UNKNOWN}:
            raise ValidationError(
                "a usable percent mapping requires non-conflicting evidence"
            )
        if not isinstance(self.quantization, Quantization):
            raise ValidationError("quantization must be Quantization")


@dataclass(frozen=True, slots=True)
class PhysicalAngleMapping:
    """Linear raw-to-radian calibration for one joint axis."""

    raw_at_first: RawNumber
    raw_at_second: RawNumber
    radians_at_first: float
    radians_at_second: float
    confidence: Confidence

    def __post_init__(self) -> None:
        values = (
            self.raw_at_first,
            self.raw_at_second,
            self.radians_at_first,
            self.radians_at_second,
        )
        if any(not is_finite_number(value) for value in values):
            raise ValidationError("physical angle endpoints must be finite")
        if self.raw_at_first == self.raw_at_second:
            raise ValidationError("physical angle raw endpoints must be distinct")
        if not isinstance(self.confidence, Confidence):
            raise ValidationError("confidence must be Confidence")
        if self.confidence in {Confidence.CONFLICTING, Confidence.UNKNOWN}:
            raise ValidationError(
                "a usable physical mapping requires non-conflicting evidence"
            )


__all__ = [
    "NumericRange",
    "PercentMapping",
    "PhysicalAngleMapping",
    "is_finite_number",
]
