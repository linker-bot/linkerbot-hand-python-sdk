"""Production protocol-profile registry tests."""

from __future__ import annotations

import pytest

from linkerbot_hand.api import HandSide, UnsupportedOperationError, ValidationError
from linkerbot_hand.capabilities.registry import get_declared_capabilities
from linkerbot_hand.protocols.l6 import L6ProtocolBackend
from linkerbot_hand.protocols.o6 import O6ProtocolBackend
from linkerbot_hand.protocols.registry import create_protocol_backend
from linkerbot_transport import CANRouteKey


@pytest.mark.parametrize(
    ("model", "side", "backend_type", "arbitration_id"),
    (
        ("l6", "left", L6ProtocolBackend, 0x28),
        ("l6", "right", L6ProtocolBackend, 0x27),
        ("o6", "left", O6ProtocolBackend, 0x28),
        ("o6", "right", O6ProtocolBackend, 0x27),
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
    assert backend.route_key == endpoint


@pytest.mark.parametrize(
    ("model", "side"),
    (
        ("l20lite", HandSide.LEFT),
        ("l25", HandSide.RIGHT),
        ("o20", HandSide.LEFT),
        ("l30", None),
        ("o30i", HandSide.RIGHT),
    ),
)
def test_registry_rejects_unmigrated_models_with_stable_error(
    model: str,
    side: HandSide | None,
) -> None:
    capabilities = get_declared_capabilities(model, side=side)

    with pytest.raises(UnsupportedOperationError, match="not available") as raised:
        create_protocol_backend(capabilities)

    assert raised.value.operation == "open"
    assert raised.value.model == capabilities.model


def test_registry_requires_capability_contract() -> None:
    with pytest.raises(ValidationError, match="DeviceCapabilities"):
        create_protocol_backend(object())  # ty: ignore[invalid-argument-type]
