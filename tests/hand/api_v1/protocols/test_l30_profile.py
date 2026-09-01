"""L30 CAN FD codec and transaction profile tests."""

from __future__ import annotations

import pytest

from linkerbot_hand.api import (
    DataOrigin,
    DeviceFaultError,
    HandSide,
    IdentityMismatchError,
    ProtocolError,
)
from linkerbot_hand.capabilities.registry import get_declared_capabilities
from linkerbot_hand.capabilities.types import CommandSemantic, TelemetrySemantic
from linkerbot_hand.protocols import IdentityResult, SamplePayload
from linkerbot_hand.protocols.l30 import L30ProtocolBackend
from linkerbot_hand.protocols.l30.codec import (
    CONFIG_UNLOCK_PASSWORD,
    JOINT_COUNT,
    PARENT_CONFIG,
    PARENT_CONTROL,
    PARENT_QUERY,
    PARENT_TACTILE,
    SUB_CALIBRATE,
    SUB_CONFIG_UNLOCK,
    SUB_CURRENT,
    SUB_DEVICE_INFO,
    SUB_DISABLE,
    SUB_ENABLE,
    SUB_FAULT,
    SUB_POSITION,
    SUB_PRODUCT_CODE,
    SUB_SPEED,
    SUB_TEMPERATURE,
    decode_i16_response,
    encode_i16_vector,
)
from linkerbot_hand.protocols.l30.framing import (
    ACCESS_READ,
    ACCESS_WRITE,
    build_can_id,
    parse_can_id,
)
from linkerbot_transport.canfd import CANFDMessage


def _capabilities():
    return get_declared_capabilities("l30")


def _response(
    parent: int,
    subcommand: int,
    data: bytes,
    *,
    write: bool = False,
) -> CANFDMessage:
    return CANFDMessage(
        arbitration_id=build_can_id(
            access=ACCESS_WRITE if write else ACCESS_READ,
            parent=parent,
            subcommand=subcommand,
            destination_id=0,
            source_id=1,
        ),
        data=data,
        is_extended_id=True,
    )


def _single_frame(payload: bytes, *, status: int = 0) -> bytes:
    return bytes((len(payload), 0, status)) + payload


def _device_info(*, serial_number: int = 1, side_code: int = 0) -> bytes:
    return (
        b"\x13"
        + serial_number.to_bytes(4, "big")
        + bytes((1, 0, 6))
        + bytes((0, 0, 3))
        + bytes((1, 0, 2))
        + bytes((1, side_code))
        + b"BA"
    )


def _consume_identity(
    *,
    product_code: str = "LHT30-06-001-L-B-3-A",
    device_info: bytes | None = None,
) -> IdentityResult:
    transaction = L30ProtocolBackend(HandSide.NOT_APPLICABLE).identity_transaction(
        _capabilities()
    )
    first = transaction.consume(
        _response(
            PARENT_CONFIG,
            SUB_DEVICE_INFO,
            _single_frame(device_info or _device_info()),
        )
    )
    assert first.complete and first.result is None
    assert transaction.next_transaction is not None
    raw_code = product_code.encode("ascii")
    second = transaction.next_transaction.consume(
        _response(
            PARENT_CONFIG,
            SUB_PRODUCT_CODE,
            _single_frame(raw_code),
        )
    )
    assert second.complete
    assert isinstance(second.result, IdentityResult)
    return second.result


def test_l30_extended_id_and_big_endian_vectors_match_documented_examples() -> None:
    request_id = build_can_id(
        access=ACCESS_WRITE,
        parent=PARENT_CONTROL,
        subcommand=SUB_ENABLE,
        destination_id=1,
        source_id=0,
    )
    response_id = build_can_id(
        access=ACCESS_WRITE,
        parent=PARENT_CONTROL,
        subcommand=SUB_ENABLE,
        destination_id=0,
        source_id=1,
    )

    assert request_id == 0x0220E100
    assert response_id == 0x0220E008
    assert parse_can_id(request_id).destination_id == 1
    values = (-32768, -1, 0, 1, 900) + (2,) * 12
    encoded = encode_i16_vector(values)
    assert encoded[:10] == b"\x80\x00\xff\xff\x00\x00\x00\x01\x03\x84"
    assert decode_i16_response(_single_frame(encoded)) == values


def test_l30_backend_preserves_configured_node_and_host_ids() -> None:
    backend = L30ProtocolBackend(
        HandSide.NOT_APPLICABLE,
        node_id=3,
        host_id=2,
    )

    transaction = backend.identity_transaction(_capabilities())
    request = transaction.request_messages[0]

    assert isinstance(request, CANFDMessage)
    frame = parse_can_id(request.arbitration_id)
    assert frame.destination_id == 3
    assert frame.source_id == 2
    assert any(
        (route_frame := parse_can_id(route.arbitration_id)).destination_id == 2
        and route_frame.source_id == 3
        for route in backend.route_keys
    )


@pytest.mark.parametrize(
    ("node_id", "host_id", "match"),
    (
        (0, 1, "node_id must be within 1..31"),
        (32, 0, "node_id must be within 1..31"),
        (1, -1, "host_id must be within 0..31"),
        (1, 32, "host_id must be within 0..31"),
        (3, 3, "must be different"),
    ),
)
def test_l30_backend_rejects_invalid_endpoint_ids(
    node_id: int,
    host_id: int,
    match: str,
) -> None:
    with pytest.raises(ProtocolError, match=match):
        L30ProtocolBackend(
            HandSide.NOT_APPLICABLE,
            node_id=node_id,
            host_id=host_id,
        )


def test_l30_identity_is_response_gated_and_keeps_public_side_neutral() -> None:
    result = _consume_identity()

    assert result.capabilities.identity.verified
    assert result.capabilities.side is HandSide.NOT_APPLICABLE
    assert result.capabilities.identity.firmware == "V1.0.6"
    assert result.capabilities.identity.hardware_revision == "V0.0.3"
    assert result.device_info.side is HandSide.NOT_APPLICABLE
    assert result.device_info.serial_number == "1"
    assert result.device_info.product_code == "LHT30-06-001-L-B-3-A"
    assert result.fingerprint == (0x13, 1, "LHT30-06-001-L-B-3-A")


@pytest.mark.parametrize(
    ("device_info", "product_code", "match"),
    (
        (_device_info(serial_number=2), "LHT30-06-001-L-B-3-A", "disagrees"),
        (_device_info(side_code=1), "LHT30-06-001-L-B-3-A", "disagrees"),
        (_device_info(), "O30-06-001-L-B-3-A", "unexpected"),
    ),
)
def test_l30_identity_rejects_conflicting_fields(
    device_info: bytes,
    product_code: str,
    match: str,
) -> None:
    with pytest.raises(IdentityMismatchError, match=match):
        _consume_identity(device_info=device_info, product_code=product_code)


def test_l30_commands_keep_angles_dispatch_only_and_publish_configuration_cache() -> (
    None
):
    capabilities = _capabilities()
    backend = L30ProtocolBackend(HandSide.NOT_APPLICABLE)

    angles = backend.command_transaction(
        CommandSemantic.ANGLES,
        (0,) * JOINT_COUNT,
        capabilities,
    )
    angle_message = angles.request_messages[0]
    assert isinstance(angle_message, CANFDMessage)
    assert angle_message.data == b"\x22\x00" + bytes(JOINT_COUNT * 2)
    assert angle_message.dlc == 0x0E
    assert not angles.expects_response
    assert not angles.terminal_acknowledges_command
    assert angles.dispatch_publications == ()

    for semantic, value, cached_semantic in (
        (CommandSemantic.SPEEDS, 60, TelemetrySemantic.CONFIGURED_SPEEDS),
        (CommandSemantic.CURRENT_LIMITS, 200, TelemetrySemantic.CURRENT_LIMITS),
    ):
        command = backend.command_transaction(
            semantic,
            (value,) * JOINT_COUNT,
            capabilities,
        )
        message = command.request_messages[0]
        assert isinstance(message, CANFDMessage)
        assert not command.expects_response
        assert message.data == b"\x22\x00" + value.to_bytes(2, "big") * JOINT_COUNT
        assert command.dispatch_publications == (
            SamplePayload(
                semantic=cached_semantic,
                raw=(value,) * JOINT_COUNT,
                origin=DataOrigin.COMMAND_CACHE,
            ),
        )


@pytest.mark.parametrize(
    ("semantic", "subcommand", "payload"),
    (
        (TelemetrySemantic.ANGLES, SUB_POSITION, b"\x03\x84" * JOINT_COUNT),
        (TelemetrySemantic.MEASURED_SPEEDS, SUB_SPEED, b"\x00\x3c" * JOINT_COUNT),
        (TelemetrySemantic.CURRENTS, SUB_CURRENT, b"\x00\xc8" * JOINT_COUNT),
        (TelemetrySemantic.TEMPERATURES, SUB_TEMPERATURE, bytes(range(17))),
        (TelemetrySemantic.FAULTS, SUB_FAULT, bytes(range(17))),
    ),
)
def test_l30_single_state_reads_preserve_protocol_values(
    semantic: TelemetrySemantic,
    subcommand: int,
    payload: bytes,
) -> None:
    transaction = L30ProtocolBackend(HandSide.NOT_APPLICABLE).read_transaction(
        semantic,
        _capabilities(),
    )

    progress = transaction.consume(
        _response(PARENT_QUERY, subcommand, _single_frame(payload))
    )

    assert progress.complete
    assert isinstance(progress.result, SamplePayload)
    if semantic in {TelemetrySemantic.TEMPERATURES, TelemetrySemantic.FAULTS}:
        assert progress.result.raw == tuple(range(17))
    else:
        expected = (
            900
            if semantic is TelemetrySemantic.ANGLES
            else (60 if semantic is TelemetrySemantic.MEASURED_SPEEDS else 200)
        )
        assert progress.result.raw == (expected,) * JOINT_COUNT


def test_l30_nonzero_device_status_has_stable_fault_classification() -> None:
    transaction = L30ProtocolBackend(HandSide.NOT_APPLICABLE).read_transaction(
        TelemetrySemantic.ANGLES,
        _capabilities(),
    )

    with pytest.raises(DeviceFaultError, match="0x22"):
        transaction.consume(
            _response(PARENT_QUERY, SUB_POSITION, _single_frame(b"", status=0x22))
        )


def test_l30_tactile_gates_each_finger_and_publishes_one_atomic_sample() -> None:
    transaction = L30ProtocolBackend(HandSide.NOT_APPLICABLE).read_transaction(
        TelemetrySemantic.FORCE,
        _capabilities(),
    )
    current = transaction

    for finger in range(5):
        assert current.key == ("read", TelemetrySemantic.FORCE, finger + 1)
        matrix = bytes((finger * 72 + index) & 0xFF for index in range(72))
        second = current.consume(
            _response(
                PARENT_TACTILE,
                finger + 1,
                b"\x0b\x11\x00" + matrix[61:],
            )
        )
        assert second.matched and not second.complete
        first = current.consume(
            _response(
                PARENT_TACTILE,
                finger + 1,
                b"\x3d\x10\x00" + matrix[:61],
            )
        )
        assert first.complete
        if finger < 4:
            assert first.result is None
            assert current.next_transaction is not None
            current = current.next_transaction
        else:
            assert isinstance(first.result, SamplePayload)
            assert len(first.result.tactile) == 5
            assert first.result.tactile[0].raw[0] == tuple(range(6))
            assert first.result.tactile[4].raw[-1][-1] == (4 * 72 + 71) & 0xFF


def test_l30_tactile_rejects_conflicting_duplicate_fragments() -> None:
    transaction = L30ProtocolBackend(HandSide.NOT_APPLICABLE).read_transaction(
        TelemetrySemantic.FORCE,
        _capabilities(),
    )
    first = _response(PARENT_TACTILE, 1, b"\x3d\x10\x00" + bytes(61))
    assert transaction.consume(first).matched

    with pytest.raises(ProtocolError, match="conflicts"):
        transaction.consume(
            _response(PARENT_TACTILE, 1, b"\x3d\x10\x00\x01" + bytes(60))
        )


def test_l30_calibration_is_disable_unlock_calibrate_with_ack_at_each_stage() -> None:
    transaction = L30ProtocolBackend(HandSide.NOT_APPLICABLE).operation_transaction(
        "calibrate",
        _capabilities(),
    )
    expected = (
        (PARENT_CONTROL, SUB_DISABLE, b"\x00\x00"),
        (PARENT_CONFIG, SUB_CONFIG_UNLOCK, b"\x06\x00" + CONFIG_UNLOCK_PASSWORD),
        (PARENT_CONFIG, SUB_CALIBRATE, b"\x00\x00"),
    )

    for index, (parent, subcommand, data) in enumerate(expected):
        message = transaction.request_messages[0]
        assert isinstance(message, CANFDMessage)
        parsed = parse_can_id(message.arbitration_id)
        assert (parsed.parent, parsed.subcommand, parsed.access) == (
            parent,
            subcommand,
            ACCESS_WRITE,
        )
        assert message.data == data
        progress = transaction.consume(
            _response(parent, subcommand, b"\x00\x00\x00", write=True)
        )
        assert progress.complete and progress.acknowledged
        if index < len(expected) - 1:
            assert transaction.next_transaction is not None
            transaction = transaction.next_transaction
        else:
            assert transaction.next_transaction is None


def test_l30_enable_and_disable_explicitly_require_device_ack() -> None:
    backend = L30ProtocolBackend(HandSide.NOT_APPLICABLE)
    for operation, subcommand in (("enable", SUB_ENABLE), ("disable", SUB_DISABLE)):
        transaction = backend.operation_transaction(operation, _capabilities())
        assert transaction.terminal_expects_response
        assert transaction.terminal_acknowledges_command
        progress = transaction.consume(
            _response(PARENT_CONTROL, subcommand, b"\x00\x00\x00", write=True)
        )
        assert progress.acknowledged
