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


@pytest.mark.parametrize(
    ("backend", "capabilities", "selector", "rows", "columns", "side"),
    (
        (
            L6ProtocolBackend(HandSide.LEFT),
            get_declared_capabilities("l6", side="left"),
            0xC6,
            12,
            6,
            HandSide.LEFT,
        ),
        (
            O6ProtocolBackend(HandSide.RIGHT),
            get_declared_capabilities("o6", side="right"),
            0xA4,
            10,
            4,
            HandSide.RIGHT,
        ),
    ),
)
@pytest.mark.parametrize("nibble_markers", (False, True))
def test_tactile_transaction_pipelines_and_atomically_assembles_all_fingers(
    backend,
    capabilities,
    selector: int,
    rows: int,
    columns: int,
    side: HandSide,
    nibble_markers: bool,
) -> None:
    transaction = backend.read_transaction(TelemetrySemantic.FORCE, capabilities)
    assert _requests(transaction) == tuple(
        bytes((opcode, selector)) for opcode in _FINGERS
    )
    assert transaction.request_interval_s == 0

    progress = None
    frames = [
        _frame(
            side,
            opcode,
            row << 4 if nibble_markers else row,
            bytes(
                (finger_index * rows + row + column) & 0xFF for column in range(columns)
            ),
        )
        for finger_index, opcode in enumerate(_FINGERS)
        for row in range(rows)
    ]
    for frame in reversed(frames):
        progress = transaction.consume(frame)
        if frame is not frames[0]:
            assert progress.result is None

    assert progress is not None and progress.complete
    assert isinstance(progress.result, SamplePayload)
    assert progress.result.semantic is TelemetrySemantic.FORCE
    assert len(progress.result.tactile) == 5
    first = progress.result.tactile[0]
    assert (first.rows, first.columns) == (rows, columns)
    assert first.raw[0] == tuple(range(columns))
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


def test_o6_profile_without_04_b_sensor_rejects_tactile_query() -> None:
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
