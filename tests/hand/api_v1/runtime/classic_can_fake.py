"""Scripted classic CAN device used by production-profile runtime tests."""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

import can

from linkerbot_hand.api import HandSide
from linkerbot_transport import CANRouteKey, SendReceipt
from linkerbot_transport.testing import FakeTransport

_FINGER_OPCODES = (0xB1, 0xB2, 0xB3, 0xB4, 0xB5)


@dataclass(slots=True)
class ClassicCANDeviceScript:
    """Deterministic L6/O6 wire behavior expressed only as CAN frames."""

    model: str
    side: HandSide
    product_code: str
    firmware: tuple[int, int, int]
    readings: dict[int, tuple[int, ...]] = field(default_factory=dict)
    sent_payloads: list[bytes] = field(default_factory=list)
    command_payloads: list[bytes] = field(default_factory=list)
    read_counts: Counter[int] = field(default_factory=Counter)
    identity_queries: int = 0

    def __post_init__(self) -> None:
        if self.model not in {"L6", "O6"}:
            raise ValueError("script model must be L6 or O6")
        self.readings.setdefault(0x01, (10, 20, 30, 40, 50, 60))
        self.readings.setdefault(0x02, (80,) * 6)
        self.readings.setdefault(0x05, (90,) * 6)
        self.readings.setdefault(0x33, (30, 31, 32, 33, 34, 35))
        self.readings.setdefault(0x35, (0,) * 6)
        self.readings.setdefault(0x36, (1, 2, 3, 4, 5, 6))
        self.readings.setdefault(0x87, (20,) * 6)

    @property
    def arbitration_id(self) -> int:
        """Return the protocol's fixed standard-frame identifier for this side."""

        return 0x28 if self.side is HandSide.LEFT else 0x27

    @property
    def tactile_shape(self) -> tuple[int, int]:
        """Return rows and columns for the selected model's five sensors."""

        return (12, 6) if self.model == "L6" else (10, 4)

    @property
    def tactile_selector(self) -> int:
        """Return the protocol selector for one full tactile matrix."""

        return 0xC6 if self.model == "L6" else 0xA4

    def respond(self, request: can.Message) -> tuple[can.Message, ...]:
        """Record one request and return its immediate scripted device frames."""

        if (
            request.arbitration_id != self.arbitration_id
            or request.is_extended_id
            or request.is_fd
        ):
            return ()
        data = bytes(request.data)
        self.sent_payloads.append(data)
        if data == b"\xc0":
            self.identity_queries += 1
            encoded = self.product_code.encode("ascii")
            return tuple(
                self._frame(bytes((0xC0, offset // 6)) + encoded[offset : offset + 6])
                for offset in range(0, len(encoded), 6)
            )
        if data in {b"\xc1", b"\xc1\x01"}:
            payload = (
                b"\xc1\x01\x04\x00\x01" if self.model == "L6" else b"\xc1\x04\x00\x01"
            )
            return (self._frame(payload),)
        if data == b"\xc2":
            return (self._frame(bytes((0xC2, *self.firmware))),)
        if data == b"\xc4":
            return (self._frame(b"\xc4\x01\x00\x02"),)
        if len(data) == 1 and data[0] in self.readings:
            opcode = data[0]
            self.read_counts[opcode] += 1
            return (self._frame(bytes((opcode, *self.readings[opcode]))),)
        if (
            len(data) == 2
            and data[0] in _FINGER_OPCODES
            and data[1] == self.tactile_selector
        ):
            self.read_counts[data[1]] += 1
            return self._tactile_frames(data[0])
        if len(data) == 7 and data[0] in {0x01, 0x02, 0x05, 0x87}:
            self.command_payloads.append(data)
            self.readings[data[0]] = tuple(data[1:])
            return ()
        if len(data) == 7 and data[0] == 0x83:
            self.command_payloads.append(data)
            self.readings[0x35] = (0,) * 6
        return ()

    def _tactile_frames(self, opcode: int) -> tuple[can.Message, ...]:
        rows, columns = self.tactile_shape
        finger = _FINGER_OPCODES.index(opcode)
        return tuple(
            self._frame(
                bytes((opcode, row))
                + bytes(
                    (finger * rows + row + column) & 0xFF for column in range(columns)
                )
            )
            for row in reversed(range(rows))
        )

    def _frame(self, data: bytes) -> can.Message:
        return can.Message(
            arbitration_id=self.arbitration_id,
            data=data,
            is_extended_id=False,
        )


class ScriptedClassicCANTransport(FakeTransport[can.Message]):
    """Managed fake transport that exchanges genuine ``can.Message`` values."""

    def __init__(self, script: ClassicCANDeviceScript) -> None:
        super().__init__(route_key_of=_route_key)
        self.script = script

    def send(self, message: can.Message) -> SendReceipt:
        receipt = super().send(message)
        if receipt.exception() is None:
            for response in self.script.respond(message):
                self.emit(response)
        return receipt


def make_script(model: str, side: HandSide) -> ClassicCANDeviceScript:
    """Create one valid identity and state script for a migrated model."""

    side_code = "L" if side is HandSide.LEFT else "R"
    if model == "L6":
        return ClassicCANDeviceScript(
            model=model,
            side=side,
            product_code=f"LHL6-04-077-{side_code}-B-1-A",
            firmware=(2, 4, 0),
        )
    if model == "O6":
        return ClassicCANDeviceScript(
            model=model,
            side=side,
            product_code=f"LHO6-04.1-730-{side_code}-B-1-D",
            firmware=(3, 1, 2),
        )
    raise ValueError("script model must be L6 or O6")


def wait_until(predicate: Callable[[], bool], *, timeout_s: float = 1.0) -> None:
    """Wait for an asynchronous runtime observation with a bounded deadline."""

    deadline = time.monotonic() + timeout_s
    while not predicate():
        if time.monotonic() >= deadline:
            raise TimeoutError("condition not met before deadline")
        time.sleep(0.001)


def _route_key(message: can.Message) -> CANRouteKey:
    return CANRouteKey(message.arbitration_id, message.is_extended_id)


__all__ = [
    "ClassicCANDeviceScript",
    "ScriptedClassicCANTransport",
    "make_script",
    "wait_until",
]
