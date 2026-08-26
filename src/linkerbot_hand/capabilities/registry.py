"""Explicit registry for built-in hand model capabilities."""

from __future__ import annotations

from dataclasses import replace

from ..contracts import DeviceCapabilities, DeviceIdentity, HandSide, TransportType
from ..errors import ValidationError
from .models import (
    L6_CAPABILITIES,
    L20LITE_CAPABILITIES,
    L25_CAPABILITIES,
    L30_CAPABILITIES,
    O6_CAPABILITIES,
    O20_CAPABILITIES,
    O30I_CAPABILITIES,
)
from .models.o6 import specialize_capabilities as specialize_o6_capabilities

MODEL_CAPABILITIES: dict[str, DeviceCapabilities] = {
    capabilities.model: capabilities
    for capabilities in (
        L6_CAPABILITIES,
        O6_CAPABILITIES,
        L20LITE_CAPABILITIES,
        L25_CAPABILITIES,
        L30_CAPABILITIES,
        O20_CAPABILITIES,
        O30I_CAPABILITIES,
    )
}

_MODEL_ALIASES = {
    "l6": "L6",
    "o6": "O6",
    "l20lite": "L20Lite",
    "l20-lite": "L20Lite",
    "l25": "L25",
    "l30": "L30",
    "o20": "O20",
    "o30i": "O30i",
}


def get_model_capabilities(
    model: str,
    *,
    side: HandSide | str = HandSide.UNKNOWN,
    firmware: str | None = None,
    transport: TransportType | None = None,
) -> DeviceCapabilities:
    """Return the SDK source of truth for one model without device I/O.

    Args:
        model: Canonical model name or supported case-insensitive alias.
        side: Requested hand side as an enum or string.
        firmware: Optional firmware identity to attach to the result.
        transport: Optional transport override for custom integration.

    Returns:
        Immutable capabilities specialized for the requested identity.

    Raises:
        ValidationError: If model, side, firmware, or transport is invalid.
    """
    if not isinstance(model, str):
        raise ValidationError("model must be str")
    canonical = _MODEL_ALIASES.get(model.lower())
    if canonical is None:
        raise ValidationError(f"unsupported hand model {model!r}")
    if isinstance(side, str):
        try:
            side = HandSide(side.lower())
        except ValueError as error:
            raise ValidationError("side must be left, right, or unknown") from error
    if not isinstance(side, HandSide):
        raise ValidationError("side must be HandSide or str")
    if firmware is not None and not isinstance(firmware, str):
        raise ValidationError("firmware must be str or None")
    if transport is not None and not isinstance(transport, TransportType):
        raise ValidationError("transport must be TransportType or None")
    capabilities = MODEL_CAPABILITIES[canonical].with_side(side)
    return replace(
        capabilities,
        firmware=firmware,
        transport=capabilities.transport if transport is None else transport,
    )


def specialize_model_capabilities(
    capabilities: DeviceCapabilities,
    identity: DeviceIdentity,
) -> DeviceCapabilities:
    """Apply model-owned hardware variants to detected device capabilities."""
    specialized = capabilities.with_identity(identity)
    if specialized.model == "O6":
        return specialize_o6_capabilities(
            specialized,
            identity.capability_variant,
        )
    return specialized


__all__ = [
    "MODEL_CAPABILITIES",
    "get_model_capabilities",
    "specialize_model_capabilities",
]
