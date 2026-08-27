"""Classic CAN frame and L6/O6 identity-profile contract tests."""

from __future__ import annotations

import can
import pytest

from linkerbot_hand.api import HandSide
from linkerbot_hand.api.errors import (
    IdentityMismatchError,
    ProtocolError,
    UnsupportedFirmwareError,
)
from linkerbot_hand.capabilities.registry import get_declared_capabilities
from linkerbot_hand.capabilities.types import TelemetrySemantic
from linkerbot_hand.protocols import IdentityResult
from linkerbot_hand.protocols.common.classic_can import (
    ClassicIdentityCollector,
    classic_route,
    decode_u8_vector,
)
from linkerbot_hand.protocols.l6 import l6_identity_transaction
from linkerbot_hand.protocols.o6 import o6_identity_transaction


def _message(side: HandSide, data: bytes) -> can.Message:
    return can.Message(
        arbitration_id=classic_route(side).arbitration_id,
        data=data,
        is_extended_id=False,
    )


def _identity_frames(
    side: HandSide,
    product_code: bytes,
    *,
    l6: bool,
    markers: tuple[int, ...] = (0, 1, 2, 3),
    firmware: tuple[int, int, int] = (2, 4, 0),
) -> tuple[can.Message, ...]:
    fragments = tuple(
        product_code[index : index + 6] for index in range(0, len(product_code), 6)
    )
    serial = tuple(
        _message(side, bytes((0xC0, marker)) + fragment)
        for marker, fragment in zip(markers, fragments)
    )
    pcb = b"\xc1\x01\x04\x00\x01" if l6 else b"\xc1\x04\x00\x01"
    return serial + (
        _message(side, pcb),
        _message(side, bytes((0xC2, *firmware))),
        _message(side, b"\xc4\x01\x00\x02"),
    )


def _consume_identity(transaction, frames: tuple[can.Message, ...]) -> IdentityResult:
    progress = None
    for frame in frames:
        progress = transaction.consume(frame)
    assert progress is not None and progress.complete
    assert isinstance(progress.result, IdentityResult)
    return progress.result


def test_classic_routes_and_u8_vectors_are_exact() -> None:
    left = classic_route(HandSide.LEFT)
    right = classic_route(HandSide.RIGHT)

    assert left.arbitration_id == 0x28
    assert right.arbitration_id == 0x27
    assert decode_u8_vector(
        _message(HandSide.LEFT, b"\x01\x00\x01\x02\x03\x04\xff"),
        route=left,
        opcode=0x01,
        count=6,
    ) == (0, 1, 2, 3, 4, 255)
    assert (
        decode_u8_vector(
            _message(HandSide.RIGHT, b"\x01\x00\x01\x02\x03\x04\xff"),
            route=left,
            opcode=0x01,
            count=6,
        )
        is None
    )
    with pytest.raises(ProtocolError, match="7 bytes"):
        decode_u8_vector(
            _message(HandSide.LEFT, b"\x01\x00"),
            route=left,
            opcode=0x01,
            count=6,
        )


@pytest.mark.parametrize("markers", ((0, 1, 2, 3), (0, 6, 12, 18)))
def test_identity_collector_accepts_frame_indices_and_byte_offsets(
    markers: tuple[int, ...],
) -> None:
    collector = ClassicIdentityCollector(
        classic_route(HandSide.RIGHT),
        l6_pcb_format=False,
    )
    frames = _identity_frames(
        HandSide.RIGHT,
        b"LHO6-04.1-730-R-B-1-D",
        l6=False,
        markers=markers,
    )

    for frame in reversed(frames[:4]):
        assert collector.offer(frame)
    for frame in frames[4:]:
        assert collector.offer(frame)

    assert collector.complete
    assert collector.product_code() == "LHO6-04.1-730-R-B-1-D"


def test_identity_collector_rejects_conflicting_duplicate_fragment() -> None:
    collector = ClassicIdentityCollector(
        classic_route(HandSide.LEFT),
        l6_pcb_format=True,
    )
    assert collector.offer(_message(HandSide.LEFT, b"\xc0\x00ABCDEF"))
    with pytest.raises(ProtocolError, match="conflicts"):
        collector.offer(_message(HandSide.LEFT, b"\xc0\x00UVWXYZ"))


def test_l6_identity_verifies_versions_and_profile() -> None:
    declared = get_declared_capabilities("l6", side="left")
    transaction = l6_identity_transaction(declared)

    result = _consume_identity(
        transaction,
        _identity_frames(
            HandSide.LEFT,
            b"LHL6-04-077-L-B-1-A",
            l6=True,
        ),
    )

    assert result.capabilities.identity.verified
    assert result.capabilities.identity.firmware == "2.4.0"
    assert result.device_info.serial_number == "077"
    assert result.device_info.product_code == "LHL6-04-077-L-B-1-A"
    assert result.fingerprint == "LHL6-04-077-L-B-1-A"


def test_l6_identity_rejects_unsupported_firmware() -> None:
    transaction = l6_identity_transaction(get_declared_capabilities("l6", side="left"))
    frames = _identity_frames(
        HandSide.LEFT,
        b"LHL6-03-077-L-B-1-A",
        l6=True,
        firmware=(2, 3, 3),
    )

    with pytest.raises(UnsupportedFirmwareError, match="2.4.0"):
        for frame in frames:
            transaction.consume(frame)


def test_o6_04_b_selects_complete_tactile_profile() -> None:
    declared = get_declared_capabilities("o6", side="right")
    transaction = o6_identity_transaction(declared)

    result = _consume_identity(
        transaction,
        _identity_frames(
            HandSide.RIGHT,
            b"LHO6-04.1-730-R-B-1-D",
            l6=False,
            firmware=(3, 1, 2),
        ),
    )

    force = result.capabilities.telemetry_channel(TelemetrySemantic.FORCE)
    assert force is not None
    assert force.shape == (5, 10, 4)
    assert result.device_info.serial_number == "730"
    assert result.device_info.sensor_variant == "B"
    assert result.capabilities.identity.sensor_variant == "B"


def test_o6_non_b_variant_does_not_advertise_tactile() -> None:
    transaction = o6_identity_transaction(get_declared_capabilities("o6", side="right"))

    result = _consume_identity(
        transaction,
        _identity_frames(
            HandSide.RIGHT,
            b"LHO6-04.1-731-R-Z-1-D",
            l6=False,
            firmware=(3, 1, 2),
        ),
    )

    assert result.capabilities.telemetry_channel(TelemetrySemantic.FORCE) is None


def test_factory_side_mismatch_is_rejected() -> None:
    transaction = o6_identity_transaction(get_declared_capabilities("o6", side="left"))
    frames = _identity_frames(
        HandSide.LEFT,
        b"LHO6-04.1-730-R-B-1-D",
        l6=False,
        firmware=(3, 1, 2),
    )

    with pytest.raises(IdentityMismatchError, match="side"):
        for frame in frames:
            transaction.consume(frame)
