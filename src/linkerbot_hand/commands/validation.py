"""Pure command normalization and capability validation."""

from __future__ import annotations

import math
from collections.abc import Iterable

from ..contracts import CommandKind, DeviceCapabilities, NumericRange
from ..errors import UnsupportedOperationError, ValidationError
from .model import HandCommand


def coerce_values(values: Iterable[float | int]) -> tuple[float | int, ...]:
    """Materialize command values without changing their numeric types.

    Args:
        values: Iterable of ordered joint values.

    Returns:
        Values stored in an immutable tuple.

    Raises:
        ValidationError: If ``values`` is not iterable.
    """
    try:
        return tuple(values)
    except TypeError as exc:
        raise ValidationError("command values must be iterable", cause=exc) from exc


def finite_number(value: float | int, label: str) -> float:
    """Validate and convert one command value to a finite float.

    Args:
        value: Candidate numeric value. Booleans are rejected.
        label: Field label included in validation errors.

    Returns:
        Finite floating-point representation of ``value``.

    Raises:
        ValidationError: If the value is non-numeric or non-finite.
    """
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValidationError(f"{label} must be numeric")
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValidationError(f"{label} must be finite", cause=exc) from exc
    if not math.isfinite(numeric):
        raise ValidationError(f"{label} must be finite")
    return numeric


def validate_command(
    command: HandCommand,
    capabilities: DeviceCapabilities,
) -> HandCommand:
    """Validate a command against device capabilities.

    Args:
        command: Candidate command.
        capabilities: Active model and side capability description.

    Returns:
        An immutable command whose values have been materialized as a tuple.

    Raises:
        ValidationError: If kind, value count, type, range, or step is invalid.
        UnsupportedOperationError: If the device lacks the requested operation.
    """
    if not isinstance(command, HandCommand):
        raise ValidationError("command must be a HandCommand")
    if not isinstance(command.kind, CommandKind):
        raise ValidationError("command kind is invalid")
    values = coerce_values(command.values)
    command = HandCommand(command.kind, values)

    if command.kind.is_continuous:
        expected = capabilities.joint_count
        if len(values) != expected:
            raise ValidationError(f"expected {expected} joint values")
        if command.kind is CommandKind.ANGLES:
            if any(joint.raw_angle_range is None for joint in capabilities.joints):
                raise UnsupportedOperationError(
                    "angle control is not supported for this hand side"
                )
            ranges = tuple(
                joint.raw_angle_range
                for joint in capabilities.joints
                if joint.raw_angle_range is not None
            )
            _validate_ranges(values, ranges, "angle")
        elif command.kind is CommandKind.SPEEDS:
            if any(joint.speed_range is None for joint in capabilities.joints):
                raise UnsupportedOperationError("speed control is not supported")
            ranges = tuple(
                joint.speed_range
                for joint in capabilities.joints
                if joint.speed_range is not None
            )
            _validate_ranges(values, ranges, "speed")
        elif command.kind is CommandKind.TORQUES:
            if any(joint.torque_range is None for joint in capabilities.joints):
                raise UnsupportedOperationError("torque control is not supported")
            ranges = tuple(
                joint.torque_range
                for joint in capabilities.joints
                if joint.torque_range is not None
            )
            _validate_ranges(values, ranges, "torque")
        return command

    if values:
        raise ValidationError("discrete commands do not accept values")
    features = capabilities.features
    supported = {
        CommandKind.CLEAR_FAULTS: features.clear_faults,
        CommandKind.ENABLE: features.enable,
        CommandKind.DISABLE: features.enable,
        CommandKind.CALIBRATE: features.calibration,
    }[command.kind]
    if not supported:
        raise UnsupportedOperationError(
            f"{command.kind.value} is not supported by this device"
        )
    return command


def _validate_ranges(
    values: tuple[float | int, ...],
    ranges: tuple[NumericRange, ...],
    label: str,
) -> None:
    for index, (value, bounds) in enumerate(zip(values, ranges)):
        numeric = finite_number(value, f"{label}[{index}]")
        if bounds.integer_only and type(value) is not int:
            raise ValidationError(f"{label}[{index}] must be int")
        if not bounds.minimum <= numeric <= bounds.maximum:
            raise ValidationError(
                f"{label}[{index}] must be in [{bounds.minimum}, {bounds.maximum}]"
            )
        if bounds.step is not None:
            steps = (numeric - bounds.minimum) / bounds.step
            if not math.isclose(steps, round(steps), abs_tol=1e-9):
                raise ValidationError(f"{label}[{index}] must use step {bounds.step}")


__all__ = ["coerce_values", "finite_number", "validate_command"]
