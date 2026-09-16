"""Pure profile selection for Hand SDK capability declarations."""

from __future__ import annotations

from collections.abc import Callable

from ..api.enums import HandSide
from ..api.errors import ValidationError
from .models import (
    l6_capabilities,
    l20_capabilities,
    l20lite_capabilities,
    l30_capabilities,
    o6_capabilities,
    o20_capabilities,
    o30_capabilities,
)
from .types import DeviceCapabilities

SUPPORTED_MODELS = ("L6", "O6", "L20Lite", "L20", "O20", "L30", "O30")

# Public model spellings only. Historical product-code prefixes are handled by
# each protocol's identity parser, not accepted as configuration aliases.
_ALIASES = {
    "l6": "L6",
    "o6": "O6",
    "l20lite": "L20Lite",
    "l20-lite": "L20Lite",
    "l20": "L20",
    "o20": "O20",
    "l30": "L30",
    "o30": "O30",
}

_PROFILE_FACTORIES: dict[str, Callable[[HandSide], DeviceCapabilities]] = {
    "L6": l6_capabilities,
    "O6": o6_capabilities,
    "L20Lite": l20lite_capabilities,
    "L20": l20_capabilities,
    "O20": o20_capabilities,
    "L30": l30_capabilities,
    "O30": o30_capabilities,
}


def get_declared_capabilities(
    model: str,
    *,
    side: HandSide | str | None = None,
) -> DeviceCapabilities:
    """Return zero-I/O capability metadata for a configured model.

    Args:
        model: Case-insensitive public model name, or ``l20-lite`` for L20Lite.
            Historical product-code prefixes are not configuration aliases.
        side: ``left`` or ``right`` for sided models. L30 requires omission.

    Returns:
        An immutable, unverified declared profile.

    Raises:
        ValidationError: If model or side does not identify a valid endpoint.
    """

    canonical = _canonical_model(model)
    if canonical == "L30":
        if side is not None:
            raise ValidationError("L30 must not specify side")
        resolved_side = HandSide.NOT_APPLICABLE
    else:
        if side is None:
            raise ValidationError(f"side is required for {canonical}")
        if isinstance(side, str):
            try:
                side = HandSide(side.lower())
            except ValueError as error:
                raise ValidationError("side must be left or right") from error
        if side not in {HandSide.LEFT, HandSide.RIGHT}:
            raise ValidationError("side must be left or right")
        resolved_side = side
    return _PROFILE_FACTORIES[canonical](resolved_side)


def _canonical_model(model: object) -> str:
    if not isinstance(model, str):
        raise ValidationError("model must be str")
    canonical = _ALIASES.get(model.lower())
    if canonical is None:
        raise ValidationError(f"unsupported hand model {model!r}")
    return canonical


__all__ = [
    "SUPPORTED_MODELS",
    "get_declared_capabilities",
]
