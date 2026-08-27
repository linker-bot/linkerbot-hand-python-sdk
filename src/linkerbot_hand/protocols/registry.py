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
from .l30 import L30ProtocolBackend
from .o6 import O6ProtocolBackend
from .o20 import O20ProtocolBackend
from .o30i import O30iProtocolBackend

ProtocolBackendFactory = Callable[[HandSide], ProtocolBackend]

_BACKEND_FACTORIES: dict[str, ProtocolBackendFactory] = {
    "L6": L6ProtocolBackend,
    "O6": O6ProtocolBackend,
    "L20Lite": L20LiteProtocolBackend,
    "L25": L25ProtocolBackend,
    "O20": O20ProtocolBackend,
    "L30": L30ProtocolBackend,
    "O30i": O30iProtocolBackend,
}

_TRANSPORT_KINDS = {
    "L6": TransportKind.CAN,
    "O6": TransportKind.CAN,
    "L20Lite": TransportKind.CAN,
    "L25": TransportKind.CAN,
    "O20": TransportKind.CAN_FD,
    "L30": TransportKind.CAN_FD,
    "O30i": TransportKind.CAN_FD,
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
    expected_transport = _TRANSPORT_KINDS[capabilities.model]
    if capabilities.transport.kind is not expected_transport:
        raise ValidationError(
            f"{capabilities.model} protocol backend requires "
            f"{expected_transport.value} capabilities"
        )
    valid_sides = (
        {HandSide.NOT_APPLICABLE}
        if capabilities.model == "L30"
        else {HandSide.LEFT, HandSide.RIGHT}
    )
    if capabilities.side not in valid_sides:
        raise ValidationError(
            f"{capabilities.model} protocol backend has an invalid side"
        )
    return factory(capabilities.side)


__all__ = ["create_protocol_backend"]
