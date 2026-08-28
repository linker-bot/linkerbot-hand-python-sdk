"""O30i compact HOP profile and transaction tests."""

from __future__ import annotations

import pytest

from linkerbot_hand.api import (
    DataOrigin,
    DeviceFaultError,
    HandSide,
    IdentityMismatchError,
    ProtocolError,
    UnsupportedFirmwareError,
)
from linkerbot_hand.capabilities.registry import get_declared_capabilities
from linkerbot_hand.capabilities.types import CommandSemantic, TelemetrySemantic
from linkerbot_hand.protocols import IdentityResult, SamplePayload
from linkerbot_hand.protocols.o30i import O30iProtocolBackend
from linkerbot_hand.protocols.o30i.codec import (
    JOINT_SLOT_INDICES,
    MI_POSITION,
    MI_SPEED,
    WIRE_SLOT_COUNT,
    decode_v25,
    encode_v25,
)
from linkerbot_hand.protocols.o30i.framing import (
    FRAME_TYPE,
    REQUEST_ID,
    RESPONSE_ID,
    build_read_message,
    build_write_message,
    decode_frame,
)
from linkerbot_transport.canfd import CANFDMessage


def _capabilities(side: str = "right"):
    return get_declared_capabilities("o30i", side=side)


def _response(
    main_index: int,
    sub_index: int,
    payload: bytes,
    *,
    rts: bool = False,
    padding: bytes = b"",
) -> CANFDMessage:
    control = main_index | (0x80 if rts else 0)
    return CANFDMessage(
        arbitration_id=RESPONSE_ID,
        data=bytes((control, sub_index, len(payload))) + payload + padding,
        is_extended_id=False,
        frame_type=FRAME_TYPE,
    )


def _identity_values(**overrides: str) -> dict[tuple[int, int, int], str]:
    values = {
        (0x41, 0x00, 0x10): "O30i",
        (0x41, 0x39, 0x20): "DEVICE-UID-O30I",
        (0x41, 0x59, 0x08): "HOP",
        (0x41, 0x61, 0x08): "0.0.3",
        (0x41, 0x69, 0x08): "V1.2.0",
        (0x41, 0x89, 0x08): "1.1.0",
        (0x41, 0x91, 0x08): "M1.0.0",
        (0x31, 0x00, 0x20): "NO_SENSOR",
    }
    names = {
        "product_model": (0x41, 0x00, 0x10),
        "device_uid": (0x41, 0x39, 0x20),
        "protocol_name": (0x41, 0x59, 0x08),
        "protocol_version": (0x41, 0x61, 0x08),
        "interface_hardware": (0x41, 0x69, 0x08),
        "application_version": (0x41, 0x89, 0x08),
        "mechanical_version": (0x41, 0x91, 0x08),
        "sensor_variant": (0x31, 0x00, 0x20),
    }
    for name, value in overrides.items():
        values[names[name]] = value
    return values


def _consume_identity(**overrides: str) -> IdentityResult:
    current = O30iProtocolBackend(HandSide.RIGHT).identity_transaction(_capabilities())
    values = _identity_values(**overrides)
    while True:
        request = current.request_messages[0]
        assert isinstance(request, CANFDMessage)
        main_index = request.data[0] & 0x7F
        sub_index = request.data[1]
        length = request.data[2]
        value = values[(main_index, sub_index, length)].encode("ascii")
        progress = current.consume(
            _response(
                main_index,
                sub_index,
                value.ljust(length, b"\x00"),
            )
        )
        assert progress.complete
        if current.next_transaction is None:
            assert isinstance(progress.result, IdentityResult)
            return progress.result
        assert progress.result is None
        current = current.next_transaction


def test_o30i_hop_frames_use_standard_ids_edl_and_no_brs() -> None:
    read = build_read_message(MI_POSITION, sub_index=0, length=25)
    write = build_write_message(MI_POSITION, sub_index=0, payload=bytes(25))

    assert read.arbitration_id == REQUEST_ID
    assert read.data == b"\x01\x00\x19"
    assert read.is_extended_id is False
    assert read.frame_type == FRAME_TYPE
    assert write.data == b"\x01\x00\x19" + bytes(25)
    assert write.dlc == 13

    decoded = decode_frame(b"\x01\x00\x02\x10\x20" + bytes(27))
    assert decoded.payload == b"\x10\x20"
    with pytest.raises(ProtocolError, match="declares"):
        decode_frame(b"\x01\x00\x02\x10")


def test_o30i_v25_mapping_keeps_every_uninstalled_slot_zero() -> None:
    values = tuple(range(20))
    encoded = encode_v25(values)

    assert len(encoded) == WIRE_SLOT_COUNT
    assert decode_v25(encoded) == values
    assert tuple(encoded[index] for index in (1, 2, 3, 4, 15)) == (0,) * 5
    assert tuple(encoded[index] for index in JOINT_SLOT_INDICES) == values


def test_o30i_identity_uses_single_field_reads_and_verifies_profile() -> None:
    result = _consume_identity()

    assert result.capabilities.identity.verified
    assert result.capabilities.identity.firmware == "1.1.0"
    assert result.capabilities.identity.hardware_revision == "V1.2.0"
    assert result.capabilities.identity.sensor_variant == "NO_SENSOR"
    assert result.device_info.serial_number == "DEVICE-UID-O30I"
    assert result.device_info.pcb_version is not None
    assert result.device_info.pcb_version.major == 1
    assert result.device_info.mechanical_version is not None
    assert result.device_info.mechanical_version.text == "M1.0.0"
    assert result.device_info.product_code is None
    assert result.device_info.side is HandSide.RIGHT
    assert result.fingerprint == (
        "O30i",
        "DEVICE-UID-O30I",
        "HOP",
        "0.0.3",
        "V1.2.0",
        "1.1.0",
        "M1.0.0",
        "NO_SENSOR",
    )


def test_o30i_identity_rejects_wrong_model_and_protocol_version() -> None:
    with pytest.raises(IdentityMismatchError, match="expected O30i"):
        _consume_identity(product_model="O20")
    with pytest.raises(UnsupportedFirmwareError, match="version"):
        _consume_identity(protocol_version="0.0.4")


def test_o30i_command_is_one_v25_frame_and_waits_for_correlated_ack() -> None:
    values = tuple(range(20))
    transaction = O30iProtocolBackend(HandSide.RIGHT).command_transaction(
        CommandSemantic.ANGLES,
        values,
        _capabilities(),
    )
    request = transaction.request_messages[0]
    assert isinstance(request, CANFDMessage)
    assert len(transaction.request_messages) == 1
    assert request.data == b"\x01\x00\x19" + encode_v25(values)
    assert transaction.terminal_expects_response
    assert transaction.terminal_acknowledges_command

    unrelated = transaction.consume(_response(MI_SPEED, 0, bytes(25)))
    assert not unrelated.matched
    acknowledged = transaction.consume(_response(MI_POSITION, 0, bytes(25)))
    assert acknowledged.complete and acknowledged.acknowledged
    assert acknowledged.result is None


def test_o30i_device_error_object_has_stable_fault_classification() -> None:
    transaction = O30iProtocolBackend(HandSide.RIGHT).read_transaction(
        TelemetrySemantic.ANGLES,
        _capabilities(),
    )

    with pytest.raises(DeviceFaultError, match="0x40"):
        transaction.consume(_response(0x4F, 0x01, b"\x40"))


def test_o30i_read_assembles_offsets_and_distinguishes_actual_from_setpoint() -> None:
    backend = O30iProtocolBackend(HandSide.RIGHT)
    actual = backend.read_transaction(
        TelemetrySemantic.MEASURED_SPEEDS, _capabilities()
    )
    target = backend.read_transaction(
        TelemetrySemantic.CONFIGURED_SPEEDS, _capabilities()
    )
    actual_request = actual.request_messages[0]
    target_request = target.request_messages[0]
    assert isinstance(actual_request, CANFDMessage)
    assert isinstance(target_request, CANFDMessage)
    assert actual_request.data == b"\x02\x00\x19"
    assert target_request.data == b"\x82\x00\x19"

    wire = encode_v25(tuple(range(20)))
    latter = actual.consume(_response(MI_SPEED, 16, wire[16:]))
    assert latter.matched and not latter.complete
    former = actual.consume(_response(MI_SPEED, 0, wire[:16], padding=bytes(16)))
    assert former.complete
    assert isinstance(former.result, SamplePayload)
    assert former.result.raw == tuple(range(20))

    configured = target.consume(_response(MI_SPEED, 0, wire, rts=True))
    assert isinstance(configured.result, SamplePayload)
    assert configured.result.origin is DataOrigin.DEVICE_CONFIG_RESPONSE


def test_o30i_stall_objects_map_only_twenty_public_channels() -> None:
    backend = O30iProtocolBackend(HandSide.LEFT)
    capabilities = _capabilities("left")
    cases = (
        (CommandSemantic.STALL_DETECTION_TIMES, 0x09),
        (CommandSemantic.STALL_THRESHOLDS, 0x0A),
        (CommandSemantic.STALL_HOLD_CURRENTS, 0x0B),
    )
    for semantic, main_index in cases:
        transaction = backend.command_transaction(
            semantic,
            (7,) * 20,
            capabilities,
        )
        message = transaction.request_messages[0]
        assert isinstance(message, CANFDMessage)
        assert message.data[:3] == bytes((main_index, 0, 25))
        assert decode_v25(message.data[3:]) == (7,) * 20


def test_o30i_duplicate_fragment_conflict_fails_transaction() -> None:
    transaction = O30iProtocolBackend(HandSide.RIGHT).read_transaction(
        TelemetrySemantic.ANGLES,
        _capabilities(),
    )
    assert transaction.consume(_response(MI_POSITION, 0, bytes(10))).matched

    with pytest.raises(ProtocolError, match="conflicts"):
        transaction.consume(_response(MI_POSITION, 0, b"\x01" + bytes(9)))
