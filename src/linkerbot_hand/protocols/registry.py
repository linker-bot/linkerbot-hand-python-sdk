"""Pure model protocol-profile selection for the Hand SDK runtime."""

from __future__ import annotations

from collections.abc import Callable

from ..api.enums import HandSide, TransportKind
from ..api.errors import UnsupportedOperationError, ValidationError
from ..capabilities.types import DeviceCapabilities
from .base import ProtocolBackend
from .l6 import L6ProtocolBackend
from .l20lite import L20LiteProtocolBackend
from .l25 import L25ProtocolBackend
from .o6 import O6ProtocolBackend

ProtocolBackendFactory = Callable[[HandSide], ProtocolBackend]

_BACKEND_FACTORIES: dict[str, ProtocolBackendFactory] = {
    "L6": L6ProtocolBackend,
    "O6": O6ProtocolBackend,
    "L20Lite": L20LiteProtocolBackend,
    "L25": L25ProtocolBackend,
}


def create_protocol_backend(capabilities: DeviceCapabilities) -> ProtocolBackend:
    """Create the stateless backend declared by one capability profile.

    Args:
        capabilities: Declared capabilities selected from the public model and side.

    Returns:
        A stateless protocol transaction factory for the configured endpoint.

    Raises:
        ValidationError: If the profile cannot describe a classic CAN endpoint.
        UnsupportedOperationError: If the model protocol has not been migrated.
    """

    if not isinstance(capabilities, DeviceCapabilities):
        raise ValidationError("capabilities must be DeviceCapabilities")
    try:
        factory = _BACKEND_FACTORIES[capabilities.model]
    except KeyError as error:
        raise UnsupportedOperationError(
            f"{capabilities.model} protocol profile is not available in this release",
            operation="open",
            model=capabilities.model,
        ) from error
    if capabilities.transport.kind is not TransportKind.CAN:
        raise ValidationError(
            f"{capabilities.model} protocol backend requires classic CAN capabilities"
        )
    if capabilities.side not in {HandSide.LEFT, HandSide.RIGHT}:
        raise ValidationError(
            f"{capabilities.model} protocol backend requires a left or right side"
        )
    return factory(capabilities.side)


__all__ = ["create_protocol_backend"]
