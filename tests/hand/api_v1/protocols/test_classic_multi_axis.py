"""L20Lite/L25 classic CAN multi-axis protocol contract tests."""

from __future__ import annotations

import can
import pytest

from linkerbot_hand.api import DataOrigin, HandSide
from linkerbot_hand.api.errors import ProtocolError
from linkerbot_hand.capabilities.registry import get_declared_capabilities
from linkerbot_hand.capabilities.types import CommandSemantic, TelemetrySemantic
from linkerbot_hand.protocols import ProtocolTransaction, SamplePayload
from linkerbot_hand.protocols.common.classic_can import classic_route
from linkerbot_hand.protocols.l20lite import L20LiteProtocolBackend
from linkerbot_hand.protocols.l25 import L25ProtocolBackend


def _message(side: HandSide, data: bytes) -> can.Message:
    return can.Message(
        arbitration_id=classic_route(side).arbitration_id,
        data=data,
        is_extended_id=False,
    )


def _request_data(transaction: ProtocolTransaction) -> tuple[bytes, ...]:
    result = []
    for message in transaction.request_messages:
        assert isinstance(message, can.Message)
        result.append(bytes(message.data))
    return tuple(result)


def _consume_all(
    transaction: ProtocolTransaction,
    messages: tuple[can.Message, ...],
) -> SamplePayload:
    progress = None
    for message in messages:
        progress = transaction.consume(message)
    assert progress is not None and progress.complete
    assert isinstance(progress.result, SamplePayload)
    return progress.result


def test_l20lite_commands_preserve_documented_split_and_public_order() -> None:
    capabilities = get_declared_capabilities("l20lite", side="left")
    backend = L20LiteProtocolBackend(HandSide.LEFT)
    values = tuple(range(10))

    angles = backend.command_transaction(CommandSemantic.ANGLES, values, capabilities)
    speeds = backend.command_transaction(CommandSemantic.SPEEDS, values, capabilities)
    effort = backend.command_transaction(
        CommandSemantic.EFFORT_LIMITS, values, capabilities
    )

    assert _request_data(angles) == (
        b"\x01\x00\x01\x02\x03\x04\x05",
        b"\x04\x06\x07\x08\x09",
    )
    assert _request_data(speeds) == (
        b"\x05\x00\x01\x02\x03\x04",
        b"\x06\x05\x06\x07\x08\x09",
    )
    assert _request_data(effort) == (
        b"\x02\x00\x01\x02\x03\x04",
        b"\x03\x05\x06\x07\x08\x09",
    )
    assert angles.expects_response
    echo = _consume_all(
        angles,
        tuple(
            _message(HandSide.LEFT, payload)
            for payload in reversed(_request_data(angles))
        ),
    )
    assert echo.raw == values
    assert echo.origin is DataOrigin.DEVICE_COMMAND_FEEDBACK
    assert not angles.terminal_acknowledges_command
    assert angles.request_interval_s == pytest.approx(0.0005)


@pytest.mark.parametrize(
    ("semantic", "responses", "origin"),
    (
        (
            TelemetrySemantic.ANGLES,
            (b"\x04\x06\x07\x08\x09", b"\x01\x00\x01\x02\x03\x04\x05"),
            DataOrigin.DEVICE_RESPONSE,
        ),
        (
            TelemetrySemantic.CONFIGURED_SPEEDS,
            (b"\x06\x05\x06\x07\x08\x09", b"\x05\x00\x01\x02\x03\x04"),
            DataOrigin.DEVICE_CONFIG_RESPONSE,
        ),
        (
            TelemetrySemantic.EFFORT_LIMITS,
            (b"\x03\x05\x06\x07\x08\x09", b"\x02\x00\x01\x02\x03\x04"),
            DataOrigin.DEVICE_CONFIG_RESPONSE,
        ),
    ),
)
def test_l20lite_reads_publish_only_complete_out_of_order_objects(
    semantic: TelemetrySemantic,
    responses: tuple[bytes, ...],
    origin: DataOrigin,
) -> None:
    capabilities = get_declared_capabilities("l20lite", side="left")
    transaction = L20LiteProtocolBackend(HandSide.LEFT).read_transaction(
        semantic, capabilities
    )

    first = transaction.consume(_message(HandSide.LEFT, responses[0]))
    assert first.matched and not first.complete
    payload = _consume_all(
        transaction,
        (_message(HandSide.LEFT, responses[1]),),
    )

    assert payload.raw == tuple(range(10))
    assert payload.origin is origin


def test_l25_reserved_slots_are_zero_filled_and_never_public() -> None:
    capabilities = get_declared_capabilities("l25", side="right")
    backend = L25ProtocolBackend(HandSide.RIGHT)
    values = tuple(range(16))
    command = backend.command_transaction(CommandSemantic.ANGLES, values, capabilities)

    assert _request_data(command) == (
        b"\x41\x00\x01\x02\x00\x00\x03",
        b"\x42\x04\x00\x05\x00\x00\x06",
        b"\x43\x07\x00\x08\x00\x00\x09",
        b"\x44\x0a\x00\x0b\x00\x00\x0c",
        b"\x45\x0d\x00\x0e\x00\x00\x0f",
    )

    transaction = backend.read_transaction(TelemetrySemantic.ANGLES, capabilities)
    replies = tuple(
        _message(
            HandSide.RIGHT,
            bytes(
                (
                    payload[0],
                    *(
                        0xEE if index in {2, 4, 5} and payload[0] != 0x41 else value
                        for index, value in enumerate(payload[1:], start=1)
                    ),
                )
            ),
        )
        for payload in reversed(_request_data(command))
    )
    payload = _consume_all(transaction, replies)

    assert payload.raw == values


def test_l25_config_reads_and_clear_faults_have_exact_semantics() -> None:
    capabilities = get_declared_capabilities("l25", side="right")
    backend = L25ProtocolBackend(HandSide.RIGHT)
    speed = backend.read_transaction(TelemetrySemantic.CONFIGURED_SPEEDS, capabilities)
    clear = backend.operation_transaction("clear_faults", capabilities)

    assert _request_data(speed) == tuple(
        bytes((opcode,)) for opcode in range(0x49, 0x4E)
    )
    assert _request_data(clear) == tuple(
        bytes((opcode, 1, 1, 1, 1, 1, 1)) for opcode in range(0x59, 0x5E)
    )
    assert not clear.expects_response


def test_matching_multi_axis_fragment_rejects_a_wrong_dlc() -> None:
    capabilities = get_declared_capabilities("l20lite", side="left")
    transaction = L20LiteProtocolBackend(HandSide.LEFT).read_transaction(
        TelemetrySemantic.ANGLES, capabilities
    )

    with pytest.raises(ProtocolError, match="7 bytes"):
        transaction.consume(_message(HandSide.LEFT, b"\x01\x00"))
