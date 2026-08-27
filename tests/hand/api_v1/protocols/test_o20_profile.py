"""O20 CAN FD codec and transaction profile tests."""

from __future__ import annotations

from dataclasses import replace

import pytest

from linkerbot_hand.api import HandSide, IdentityMismatchError, ProtocolError
from linkerbot_hand.capabilities.registry import get_declared_capabilities
from linkerbot_hand.capabilities.types import CommandSemantic, TelemetrySemantic
from linkerbot_hand.protocols import IdentityResult, SamplePayload
from linkerbot_hand.protocols.o20 import O20ProtocolBackend
from linkerbot_hand.protocols.o20.codec import (
    JOINT_COUNT,
    REG_CONFIGURED_SPEEDS,
    REG_DEVICE_INFO,
    REG_EFFORT_LIMITS,
    REG_FAULTS,
    REG_TACTILE_FIRST,
    build_can_id,
    decode_i16_vector,
    encode_i16_vector,
    parse_can_id,
)
from linkerbot_transport.canfd import CANFDMessage


def _capabilities(side: str = "right"):
    return get_declared_capabilities("o20", side=side)


def _response(device_id: int, register: int, data: bytes) -> CANFDMessage:
    return CANFDMessage(
        arbitration_id=build_can_id(
            device_id=device_id,
            register=register,
            write=False,
        ),
        data=data,
        is_extended_id=True,
    )


def _identity_payload(*, side_code: int = 1, serial: bytes = b"SN-O20-0001") -> bytes:
    return (
        b"O20-R".ljust(10, b"\x00")
        + serial.ljust(20, b"\x00")
        + b"V2.1.0".ljust(10, b"\x00")
        + b"H1.3".ljust(10, b"\x00")
        + bytes((side_code,))
        + bytes(range(11))
    )


def test_o20_id_and_vector_codecs_preserve_the_wire_contract() -> None:
    arbitration_id = build_can_id(device_id=1, register=0x08, write=True)
    assert parse_can_id(arbitration_id) == (1, 0x08, True)

    values = tuple(range(-8, 8))
    payload = encode_i16_vector(values)

    assert len(payload) == 34
    assert payload[-2:] == b"\x00\x00"
    assert decode_i16_vector(payload + bytes(14)) == values
    with pytest.raises(ProtocolError, match="at least 34"):
        decode_i16_vector(payload[:-1])
    with pytest.raises(ProtocolError, match="reserved bits"):
        parse_can_id(arbitration_id | 1)


def test_o20_identity_verifies_side_and_uses_serial_and_uid_fingerprint() -> None:
    capabilities = _capabilities()
    backend = O20ProtocolBackend(HandSide.RIGHT)
    transaction = backend.identity_transaction(capabilities)

    progress = transaction.consume(
        _response(1, REG_DEVICE_INFO, _identity_payload() + b"\x00\x00")
    )

    assert progress.complete
    assert isinstance(progress.result, IdentityResult)
    result = progress.result
    assert result.capabilities.identity.verified
    assert result.capabilities.identity.firmware == "V2.1.0"
    assert result.capabilities.identity.hardware_revision == "H1.3"
    assert result.device_info.serial_number == "SN-O20-0001"
    assert result.device_info.pcb_version is not None
    assert result.device_info.pcb_version.text == "H1.3"
    assert result.fingerprint == (
        "O20-R",
        "right",
        "SN-O20-0001",
        bytes(range(11)),
    )

    mismatched = backend.identity_transaction(capabilities)
    with pytest.raises(IdentityMismatchError, match="side"):
        mismatched.consume(
            _response(1, REG_DEVICE_INFO, _identity_payload(side_code=2))
        )


def test_o20_commands_use_17_slots_and_never_claim_device_ack() -> None:
    capabilities = _capabilities()
    backend = O20ProtocolBackend(HandSide.RIGHT)

    speed = backend.command_transaction(
        CommandSemantic.SPEEDS,
        (100,) * JOINT_COUNT,
        capabilities,
    )
    speed_message = speed.request_messages[0]
    assert isinstance(speed_message, CANFDMessage)
    assert parse_can_id(speed_message.arbitration_id) == (
        1,
        REG_CONFIGURED_SPEEDS,
        True,
    )
    assert speed_message.data == b"\x64\x00" * JOINT_COUNT + b"\x00\x00"
    assert not speed.expects_response

    effort = backend.command_transaction(
        CommandSemantic.EFFORT_LIMITS,
        (1000,) * JOINT_COUNT,
        capabilities,
    )
    effort_message = effort.request_messages[0]
    assert isinstance(effort_message, CANFDMessage)
    assert parse_can_id(effort_message.arbitration_id) == (
        1,
        REG_EFFORT_LIMITS,
        True,
    )
    assert effort_message.data[:2] == b"\xe8\x03"
    assert effort_message.data[-2:] == b"\x00\x00"


def test_o20_complete_tactile_transaction_accepts_out_of_order_and_padding() -> None:
    capabilities = _capabilities()
    transaction = O20ProtocolBackend(HandSide.RIGHT).read_transaction(
        TelemetrySemantic.FORCE,
        capabilities,
    )
    responses: list[CANFDMessage] = []
    for finger in range(5):
        first = REG_TACTILE_FIRST + finger * 2
        matrix = bytes((finger * 20 + index) & 0xFF for index in range(72))
        responses.extend(
            (
                _response(1, first, b"\x01" + matrix[:63]),
                _response(1, first + 1, matrix[63:] + b"\x00\x00\x00"),
            )
        )

    progress = None
    for response in reversed(responses):
        progress = transaction.consume(response)

    assert progress is not None and progress.complete
    assert isinstance(progress.result, SamplePayload)
    assert len(progress.result.tactile) == 5
    assert progress.result.tactile[0].online is True
    assert progress.result.tactile[0].raw[0] == tuple(range(6))
    assert progress.result.tactile[4].raw[-1][-1] == (80 + 71) & 0xFF


def test_o20_tactile_transaction_rejects_conflicting_duplicate() -> None:
    transaction = O20ProtocolBackend(HandSide.RIGHT).read_transaction(
        TelemetrySemantic.FORCE,
        _capabilities(),
    )
    first = _response(1, REG_TACTILE_FIRST, b"\x01" + bytes(63))
    assert transaction.consume(first).matched

    with pytest.raises(ProtocolError, match="conflicts"):
        transaction.consume(_response(1, REG_TACTILE_FIRST, b"\x01\x01" + bytes(62)))


def test_o20_read_ranges_and_clear_fault_payload_are_explicit() -> None:
    capabilities = _capabilities()
    backend = O20ProtocolBackend(HandSide.RIGHT)
    read = backend.read_transaction(TelemetrySemantic.EFFORT_LIMITS, capabilities)
    data = b"\xe8\x03" * JOINT_COUNT + b"\x00\x00" + bytes(14)

    progress = read.consume(_response(1, REG_EFFORT_LIMITS, data))

    assert isinstance(progress.result, SamplePayload)
    assert progress.result.raw == (1000,) * JOINT_COUNT
    assert progress.result.within_declared_range

    out_of_range = backend.read_transaction(
        TelemetrySemantic.EFFORT_LIMITS,
        capabilities,
    ).consume(_response(1, REG_EFFORT_LIMITS, b"\xe9\x03" * 17))
    assert isinstance(out_of_range.result, SamplePayload)
    assert not out_of_range.result.within_declared_range

    verified = replace(
        capabilities, identity=replace(capabilities.identity, verified=True)
    )
    clear = backend.operation_transaction("clear_faults", verified)
    message = clear.request_messages[0]
    assert isinstance(message, CANFDMessage)
    assert parse_can_id(message.arbitration_id) == (1, REG_FAULTS, True)
    assert message.data == bytes(17)
