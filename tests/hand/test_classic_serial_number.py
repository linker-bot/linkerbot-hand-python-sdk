"""Regression tests for classic CAN serial-number response assembly."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import can
import pytest

from linkerbot.hand._serial_number import SerialNumberCollector, SerialNumberFrames
from linkerbot.hand.l6.version import VersionManager as L6VersionManager
from linkerbot.hand.l20lite.version import VersionManager as L20LiteVersionManager
from linkerbot.hand.l25.version import VersionManager as L25VersionManager
from linkerbot.hand.o6.version import VersionManager as O6VersionManager
from linkerbot_transport.errors import TimeoutError


class _SerialDispatcher:
    """Return configured serial-number fragments for a manager request."""

    def __init__(self, fragments: Sequence[tuple[int, bytes]]) -> None:
        self._fragments = fragments
        self._callback: Callable[[can.Message], None] | None = None

    def subscribe(self, callback: Callable[[can.Message], None]) -> None:
        self._callback = callback

    def send(self, message: can.Message) -> None:
        if message.data[0] != 0xC0:
            return
        assert self._callback is not None
        for position, payload in self._fragments:
            self._callback(
                can.Message(
                    arbitration_id=message.arbitration_id,
                    data=[0xC0, position, *payload],
                    is_extended_id=False,
                )
            )


@pytest.mark.parametrize(
    "positions",
    (
        (0, 1, 2, 3),
        (0, 6, 12, 18),
    ),
)
def test_frames_accept_frame_numbers_and_byte_offsets(
    positions: tuple[int, ...],
) -> None:
    frames = SerialNumberFrames()
    for position, payload in zip(
        positions, (b"ABCDEF", b"GHIJKL", b"MNOPQR", b"ST\0\0\0\0")
    ):
        frames = frames.add_frame(position, payload)

    assert frames.is_complete()
    assert frames.assemble() == "ABCDEFGHIJKLMNOPQRST"


@pytest.mark.parametrize(
    "manager_type",
    (
        L6VersionManager,
        O6VersionManager,
        L20LiteVersionManager,
        L25VersionManager,
    ),
)
@pytest.mark.parametrize("positions", ((0, 1, 2, 3), (0, 6, 12, 18)))
def test_all_classic_managers_accept_both_position_encodings(
    manager_type: type[Any],
    positions: tuple[int, ...],
) -> None:
    fragments = tuple(zip(positions, (b"ABCDEF", b"GHIJKL", b"MNOPQR", b"ST\0\0\0\0")))
    manager = manager_type(0x28, _SerialDispatcher(fragments))

    assert manager._get_serial_number_blocking(20) == "ABCDEFGHIJKLMNOPQRST"


def test_frames_assemble_out_of_order_duplicate_fragments() -> None:
    frames = SerialNumberFrames()
    for position, payload in (
        (2, b"MNOPQR"),
        (0, b"ABCDEF"),
        (3, b"ST\0\0\0\0"),
        (1, b"GHIJKL"),
        (1, b"GHIJKL"),
    ):
        frames = frames.add_frame(position, payload)

    assert frames.is_complete()
    assert frames.assemble() == "ABCDEFGHIJKLMNOPQRST"


@pytest.mark.parametrize("positions", ((0, 1, 2), (0, 6, 12)))
def test_collector_accepts_three_contiguous_l6_frames_after_wait(
    positions: tuple[int, ...],
) -> None:
    collector = SerialNumberCollector()
    collector.begin()
    for position, payload in zip(positions, (b"LHL6 -", b"03-077", b"-L-B-1")):
        collector.add_frame(position, payload)

    assert collector.wait(0.001) == "LHL6 -03-077-L-B-1"


def test_collector_rejects_short_response_with_a_missing_middle_frame() -> None:
    collector = SerialNumberCollector()
    collector.begin()
    collector.add_frame(0, b"ABCDEF")
    collector.add_frame(12, b"MNOPQR")
    collector.add_frame(18, b"STUVWX")

    with pytest.raises(TimeoutError, match="within 1ms"):
        collector.wait(0.001)


def test_collector_ignores_fragments_outside_both_position_encodings() -> None:
    collector = SerialNumberCollector()
    collector.begin()
    collector.add_frame(0, b"ABCDEF")
    collector.add_frame(5, b"GHIJKL")
    collector.add_frame(12, b"MNOPQR")
    collector.add_frame(18, b"STUVWX")

    with pytest.raises(TimeoutError, match="within 1ms"):
        collector.wait(0.001)


def test_collector_request_cleans_state_after_send_error() -> None:
    collector = SerialNumberCollector()
    error = RuntimeError("send failed")

    def fail() -> None:
        raise error

    with pytest.raises(RuntimeError) as raised:
        collector.request(fail, 0.01)
    assert raised.value is error

    def respond() -> None:
        for position, payload in (
            (0, b"ABCDEF"),
            (1, b"GHIJKL"),
            (2, b"MNOPQR"),
            (3, b"ST\0\0\0\0"),
        ):
            collector.add_frame(position, payload)

    assert collector.request(respond, 0.01) == "ABCDEFGHIJKLMNOPQRST"
