"""O30 compact HOP profile and transaction tests."""

from __future__ import annotations

import pytest

from linkerbot_hand.api import (
    DataOrigin,
    HandSide,
    IdentityMismatchError,
    ProtocolError,
    UnsupportedFirmwareError,
)
from linkerbot_hand.capabilities.registry import get_declared_capabilities
from linkerbot_hand.capabilities.types import CommandSemantic, TelemetrySemantic
from linkerbot_hand.protocols import IdentityResult, SamplePayload
from linkerbot_hand.protocols.o30 import O30ProtocolBackend
from linkerbot_hand.protocols.o30.codec import (
    JOINT_SLOT_INDICES,
    LOGICAL_JOINT_IDS,
    LOGICAL_SLOT_COUNT,
    MI_POSITION,
    MI_SPEED,
    PUBLIC_JOINT_LOGICAL_IDS,
    WIRE_SLOT_COUNT,
    decode_fault_statuses,
    decode_logical_v36,
    decode_v25,
    encode_v25,
)
from linkerbot_hand.protocols.o30.framing import (
    FRAME_TYPE,
    LEFT_REQUEST_ID,
    LEFT_RESPONSE_ID,
    REQUEST_ID,
    RESPONSE_ID,
    addressing_for_side,
    build_read_message,
    build_write_message,
    decode_frame,
    matching_response_frame,
)
from linkerbot_transport.canfd import CANFDMessage


def _capabilities(side: str = "right"):
    return get_declared_capabilities("o30", side=side)


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


def _identity_object(layout: str, **overrides: str) -> bytes:
    values = {
        "product_model": "O30",
        "power_supply_range": "12V~24V",
        "microcontroller_uid": "0029003E594650142035334E",
        "device_uid": "DEVICE-UID-O30",
        "protocol_name": "HOP",
        "protocol_version": "0.0.4",
        "interface_board_version": "V1.2.0",
        "adapter_board_version": "V1.1.0",
        "control_board_version": "V0.9.0",
        "bootloader_version": "1.0.0",
        "application_version": "1.2.0",
        "mechanical_version": "M1.0.0",
        "build_time": "2026-09-03 13:55:02",
        "supported_interfaces": "CAN",
        "reported_side": "RIGHT",
    }
    values.update(overrides)
    data = bytearray(225 if layout == "deployed" else 249)

    def put(offset: int, length: int, name: str) -> None:
        encoded = values[name].encode("ascii")
        data[offset : offset + min(length, len(encoded))] = encoded[:length]

    for offset, length, name in (
        (0x00, 0x10, "product_model"),
        (0x10, 0x10, "power_supply_range"),
        (0x20, 0x19, "microcontroller_uid"),
        (0x39, 0x20, "device_uid"),
        (0x59, 0x08, "protocol_name"),
        (0x61, 0x08, "protocol_version"),
    ):
        put(offset, length, name)
    if layout == "deployed":
        fields = (
            (0x69, 0x08, "interface_board_version"),
            (0x71, 0x08, "adapter_board_version"),
            (0x79, 0x08, "control_board_version"),
            (0x81, 0x08, "bootloader_version"),
            (0x89, 0x08, "application_version"),
            (0x91, 0x08, "mechanical_version"),
            (0x99, 0x20, "build_time"),
            (0xB9, 0x20, "supported_interfaces"),
            (0xD9, 0x08, "reported_side"),
        )
    else:
        fields = (
            (0x69, 0x10, "interface_board_version"),
            (0x79, 0x10, "adapter_board_version"),
            (0x89, 0x10, "control_board_version"),
            (0x99, 0x08, "bootloader_version"),
            (0xA1, 0x08, "application_version"),
            (0xA9, 0x08, "mechanical_version"),
            (0xB1, 0x20, "build_time"),
            (0xD1, 0x20, "supported_interfaces"),
            (0xF1, 0x08, "reported_side"),
        )
    for offset, length, name in fields:
        put(offset, length, name)
    return bytes(data)


def _consume_identity(
    *,
    layout: str = "deployed",
    **overrides: str,
) -> IdentityResult:
    current = O30ProtocolBackend(HandSide.RIGHT).identity_transaction(_capabilities())
    product_info = _identity_object(layout, **overrides)
    while True:
        request = current.request_messages[0]
        assert isinstance(request, CANFDMessage)
        main_index = request.data[0] & 0x7F
        sub_index = request.data[1]
        length = request.data[2]
        assert main_index == 0x41
        progress = current.consume(
            _response(
                main_index,
                sub_index,
                product_info[sub_index : sub_index + length],
            )
        )
        assert progress.complete
        next_transaction = current.build_next_transaction()
        if next_transaction is None:
            assert isinstance(progress.result, IdentityResult)
            return progress.result
        assert progress.result is None
        current = next_transaction


def test_o30_hop_frames_use_standard_ids_edl_and_no_brs() -> None:
    read = build_read_message(
        MI_POSITION, sub_index=0, length=25, request_id=REQUEST_ID
    )
    write = build_write_message(
        MI_POSITION, sub_index=0, payload=bytes(25), request_id=REQUEST_ID
    )

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


def test_o30_left_hand_uses_0x002_0x402_and_filters_by_response_id() -> None:
    left = addressing_for_side(HandSide.LEFT)
    assert (left.request_id, left.response_id) == (LEFT_REQUEST_ID, LEFT_RESPONSE_ID)
    assert (LEFT_REQUEST_ID, LEFT_RESPONSE_ID) == (0x002, 0x402)

    read = build_read_message(
        MI_POSITION, sub_index=0, length=25, request_id=left.request_id
    )
    assert read.arbitration_id == 0x002

    left_reply = CANFDMessage(
        arbitration_id=LEFT_RESPONSE_ID,
        data=b"\x01\x00\x02\x10\x20",
        is_extended_id=False,
        frame_type=FRAME_TYPE,
    )
    right_reply = CANFDMessage(
        arbitration_id=RESPONSE_ID,
        data=b"\x01\x00\x02\x10\x20",
        is_extended_id=False,
        frame_type=FRAME_TYPE,
    )
    assert matching_response_frame(left_reply, LEFT_RESPONSE_ID) is not None
    assert matching_response_frame(right_reply, LEFT_RESPONSE_ID) is None
    assert matching_response_frame(right_reply, RESPONSE_ID) is not None


def test_o30_v25_mapping_keeps_every_uninstalled_slot_zero() -> None:
    values = tuple(range(20))
    encoded = encode_v25(values)

    assert len(encoded) == WIRE_SLOT_COUNT
    assert decode_v25(encoded) == values
    assert tuple(encoded[index] for index in (1, 2, 3, 4, 15)) == (0,) * 5
    assert tuple(encoded[index] for index in JOINT_SLOT_INDICES) == values


def test_o30_logical_joint_ids_are_distinct_from_v25_wire_slots() -> None:
    logical_object = bytes(range(LOGICAL_SLOT_COUNT))

    assert LOGICAL_JOINT_IDS == (
        0,
        1,
        2,
        4,
        6,
        7,
        8,
        9,
        11,
        12,
        13,
        14,
        16,
        17,
        18,
        19,
        21,
        22,
        23,
        24,
    )
    assert PUBLIC_JOINT_LOGICAL_IDS != JOINT_SLOT_INDICES
    assert decode_logical_v36(logical_object) == PUBLIC_JOINT_LOGICAL_IDS


def test_o30_fault_decoder_preserves_raw_and_names_every_documented_bit() -> None:
    raw = (0xFF, 0x05, *(0 for _ in range(18)))

    statuses = decode_fault_statuses(raw)

    assert tuple(status.raw for status in statuses) == raw
    assert statuses[0].hand_layer_stall
    assert statuses[0].actuator_layer_stall
    assert statuses[0].over_current
    assert statuses[0].over_temperature
    assert statuses[0].reserved_bits == 0x30
    assert statuses[0].actuator_abnormal
    assert statuses[0].actuator_offline
    assert statuses[1].hand_layer_stall
    assert statuses[1].over_current
    assert not statuses[1].actuator_offline


def test_o30_identity_uses_single_field_reads_and_verifies_profile() -> None:
    result = _consume_identity()

    assert result.capabilities.identity.verified
    assert result.capabilities.identity.firmware == "1.2.0"
    assert result.capabilities.identity.hardware_revision == "V1.2.0"
    assert result.capabilities.identity.adapter_board_version == "V1.1.0"
    assert result.capabilities.identity.control_board_version == "V0.9.0"
    assert result.capabilities.identity.sensor_variant is None
    assert result.capabilities.identity.protocol_name == "HOP"
    assert result.capabilities.identity.protocol_version == "0.0.4"
    assert result.capabilities.model == "O30"
    assert result.capabilities.identity.product_code == "O30"
    assert result.device_info.serial_number == "DEVICE-UID-O30"
    assert result.device_info.pcb_version is not None
    assert result.device_info.pcb_version.major == 1
    assert result.device_info.mechanical_version is not None
    assert result.device_info.mechanical_version.text == "M1.0.0"
    assert result.device_info.product_code == "O30"
    assert result.device_info.side is HandSide.RIGHT
    assert result.device_info.reported_side is HandSide.RIGHT
    assert result.device_info.power_supply_range == "12V~24V"
    assert result.device_info.microcontroller_uid == "0029003E594650142035334E"
    assert result.device_info.build_time == "2026-09-03 13:55:02"
    assert result.device_info.supported_interfaces == "CAN"
    assert result.fingerprint == (
        "O30",
        "DEVICE-UID-O30",
        "HOP",
        "0.0.4",
        "V1.2.0",
        "V1.1.0",
        "V0.9.0",
        "1.0.0",
        "1.2.0",
        "M1.0.0",
        "right",
    )


def test_o30_identity_decodes_the_documented_004_layout() -> None:
    result = _consume_identity(layout="documented")

    assert result.device_info.pcb_version is not None
    assert result.device_info.pcb_version.text == "V1.2.0"
    assert result.device_info.adapter_board_version is not None
    assert result.device_info.adapter_board_version.text == "V1.1.0"
    assert result.device_info.control_board_version is not None
    assert result.device_info.control_board_version.text == "V0.9.0"
    assert result.device_info.bootloader_version is not None
    assert result.device_info.bootloader_version.text == "1.0.0"
    assert len(result.device_info.raw_identity or b"") == 249


def test_o30_identity_accepts_003_without_requiring_sensor_metadata() -> None:
    result = _consume_identity(protocol_version="0.0.3", reported_side="")

    assert result.device_info.protocol_version == "0.0.3"
    assert result.device_info.sensor_variant is None
    assert result.device_info.reported_side is None
    assert result.device_info.side is HandSide.RIGHT


@pytest.mark.parametrize(
    "product_model",
    (
        "O30",
        "o30",
        "O30-01-R",
        "O30i",
        "o30i",
        "O30i-20-001-R-B",
        "o30i-20-001-R-B",
    ),
)
def test_o30_identity_accepts_product_model_prefix(product_model: str) -> None:
    result = _consume_identity(product_model=product_model)

    assert result.capabilities.model == "O30"
    assert result.capabilities.identity.product_code == product_model
    assert result.device_info.product_code == product_model


def test_o30_identity_rejects_wrong_model_and_protocol_name_only() -> None:
    with pytest.raises(IdentityMismatchError, match="expected O30"):
        _consume_identity(product_model="O20")
    with pytest.raises(UnsupportedFirmwareError, match="protocol"):
        _consume_identity(protocol_name="OTHER")

    result = _consume_identity(layout="documented", protocol_version="9.9.9")
    assert result.device_info.protocol_version == "9.9.9"


def test_o30_command_is_one_v25_frame_and_waits_for_correlated_ack() -> None:
    values = tuple(range(20))
    transaction = O30ProtocolBackend(HandSide.RIGHT).command_transaction(
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
    assert isinstance(acknowledged.result, SamplePayload)
    assert acknowledged.result.raw == (0,) * 20
    assert acknowledged.result.origin is DataOrigin.DEVICE_COMMAND_FEEDBACK


def test_o30_device_error_object_is_a_communication_error() -> None:
    transaction = O30ProtocolBackend(HandSide.RIGHT).read_transaction(
        TelemetrySemantic.ANGLES,
        _capabilities(),
    )

    with pytest.raises(ProtocolError, match="unknown flags 0x40"):
        transaction.consume(_response(0x4F, 0x01, b"\x40"))


def test_o30_read_assembles_offsets_and_distinguishes_actual_from_setpoint() -> None:
    backend = O30ProtocolBackend(HandSide.RIGHT)
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


def test_o30_stall_objects_map_only_twenty_public_channels() -> None:
    backend = O30ProtocolBackend(HandSide.LEFT)
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


def test_o30_duplicate_fragment_conflict_fails_transaction() -> None:
    transaction = O30ProtocolBackend(HandSide.RIGHT).read_transaction(
        TelemetrySemantic.ANGLES,
        _capabilities(),
    )
    assert transaction.consume(_response(MI_POSITION, 0, bytes(10))).matched

    with pytest.raises(ProtocolError, match="conflicts"):
        transaction.consume(_response(MI_POSITION, 0, b"\x01" + bytes(9)))
