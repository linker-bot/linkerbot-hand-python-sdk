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
    """Deterministic classic-hand wire behavior expressed only as CAN frames."""

    model: str
    side: HandSide
    product_code: str
    firmware: tuple[int, int, int]
    readings: dict[int, tuple[int, ...]] = field(default_factory=dict)
    sent_payloads: list[bytes] = field(default_factory=list)
    command_payloads: list[bytes] = field(default_factory=list)
    read_counts: Counter[int] = field(default_factory=Counter)
    identity_queries: int = 0
    unsupported_queries: set[int] = field(default_factory=set)
    malformed_query_responses: dict[int, bytes] = field(default_factory=dict)
    bad_command_echoes: dict[int, tuple[int, ...]] = field(default_factory=dict)
    dropped_command_responses: set[int] = field(default_factory=set)

    def __post_init__(self) -> None:
        if self.model not in {"L6", "O6", "L20Lite", "L20"}:
            raise ValueError("unsupported classic hand script model")
        for opcode, values in _default_readings(self.model).items():
            self.readings.setdefault(opcode, values)

    @property
    def arbitration_id(self) -> int:
        """Return the protocol's fixed standard-frame identifier for this side."""

        return 0x28 if self.side is HandSide.LEFT else 0x27

    @property
    def tactile_shape(self) -> tuple[int, int]:
        """Return rows and columns for the selected model's five sensors."""

        return (10, 4) if self.model == "O6" else (12, 6)

    @property
    def tactile_selector(self) -> int:
        """Return the protocol selector for one full tactile matrix."""

        return 0xA4 if self.model == "O6" else 0xC6

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
        if data == bytes((0xCF,)) * 8:
            self.command_payloads.append(data)
            return (self._frame(b"\xcf\x01"),)
        if len(data) == 1 and data[0] in self.readings:
            opcode = data[0]
            self.read_counts[opcode] += 1
            if opcode in self.unsupported_queries:
                return ()
            if opcode in self.malformed_query_responses:
                return (self._frame(self.malformed_query_responses[opcode]),)
            return (self._frame(bytes((opcode, *self.readings[opcode]))),)
        if (
            len(data) == 2
            and data[0] in _FINGER_OPCODES
            and data[1] == self.tactile_selector
        ):
            self.read_counts[data[1]] += 1
            if (
                data[0] in self.unsupported_queries
                or data[1] in self.unsupported_queries
            ):
                return ()
            return self._tactile_frames(data[0])
        if len(data) > 1 and data[0] in self.readings:
            self.command_payloads.append(data)
            if self.model == "L20" and data[0] in range(0x59, 0x5E):
                self.readings[data[0]] = (0,) * 6
            else:
                self.readings[data[0]] = tuple(data[1:])
            angle_opcodes = (
                {0x01}
                if self.model in {"L6", "O6"}
                else {0x01, 0x04}
                if self.model == "L20Lite"
                else set(range(0x41, 0x46))
            )
            config_echo_opcodes = (
                {0x02, 0x05, 0x38, 0xC5, 0xC6, 0xC7}
                if self.model == "L6"
                else {0x02, 0x05, 0x87, 0xC5, 0xC6, 0xC7}
                if self.model == "O6"
                else set()
            )
            echo_opcodes = angle_opcodes | config_echo_opcodes
            if data[0] in echo_opcodes:
                if data[0] in self.dropped_command_responses:
                    return ()
                echoed = self.bad_command_echoes.get(
                    data[0],
                    self.readings[data[0]],
                )
                return (self._frame(bytes((data[0], *echoed))),)
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
    if model == "L20Lite":
        return ClassicCANDeviceScript(
            model=model,
            side=side,
            product_code=f"LHT10-07-098-{side_code}-A-1-C",
            firmware=(2, 5, 1),
        )
    if model == "L20":
        return ClassicCANDeviceScript(
            model=model,
            side=side,
            product_code=f"LHG20-010-001-{side_code}-Z-1-A",
            firmware=(3, 0, 2),
        )
    raise ValueError("unsupported classic hand script model")


def _default_readings(model: str) -> dict[int, tuple[int, ...]]:
    if model in {"L6", "O6"}:
        readings = {
            0x01: (10, 20, 30, 40, 50, 60),
            0x02: (80,) * 6,
            0x05: (90,) * 6,
            0x33: (30, 31, 32, 33, 34, 35),
            0x35: (0,) * 6,
            0x36: (1, 2, 3, 4, 5, 6),
            0x87: (20,) * 6,
        }
        if model == "L6":
            readings.update(
                {
                    0x38: (0, 0, 227, 223, 208, 207),
                    0xC5: (5,) * 6,
                    0xC6: (91,) * 6,
                    0xC7: (106,) * 6,
                }
            )
        elif model == "O6":
            readings.update(
                {
                    0xC5: (128,) * 6,
                    0xC6: (5,) * 6,
                    0xC7: (118,) * 6,
                }
            )
        return readings
    if model == "L20Lite":
        return {
            0x01: tuple(range(10, 16)),
            0x04: tuple(range(16, 20)),
            0x02: (80,) * 5,
            0x03: (80,) * 5,
            0x05: (90,) * 5,
            0x06: (90,) * 5,
            0x33: tuple(range(30, 35)),
            0x34: tuple(range(35, 40)),
            0x35: (0,) * 5,
            0x36: (0,) * 5,
        }
    if model == "L20":
        layouts = {
            0x41: (10, 11, 12, 0, 0, 13),
            0x42: (14, 0, 15, 0, 0, 16),
            0x43: (17, 0, 18, 0, 0, 19),
            0x44: (20, 0, 21, 0, 0, 22),
            0x45: (23, 0, 24, 0, 0, 25),
        }
        return {
            **layouts,
            **{opcode: (90,) * 6 for opcode in range(0x49, 0x4E)},
            **{opcode: (80,) * 6 for opcode in range(0x51, 0x56)},
            **{opcode: (0,) * 6 for opcode in range(0x59, 0x5E)},
            **{opcode: (30 + opcode - 0x61,) * 6 for opcode in range(0x61, 0x66)},
        }
    raise ValueError("unsupported classic hand script model")


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
