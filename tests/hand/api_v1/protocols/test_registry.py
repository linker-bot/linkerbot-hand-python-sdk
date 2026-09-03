"""Production protocol-profile registry tests."""

from __future__ import annotations

import pytest

from linkerbot_hand.api import HandSide, ValidationError
from linkerbot_hand.capabilities.registry import get_declared_capabilities
from linkerbot_hand.protocols.l6 import L6ProtocolBackend
from linkerbot_hand.protocols.l20 import L20ProtocolBackend
from linkerbot_hand.protocols.l20lite import L20LiteProtocolBackend
from linkerbot_hand.protocols.l30 import L30ProtocolBackend
from linkerbot_hand.protocols.o6 import O6ProtocolBackend
from linkerbot_hand.protocols.o20 import O20ProtocolBackend
from linkerbot_hand.protocols.o30 import O30ProtocolBackend
from linkerbot_hand.protocols.registry import create_protocol_backend
from linkerbot_transport import CANRouteKey


@pytest.mark.parametrize(
    ("model", "side", "backend_type", "arbitration_id"),
    (
        ("l6", "left", L6ProtocolBackend, 0x28),
        ("l6", "right", L6ProtocolBackend, 0x27),
        ("o6", "left", O6ProtocolBackend, 0x28),
        ("o6", "right", O6ProtocolBackend, 0x27),
        ("l20lite", "left", L20LiteProtocolBackend, 0x28),
        ("l20lite", "right", L20LiteProtocolBackend, 0x27),
        ("l20", "left", L20ProtocolBackend, 0x28),
        ("l20", "right", L20ProtocolBackend, 0x27),
    ),
)
def test_registry_selects_model_and_side_without_io(
    model: str,
    side: str,
    backend_type: type,
    arbitration_id: int,
) -> None:
    capabilities = get_declared_capabilities(model, side=side)

    backend = create_protocol_backend(capabilities)

    assert isinstance(backend, backend_type)
    endpoint = backend.endpoint_id
    assert isinstance(endpoint, CANRouteKey)
    assert endpoint.arbitration_id == arbitration_id
    assert backend.route_keys == (endpoint,)


def test_registry_selects_o20_can_fd_routes_without_io() -> None:
    capabilities = get_declared_capabilities("o20", side="right")

    backend = create_protocol_backend(capabilities)

    assert isinstance(backend, O20ProtocolBackend)
    assert backend.endpoint_id == ("o20", 1)
    assert len(backend.route_keys) == 18
    assert len(set(backend.route_keys)) == len(backend.route_keys)
    assert all(route.is_extended_id for route in backend.route_keys)


def test_registry_selects_o30_standard_can_fd_route_without_io() -> None:
    capabilities = get_declared_capabilities("o30", side=HandSide.RIGHT)

    backend = create_protocol_backend(capabilities)

    assert isinstance(backend, O30ProtocolBackend)
    assert backend.endpoint_id == ("o30", 0x001, 0x401)
    assert backend.route_keys == (CANRouteKey(0x401, False),)


def test_registry_selects_side_neutral_l30_can_fd_routes_without_io() -> None:
    capabilities = get_declared_capabilities("l30")

    backend = create_protocol_backend(capabilities)

    assert isinstance(backend, L30ProtocolBackend)
    assert backend.endpoint_id == ("l30", 1, 0)
    assert len(backend.route_keys) == 16
    assert len(set(backend.route_keys)) == len(backend.route_keys)
    assert all(route.is_extended_id for route in backend.route_keys)


def test_registry_requires_capability_contract() -> None:
    with pytest.raises(ValidationError, match="DeviceCapabilities"):
        create_protocol_backend(object())  # ty: ignore[invalid-argument-type]
