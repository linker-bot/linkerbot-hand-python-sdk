"""Pure profile selection for new and transitional Hand SDK contracts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from ..api.enums import HandSide
from ..api.errors import ValidationError as APIValidationError
from ..contracts import (
    DeviceCapabilities as LegacyDeviceCapabilities,
)
from ..contracts import (
    DeviceIdentity as LegacyDeviceIdentity,
)
from ..contracts import (
    HandSide as LegacyHandSide,
)
from ..contracts import (
    SensorType,
    TransportType,
)
from ..errors import ValidationError as LegacyValidationError
from ._legacy_adapter import to_legacy_capabilities
from .models import (
    l6_capabilities,
    l20lite_capabilities,
    l25_capabilities,
    l30_capabilities,
    o6_capabilities,
    o20_capabilities,
    o30i_capabilities,
)
from .types import DeviceCapabilities

SUPPORTED_MODELS = ("L6", "O6", "L20Lite", "L25", "O20", "L30", "O30i")

_ALIASES = {
    "l6": "L6",
    "o6": "O6",
    "l20lite": "L20Lite",
    "l20-lite": "L20Lite",
    "l25": "L25",
    "o20": "O20",
    "l30": "L30",
    "o30i": "O30i",
}

_PROFILE_FACTORIES: dict[str, Callable[[HandSide], DeviceCapabilities]] = {
    "L6": l6_capabilities,
    "O6": o6_capabilities,
    "L20Lite": l20lite_capabilities,
    "L25": l25_capabilities,
    "O20": o20_capabilities,
    "L30": l30_capabilities,
    "O30i": o30i_capabilities,
}


def get_declared_capabilities(
    model: str,
    *,
    side: HandSide | str | None = None,
) -> DeviceCapabilities:
    """Return zero-I/O capability metadata for a configured model.

    Args:
        model: Canonical name or supported case-insensitive alias.
        side: ``left`` or ``right`` for sided models. L30 requires omission.

    Returns:
        An immutable, unverified declared profile.

    Raises:
        ValidationError: If model or side does not identify a valid endpoint.
    """

    canonical = _canonical_model(model, APIValidationError)
    if canonical == "L30":
        if side is not None:
            raise APIValidationError("L30 must not specify side")
        resolved_side = HandSide.NOT_APPLICABLE
    else:
        if side is None:
            raise APIValidationError(f"side is required for {canonical}")
        if isinstance(side, str):
            try:
                side = HandSide(side.lower())
            except ValueError as error:
                raise APIValidationError("side must be left or right") from error
        if side not in {HandSide.LEFT, HandSide.RIGHT}:
            raise APIValidationError("side must be left or right")
        resolved_side = side
    return _PROFILE_FACTORIES[canonical](resolved_side)


# The old runtime reads a generated compatibility view until issue #48 removes
# it. Model values still have one source: the 1.0 profile factories above.
MODEL_CAPABILITIES: dict[str, LegacyDeviceCapabilities] = {
    model: to_legacy_capabilities(
        factory(HandSide.NOT_APPLICABLE if model == "L30" else HandSide.LEFT)
    )
    for model, factory in _PROFILE_FACTORIES.items()
}


def get_model_capabilities(
    model: str,
    *,
    side: LegacyHandSide | str = LegacyHandSide.UNKNOWN,
    firmware: str | None = None,
    transport: TransportType | None = None,
) -> LegacyDeviceCapabilities:
    """Return the generated pre-1.0 view used by the transitional runtime."""

    canonical = _canonical_model(model, LegacyValidationError)
    if isinstance(side, str):
        try:
            side = LegacyHandSide(side.lower())
        except ValueError as error:
            raise LegacyValidationError(
                "side must be left, right, or unknown"
            ) from error
    if not isinstance(side, LegacyHandSide):
        raise LegacyValidationError("side must be HandSide or str")
    if firmware is not None and not isinstance(firmware, str):
        raise LegacyValidationError("firmware must be str or None")
    if transport is not None and not isinstance(transport, TransportType):
        raise LegacyValidationError("transport must be TransportType or None")
    capabilities = MODEL_CAPABILITIES[canonical].with_side(side)
    return replace(
        capabilities,
        firmware=firmware,
        transport=capabilities.transport if transport is None else transport,
    )


def specialize_model_capabilities(
    capabilities: LegacyDeviceCapabilities,
    identity: LegacyDeviceIdentity,
) -> LegacyDeviceCapabilities:
    """Apply identity-dependent variants to the transitional runtime view."""

    specialized = capabilities.with_identity(identity)
    if specialized.model != "O6":
        return specialized
    variant = identity.capability_variant
    if variant is None or not variant.startswith("04-") or variant == "04-B":
        return specialized
    return replace(
        specialized,
        sensors=tuple(
            value
            for value in specialized.sensors
            if value.sensor is not SensorType.FORCE
        ),
    )


def _canonical_model(model: object, error_type: type[Exception]) -> str:
    if not isinstance(model, str):
        raise error_type("model must be str")
    canonical = _ALIASES.get(model.lower())
    if canonical is None:
        raise error_type(f"unsupported hand model {model!r}")
    return canonical


__all__ = [
    "MODEL_CAPABILITIES",
    "SUPPORTED_MODELS",
    "get_declared_capabilities",
    "get_model_capabilities",
    "specialize_model_capabilities",
]
