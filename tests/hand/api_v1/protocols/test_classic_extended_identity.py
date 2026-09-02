"""L20Lite/L20 classic CAN identity-profile tests."""

from __future__ import annotations

import can
import pytest

from linkerbot_hand.api import HandSide
from linkerbot_hand.api.errors import IdentityMismatchError
from linkerbot_hand.capabilities.registry import get_declared_capabilities
from linkerbot_hand.protocols import IdentityResult
from linkerbot_hand.protocols.common.classic_can import classic_route
from linkerbot_hand.protocols.l20 import l20_identity_transaction
from linkerbot_hand.protocols.l20lite import l20lite_identity_transaction


def _message(side: HandSide, data: bytes) -> can.Message:
    return can.Message(
        arbitration_id=classic_route(side).arbitration_id,
        data=data,
        is_extended_id=False,
    )


def _frames(side: HandSide, product_code: str) -> tuple[can.Message, ...]:
    encoded = product_code.encode("ascii")
    serial = tuple(
        _message(side, bytes((0xC0, offset // 6)) + encoded[offset : offset + 6])
        for offset in range(0, len(encoded), 6)
    )
    return serial + (
        _message(side, b"\xc1\x04\x00\x01"),
        _message(side, b"\xc2\x03\x00\x02"),
        _message(side, b"\xc4\x01\x00\x04"),
    )


@pytest.mark.parametrize(
    ("model", "side", "product_code", "factory"),
    (
        (
            "l20lite",
            HandSide.LEFT,
            "LHT10-07-098-L-A-1-C",
            l20lite_identity_transaction,
        ),
        ("l20", HandSide.RIGHT, "LHG20-010-001-R-Z-1-A", l20_identity_transaction),
    ),
)
def test_extended_classic_identity_verifies_all_common_fields(
    model: str,
    side: HandSide,
    product_code: str,
    factory,
) -> None:
    capabilities = get_declared_capabilities(model, side=side)
    transaction = factory(capabilities)
    progress = None

    for frame in reversed(_frames(side, product_code)):
        progress = transaction.consume(frame)

    assert progress is not None and progress.complete
    assert isinstance(progress.result, IdentityResult)
    result = progress.result
    assert result.capabilities.identity.verified
    assert result.capabilities.identity.firmware == "3.0.2"
    assert result.device_info.product_code == product_code
    assert result.device_info.serial_number in {"098", "001"}
    assert result.device_info.pcb_version is not None
    assert result.device_info.mechanical_version is not None
    assert result.fingerprint == product_code


@pytest.mark.parametrize("factory_prefix", ("L20", "L25", "LHL25", "LHG20"))
def test_l20_identity_accepts_historical_factory_prefixes_and_reports_l20(
    factory_prefix: str,
) -> None:
    product_code = f"{factory_prefix}-010-001-L-Z-1-A"
    transaction = l20_identity_transaction(
        get_declared_capabilities("l20", side=HandSide.LEFT)
    )

    progress = None
    for frame in reversed(_frames(HandSide.LEFT, product_code)):
        progress = transaction.consume(frame)

    assert progress is not None and progress.complete
    assert isinstance(progress.result, IdentityResult)
    assert progress.result.capabilities.model == "L20"
    assert progress.result.device_info.model == "L20"
    assert progress.result.device_info.product_code == product_code


def test_l20_identity_rejects_a_different_factory_family() -> None:
    transaction = l20_identity_transaction(
        get_declared_capabilities("l20", side="left")
    )

    with pytest.raises(IdentityMismatchError, match="factory model"):
        for frame in _frames(HandSide.LEFT, "LHO6-04-001-L-B-1-A"):
            transaction.consume(frame)
