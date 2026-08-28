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


def test_l6_commands_keep_speed_cache_and_current_limit_separate() -> None:
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
    assert (
        speeds.dispatch_publications[0].semantic is TelemetrySemantic.CONFIGURED_SPEEDS
    )
    assert speeds.dispatch_publications[0].origin is DataOrigin.COMMAND_CACHE
    assert _request_data(limits) == b"\x02" + bytes((90,) * 6)
    assert not limits.dispatch_publications
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
        read = backend.read_transaction(telemetry_semantic, capabilities)
        payload = _payload(read, _reply(HandSide.RIGHT, opcode, (42,) * 6))
        assert payload.semantic is telemetry_semantic
        assert payload.origin is DataOrigin.DEVICE_CONFIG_RESPONSE

    assert capabilities.telemetry_channel(TelemetrySemantic.MEASURED_SPEEDS) is None
    assert capabilities.telemetry_channel(TelemetrySemantic.TORQUES) is None


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
