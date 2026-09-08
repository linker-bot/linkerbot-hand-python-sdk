"""L6/O6 fixed-width control and state transaction tests."""

from __future__ import annotations

import can
import pytest

from linkerbot_hand.api import DataOrigin, HandSide
from linkerbot_hand.api.errors import ProtocolError, UnsupportedOperationError
from linkerbot_hand.capabilities.registry import get_declared_capabilities
from linkerbot_hand.capabilities.types import CommandSemantic, TelemetrySemantic
from linkerbot_hand.protocols import ProtocolTransaction, SamplePayload
from linkerbot_hand.protocols.common.classic_can import classic_route
from linkerbot_hand.protocols.l6 import L6ProtocolBackend
from linkerbot_hand.protocols.o6 import O6ProtocolBackend


def _reply(side: HandSide, opcode: int, values: tuple[int, ...]) -> can.Message:
    return can.Message(
        arbitration_id=classic_route(side).arbitration_id,
        data=bytes((opcode, *values)),
        is_extended_id=False,
    )


def _payload(
    transaction: ProtocolTransaction,
    response: can.Message,
) -> SamplePayload:
    progress = transaction.consume(response)
    assert progress.complete
    assert isinstance(progress.result, SamplePayload)
    return progress.result


def _request_data(transaction: ProtocolTransaction) -> bytes:
    message = transaction.request_messages[0]
    assert isinstance(message, can.Message)
    return bytes(message.data)


def test_l6_speed_and_current_limit_writes_use_documented_responses() -> None:
    capabilities = get_declared_capabilities("l6", side="left")
    backend = L6ProtocolBackend(HandSide.LEFT)

    angles = backend.command_transaction(
        CommandSemantic.ANGLES,
        (0, 1, 2, 3, 4, 255),
        capabilities,
    )
    speeds = backend.command_transaction(
        CommandSemantic.SPEEDS,
        (80,) * 6,
        capabilities,
    )
    limits = backend.command_transaction(
        CommandSemantic.CURRENT_LIMITS,
        (90,) * 6,
        capabilities,
    )

    assert _request_data(angles) == b"\x01\x00\x01\x02\x03\x04\xff"
    assert _request_data(speeds) == b"\x05" + bytes((80,) * 6)
    assert not speeds.dispatch_publications
    assert speeds.terminal_acknowledges_command
    speed_response = speeds.consume(_reply(HandSide.LEFT, 0x05, (79,) * 6))
    assert speed_response.acknowledged
    assert isinstance(speed_response.result, SamplePayload)
    assert speed_response.result.semantic is TelemetrySemantic.CONFIGURED_SPEEDS
    assert speed_response.result.raw == (79,) * 6
    assert speed_response.result.origin is DataOrigin.DEVICE_CONFIG_RESPONSE
    assert _request_data(limits) == b"\x02" + bytes((90,) * 6)
    assert not limits.dispatch_publications
    assert limits.terminal_acknowledges_command
    limit_response = limits.consume(_reply(HandSide.LEFT, 0x02, (89,) * 6))
    assert limit_response.acknowledged
    assert isinstance(limit_response.result, SamplePayload)
    assert limit_response.result.raw == (89,) * 6
    assert angles.expects_response
    echo = _payload(angles, _reply(HandSide.LEFT, 0x01, (6, 5, 4, 3, 2, 1)))
    assert echo.raw == (6, 5, 4, 3, 2, 1)
    assert echo.origin is DataOrigin.DEVICE_COMMAND_FEEDBACK
    assert not angles.terminal_acknowledges_command


def test_l6_reads_map_protocol_values_to_exact_semantics() -> None:
    capabilities = get_declared_capabilities("l6", side="left")
    backend = L6ProtocolBackend(HandSide.LEFT)
    cases = (
        (TelemetrySemantic.ANGLES, 0x01, DataOrigin.DEVICE_RESPONSE),
        (TelemetrySemantic.CURRENT_LIMITS, 0x02, DataOrigin.DEVICE_CONFIG_RESPONSE),
        (TelemetrySemantic.TEMPERATURES, 0x33, DataOrigin.DEVICE_RESPONSE),
        (TelemetrySemantic.FAULTS, 0x35, DataOrigin.DEVICE_RESPONSE),
        (TelemetrySemantic.CURRENTS, 0x36, DataOrigin.DEVICE_RESPONSE),
    )

    for semantic, opcode, origin in cases:
        transaction = backend.read_transaction(semantic, capabilities)
        assert _request_data(transaction) == bytes((opcode,))
        payload = _payload(transaction, _reply(HandSide.LEFT, opcode, (1,) * 6))
        assert payload.semantic is semantic
        assert payload.raw == (1,) * 6
        assert payload.origin is origin

    with pytest.raises(UnsupportedOperationError, match="active protocol query"):
        backend.read_transaction(TelemetrySemantic.CONFIGURED_SPEEDS, capabilities)


def test_l6_clear_faults_is_a_bounded_dispatch_only_operation() -> None:
    capabilities = get_declared_capabilities("l6", side="left")
    transaction = L6ProtocolBackend(HandSide.LEFT).operation_transaction(
        "clear_faults",
        capabilities,
    )

    assert _request_data(transaction) == b"\x83" + bytes((1,) * 6)
    assert not transaction.expects_response


def test_l6_stall_configuration_maps_raw_objects_and_echo_responses() -> None:
    capabilities = get_declared_capabilities("l6", side="left")
    backend = L6ProtocolBackend(HandSide.LEFT)
    cases = (
        (
            CommandSemantic.STALL_DETECTION_TIMES,
            TelemetrySemantic.STALL_DETECTION_TIMES,
            0xC5,
        ),
        (
            CommandSemantic.STALL_THRESHOLDS,
            TelemetrySemantic.STALL_THRESHOLDS,
            0xC6,
        ),
        (
            CommandSemantic.STALL_HOLD_CURRENTS,
            TelemetrySemantic.STALL_HOLD_CURRENTS,
            0xC7,
        ),
    )

    for command_semantic, telemetry_semantic, opcode in cases:
        command = backend.command_transaction(
            command_semantic,
            (10, 20, 30, 40, 50, 60),
            capabilities,
        )
        assert _request_data(command) == bytes((opcode, 10, 20, 30, 40, 50, 60))
        assert command.terminal_acknowledges_command
        read = backend.read_transaction(telemetry_semantic, capabilities)
        assert _request_data(read) == bytes((opcode,))
        payload = _payload(read, _reply(HandSide.LEFT, opcode, (1,) * 6))
        assert payload.semantic is telemetry_semantic
        assert payload.origin is DataOrigin.DEVICE_CONFIG_RESPONSE


def test_l6_limit_compensation_write_requires_documented_exact_echo() -> None:
    capabilities = get_declared_capabilities("l6", side="left")
    backend = L6ProtocolBackend(HandSide.LEFT)
    transaction = backend.command_transaction(
        CommandSemantic.LIMIT_COMPENSATIONS,
        (0, 1, 2, 3, 4, 255),
        capabilities,
    )

    assert _request_data(transaction) == b"\x38\x00\x01\x02\x03\x04\xff"
    assert transaction.next_transaction is None
    assert transaction.terminal_acknowledges_command
    assert transaction.consume(
        _reply(HandSide.LEFT, 0x38, (0, 1, 2, 3, 4, 255))
    ).acknowledged
    with pytest.raises(ProtocolError, match="did not echo"):
        transaction.consume(_reply(HandSide.LEFT, 0x38, (0,) * 6))


def test_l6_limit_compensation_read_and_parameter_save_are_response_gated() -> None:
    capabilities = get_declared_capabilities("l6", side="left")
    backend = L6ProtocolBackend(HandSide.LEFT)
    read = backend.read_transaction(
        TelemetrySemantic.LIMIT_COMPENSATIONS,
        capabilities,
    )
    save = backend.operation_transaction("save_parameters", capabilities)

    assert _request_data(read) == b"\x38"
    assert _request_data(save) == bytes((0xCF,)) * 8
    assert save.response_acknowledges_command
    assert save.consume(
        can.Message(
            arbitration_id=classic_route(HandSide.LEFT).arbitration_id,
            data=b"\xcf\x01",
            is_extended_id=False,
        )
    ).acknowledged
    with pytest.raises(ProtocolError, match="was not CF 01"):
        save.consume(
            can.Message(
                arbitration_id=classic_route(HandSide.LEFT).arbitration_id,
                data=b"\xcf\x00",
                is_extended_id=False,
            )
        )


def test_o6_configuration_queries_are_not_measured_speed_or_torque() -> None:
    capabilities = get_declared_capabilities("o6", side="right")
    backend = O6ProtocolBackend(HandSide.RIGHT)
    cases = (
        (CommandSemantic.CURRENT_LIMITS, TelemetrySemantic.CURRENT_LIMITS, 0x02),
        (CommandSemantic.SPEEDS, TelemetrySemantic.CONFIGURED_SPEEDS, 0x05),
        (
            CommandSemantic.ACCELERATIONS,
            TelemetrySemantic.CONFIGURED_ACCELERATIONS,
            0x87,
        ),
    )

    for command_semantic, telemetry_semantic, opcode in cases:
        command = backend.command_transaction(
            command_semantic,
            (100,) * 6,
            capabilities,
        )
        assert _request_data(command) == bytes((opcode, *([100] * 6)))
        assert not command.dispatch_publications
        assert command.terminal_acknowledges_command
        response = command.consume(_reply(HandSide.RIGHT, opcode, (100,) * 6))
        assert response.acknowledged
        assert isinstance(response.result, SamplePayload)
        assert response.result.semantic is telemetry_semantic
        assert response.result.origin is DataOrigin.DEVICE_CONFIG_RESPONSE
        read = backend.read_transaction(telemetry_semantic, capabilities)
        payload = _payload(read, _reply(HandSide.RIGHT, opcode, (42,) * 6))
        assert payload.semantic is telemetry_semantic
        assert payload.origin is DataOrigin.DEVICE_CONFIG_RESPONSE

    assert capabilities.telemetry_channel(TelemetrySemantic.MEASURED_SPEEDS) is None
    assert capabilities.telemetry_channel(TelemetrySemantic.TORQUES) is None


def test_o6_stall_configuration_uses_protocol_specific_opcode_order() -> None:
    capabilities = get_declared_capabilities("o6", side="right")
    backend = O6ProtocolBackend(HandSide.RIGHT)
    cases = (
        (
            CommandSemantic.STALL_THRESHOLDS,
            TelemetrySemantic.STALL_THRESHOLDS,
            0xC5,
        ),
        (
            CommandSemantic.STALL_DETECTION_TIMES,
            TelemetrySemantic.STALL_DETECTION_TIMES,
            0xC6,
        ),
        (
            CommandSemantic.STALL_HOLD_CURRENTS,
            TelemetrySemantic.STALL_HOLD_CURRENTS,
            0xC7,
        ),
    )

    for command_semantic, telemetry_semantic, opcode in cases:
        values = (10, 20, 30, 40, 50, 60)
        command = backend.command_transaction(
            command_semantic,
            values,
            capabilities,
        )
        assert _request_data(command) == bytes((opcode, *values))
        assert command.terminal_acknowledges_command
        assert command.consume(_reply(HandSide.RIGHT, opcode, values)).acknowledged
        with pytest.raises(ProtocolError, match="did not echo"):
            command.consume(_reply(HandSide.RIGHT, opcode, (0,) * 6))

        read = backend.read_transaction(telemetry_semantic, capabilities)
        assert _request_data(read) == bytes((opcode,))
        payload = _payload(read, _reply(HandSide.RIGHT, opcode, (1,) * 6))
        assert payload.semantic is telemetry_semantic
        assert payload.origin is DataOrigin.DEVICE_CONFIG_RESPONSE


def test_o6_parameter_save_requires_cf_01_response() -> None:
    capabilities = get_declared_capabilities("o6", side="right")
    transaction = O6ProtocolBackend(HandSide.RIGHT).operation_transaction(
        "save_parameters",
        capabilities,
    )

    assert _request_data(transaction) == bytes((0xCF,)) * 8
    assert transaction.response_acknowledges_command
    assert transaction.consume(
        can.Message(
            arbitration_id=classic_route(HandSide.RIGHT).arbitration_id,
            data=b"\xcf\x01",
            is_extended_id=False,
        )
    ).acknowledged
    with pytest.raises(ProtocolError, match="was not CF 01"):
        transaction.consume(
            can.Message(
                arbitration_id=classic_route(HandSide.RIGHT).arbitration_id,
                data=b"\xcf\x00",
                is_extended_id=False,
            )
        )


def test_unsolicited_replies_preserve_semantics_without_active_query() -> None:
    l6 = get_declared_capabilities("l6", side="left")
    o6 = get_declared_capabilities("o6", side="right")

    l6_payload = L6ProtocolBackend(HandSide.LEFT).decode_report(
        _reply(HandSide.LEFT, 0x01, (9,) * 6),
        l6,
    )[0]
    o6_payload = O6ProtocolBackend(HandSide.RIGHT).decode_report(
        _reply(HandSide.RIGHT, 0x05, (8,) * 6),
        o6,
    )[0]

    assert l6_payload.semantic is TelemetrySemantic.ANGLES
    assert l6_payload.origin is DataOrigin.DEVICE_REPORT
    assert o6_payload.semantic is TelemetrySemantic.CONFIGURED_SPEEDS
    assert o6_payload.origin is DataOrigin.DEVICE_CONFIG_RESPONSE


def test_fixed_width_response_rejects_truncated_matching_opcode() -> None:
    capabilities = get_declared_capabilities("o6", side="right")
    transaction = O6ProtocolBackend(HandSide.RIGHT).read_transaction(
        TelemetrySemantic.ANGLES,
        capabilities,
    )

    with pytest.raises(ProtocolError, match="7 bytes"):
        transaction.consume(_reply(HandSide.RIGHT, 0x01, (1, 2)))


def test_baseline_includes_angles_faults_and_current_limits() -> None:
    for model, side, backend in (
        ("l6", "left", L6ProtocolBackend(HandSide.LEFT)),
        ("o6", "right", O6ProtocolBackend(HandSide.RIGHT)),
    ):
        capabilities = get_declared_capabilities(model, side=side)
        assert backend.baseline_semantics(capabilities) == (
            TelemetrySemantic.ANGLES,
            TelemetrySemantic.FAULTS,
            TelemetrySemantic.CURRENT_LIMITS,
        )


def _stray(side: HandSide, data: bytes) -> can.Message:
    return can.Message(
        arbitration_id=classic_route(side).arbitration_id,
        data=data,
        is_extended_id=False,
    )


@pytest.mark.parametrize(
    ("model", "backend_type"),
    (("l6", L6ProtocolBackend), ("o6", O6ProtocolBackend)),
)
def test_decode_report_returns_empty_tuple_on_opcode_collision(
    model: str,
    backend_type: type,
) -> None:
    """``decode_report`` must report "unmatched", never raise (base.py contract)."""

    side = HandSide.LEFT
    backend = backend_type(side)
    capabilities = get_declared_capabilities(model, side=side.value)

    # First byte matches the angles opcode but the frame is one byte too long,
    # which is how padded traffic from another master looks on the bus.
    assert (
        backend.decode_report(_stray(side, bytes([0x01]) + bytes(7)), capabilities)
        == ()
    )
    # A shorter collision is equally unmatched rather than a protocol error.
    assert backend.decode_report(_stray(side, bytes([0x01, 0x02])), capabilities) == ()


def test_active_read_still_rejects_a_wrong_length_response() -> None:
    """Relaxing report decoding must not relax the active response check."""

    side = HandSide.LEFT
    capabilities = get_declared_capabilities("l6", side=side.value)
    transaction = L6ProtocolBackend(side).read_transaction(
        TelemetrySemantic.ANGLES,
        capabilities,
    )

    with pytest.raises(ProtocolError, match="must contain 7 bytes"):
        transaction.consume(_stray(side, bytes([0x01]) + bytes(7)))
