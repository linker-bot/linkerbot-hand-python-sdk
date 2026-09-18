"""Atomic complete-five-finger tactile transaction tests."""

from __future__ import annotations

from dataclasses import replace

import can
import pytest

from linkerbot_hand.api import HandSide
from linkerbot_hand.api.errors import ProtocolError, UnsupportedOperationError
from linkerbot_hand.capabilities.registry import get_declared_capabilities
from linkerbot_hand.capabilities.types import TelemetrySemantic
from linkerbot_hand.protocols import SamplePayload
from linkerbot_hand.protocols.common.classic_can import classic_route
from linkerbot_hand.protocols.l6 import L6ProtocolBackend
from linkerbot_hand.protocols.l20 import L20ProtocolBackend
from linkerbot_hand.protocols.l20lite import L20LiteProtocolBackend
from linkerbot_hand.protocols.o6 import O6ProtocolBackend

_FINGERS = (0xB1, 0xB2, 0xB3, 0xB4, 0xB5)


def _frame(
    side: HandSide,
    opcode: int,
    marker: int,
    payload: bytes,
) -> can.Message:
    return can.Message(
        arbitration_id=classic_route(side).arbitration_id,
        data=bytes((opcode, marker)) + payload,
        is_extended_id=False,
    )


def _requests(transaction) -> tuple[bytes, ...]:
    result: list[bytes] = []
    for message in transaction.request_messages:
        assert isinstance(message, can.Message)
        result.append(bytes(message.data))
    return tuple(result)


@pytest.mark.parametrize("nibble_markers", (False, True))
def test_o6_tactile_transaction_gates_each_finger_and_assembles_atomically(
    nibble_markers: bool,
) -> None:
    backend = O6ProtocolBackend(HandSide.RIGHT)
    capabilities = get_declared_capabilities("o6", side="right")
    selector = 0xA4
    rows = 10
    columns = 4
    side = HandSide.RIGHT
    transaction = backend.read_transaction(TelemetrySemantic.FORCE, capabilities)
    assert transaction.firmware_optional

    progress = None
    stage = transaction
    for finger_index, opcode in enumerate(_FINGERS):
        assert stage is not None
        assert _requests(stage) == (bytes((opcode, selector)),)
        assert stage.request_interval_s == 0
        assert stage.yield_after_response is (opcode != _FINGERS[-1])
        for row in reversed(range(rows)):
            progress = stage.consume(
                _frame(
                    side,
                    opcode,
                    row << 4 if nibble_markers else row,
                    bytes(
                        (finger_index * rows + row + column) & 0xFF
                        for column in range(columns)
                    ),
                )
            )
            assert progress.complete is (row == 0)
            if opcode != _FINGERS[-1] or row != 0:
                assert progress.result is None
        stage = stage.next_transaction

    assert stage is None
    assert progress is not None and progress.complete
    assert isinstance(progress.result, SamplePayload)
    assert progress.result.semantic is TelemetrySemantic.FORCE
    assert len(progress.result.tactile) == 5
    first = progress.result.tactile[0]
    assert (first.rows, first.columns) == (rows, columns)
    assert first.raw[0] == tuple(range(columns))
    assert progress.result.tactile[-1].name == "pinky"


@pytest.mark.parametrize("nibble_markers", (False, True))
@pytest.mark.parametrize(
    ("model", "backend_type"),
    (
        ("l6", L6ProtocolBackend),
        ("l20", L20ProtocolBackend),
        ("l20lite", L20LiteProtocolBackend),
    ),
)
def test_12_by_6_tactile_waits_for_each_finger_before_requesting_next(
    nibble_markers: bool,
    model,
    backend_type,
) -> None:
    capabilities = get_declared_capabilities(model, side="left")
    transaction = backend_type(HandSide.LEFT).read_transaction(
        TelemetrySemantic.FORCE,
        capabilities,
    )
    if model == "l6":
        assert transaction.firmware_optional

    progress = None
    stage = transaction
    for finger_index, opcode in enumerate(_FINGERS):
        assert stage is not None
        assert _requests(stage) == (bytes((opcode, 0xC6)),)
        assert stage.request_interval_s == 0
        assert stage.yield_after_response is (opcode != _FINGERS[-1])
        for row in reversed(range(12)):
            progress = stage.consume(
                _frame(
                    HandSide.LEFT,
                    opcode,
                    row << 4 if nibble_markers else row,
                    bytes(
                        (finger_index * 12 + row + column) & 0xFF for column in range(6)
                    ),
                )
            )
            assert progress.complete is (row == 0)
            if opcode != _FINGERS[-1] or row != 0:
                assert progress.result is None
        stage = stage.next_transaction

    assert stage is None
    assert progress is not None and progress.complete
    assert isinstance(progress.result, SamplePayload)
    assert len(progress.result.tactile) == 5
    assert progress.result.tactile[0].raw[0] == tuple(range(6))
    assert progress.result.tactile[-1].name == "pinky"


def test_tactile_transaction_accepts_identical_duplicate_rows() -> None:
    capabilities = get_declared_capabilities("o6", side="right")
    transaction = O6ProtocolBackend(HandSide.RIGHT).read_transaction(
        TelemetrySemantic.FORCE,
        capabilities,
    )
    frame = _frame(HandSide.RIGHT, 0xB1, 0, b"\x01\x02\x03\x04")

    assert transaction.consume(frame).matched
    assert transaction.consume(frame).matched


def test_tactile_transaction_rejects_conflicting_duplicate_rows() -> None:
    capabilities = get_declared_capabilities("l6", side="left")
    transaction = L6ProtocolBackend(HandSide.LEFT).read_transaction(
        TelemetrySemantic.FORCE,
        capabilities,
    )
    transaction.consume(_frame(HandSide.LEFT, 0xB1, 0, b"\x00" * 6))

    with pytest.raises(ProtocolError, match="conflicts"):
        transaction.consume(_frame(HandSide.LEFT, 0xB1, 0, b"\x01" * 6))


def test_tactile_transaction_rejects_wrong_dlc_and_row_marker() -> None:
    capabilities = get_declared_capabilities("o6", side="right")
    backend = O6ProtocolBackend(HandSide.RIGHT)

    with pytest.raises(ProtocolError, match="6 bytes"):
        backend.read_transaction(
            TelemetrySemantic.FORCE,
            capabilities,
        ).consume(_frame(HandSide.RIGHT, 0xB1, 0, b"\x01\x02"))
    with pytest.raises(ProtocolError, match="row marker"):
        backend.read_transaction(
            TelemetrySemantic.FORCE,
            capabilities,
        ).consume(_frame(HandSide.RIGHT, 0xB1, 0xF0, b"\x00" * 4))


def test_o6_tactile_response_of_another_layout_names_the_assembled_layout() -> None:
    backend = O6ProtocolBackend(HandSide.RIGHT)
    capabilities = get_declared_capabilities("o6", side="right")
    transaction = backend.read_transaction(TelemetrySemantic.FORCE, capabilities)

    # A hand whose matrix is not 10 by 4 answers with a different row width.
    # That is a ProtocolError, which the firmware-optional path counts towards
    # downgrading the channel rather than mis-slicing the data.
    with pytest.raises(ProtocolError, match="10 by 4 layout"):
        transaction.consume(_frame(HandSide.RIGHT, _FINGERS[0], 0, bytes(6)))
    assert transaction.firmware_optional


def test_o6_rejects_a_tactile_query_once_the_channel_is_unsupported() -> None:
    capabilities = get_declared_capabilities("o6", side="right")
    without_force = replace(
        capabilities,
        telemetry=tuple(
            channel
            for channel in capabilities.telemetry
            if channel.semantic is not TelemetrySemantic.FORCE
        ),
    )

    with pytest.raises(UnsupportedOperationError, match="tactile"):
        O6ProtocolBackend(HandSide.RIGHT).read_transaction(
            TelemetrySemantic.FORCE,
            without_force,
        )
