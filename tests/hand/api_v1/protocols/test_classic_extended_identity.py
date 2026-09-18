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


@pytest.mark.parametrize(
    "product_code",
    (
        "L20-010-001-L-Z-1-A",
        "LHT20-10-499-L-B-1-D",
        "L25-010-001-L-Z-1-A",
        "LHL25-010-001-L-Z-1-A",
        "LHG20-010-001-L-Z-1-A",
    ),
)
def test_l20_identity_accepts_known_factory_product_codes_and_reports_l20(
    product_code: str,
) -> None:
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


@pytest.mark.parametrize("reversed_frames", (False, True))
def test_l20lite_accepts_captured_18_byte_identity_without_terminator(
    reversed_frames: bool,
) -> None:
    transaction = l20lite_identity_transaction(
        get_declared_capabilities("l20lite", side="left")
    )
    # Captured on can3: firmware omits a fourth C0 fragment or NUL when the
    # seven-field factory code ends exactly at the third six-byte boundary.
    payloads = [
        bytes.fromhex("c0 00 4c 48 54 31 30 2d"),
        bytes.fromhex("c0 06 30 37 2d 30 2d 4c"),
        bytes.fromhex("c0 0c 2d 42 2d 31 2d 41"),
        b"\xc1\x01\x01\x01",
        b"\xc2\x04\x06\x0a",
        b"\xc4\x00\x00\x07",
    ]
    if reversed_frames:
        payloads.reverse()
    for data in payloads:
        progress = transaction.consume(_message(HandSide.LEFT, data))
    assert progress.complete
    assert isinstance(progress.result, IdentityResult)
    assert progress.result.device_info.product_code == "LHT10-07-0-L-B-1-A"
    assert progress.result.device_info.raw_identity == b"LHT10-07-0-L-B-1-A"


def test_l20lite_does_not_treat_a_truncated_long_factory_code_as_complete() -> None:
    transaction = l20lite_identity_transaction(
        get_declared_capabilities("l20lite", side="left")
    )
    frames = _frames(HandSide.LEFT, "LHT10-07-123-L-B-1-A")
    for frame in frames:
        if frame.data[0] == 0xC0 and frame.data[1] == 3:
            terminal = frame
        else:
            assert not transaction.consume(frame).complete
    assert transaction.consume(terminal).complete
