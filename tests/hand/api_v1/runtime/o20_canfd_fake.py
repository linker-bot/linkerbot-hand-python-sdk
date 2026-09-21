"""Scripted O20 CAN FD device for unified runtime integration tests."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from linkerbot_hand.api import HandSide, ProtocolError, ValidationError
from linkerbot_hand.protocols.o20 import codec
from linkerbot_transport import CANRouteKey, SendReceipt
from linkerbot_transport.canfd import CANFDMessage
from linkerbot_transport.testing import FakeTransport


@dataclass(slots=True)
class O20DeviceScript:
    """Deterministic O20 wire behavior expressed as real CAN FD messages."""

    side: HandSide
    serial_number: str = "SN-O20-0001"
    firmware: str = "V2.1.0"
    hardware: str = "H1.3"
    unique_id: bytes = bytes(range(11))
    sent_messages: list[CANFDMessage] = field(default_factory=list)
    command_messages: list[CANFDMessage] = field(default_factory=list)
    read_counts: Counter[int] = field(default_factory=Counter)
    identity_queries: int = 0
    readings: dict[int, bytes] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.readings.setdefault(
            codec.REG_ANGLES,
            codec.encode_i16_vector(tuple(range(10, 26))),
        )
        self.readings.setdefault(
            codec.REG_FAULTS,
            bytes(17),
        )
        self.readings.setdefault(
            codec.REG_MEASURED_SPEEDS,
            codec.encode_i16_vector(tuple(range(-8, 8))),
        )
        self.readings.setdefault(
            codec.REG_CONFIGURED_SPEEDS,
            codec.encode_i16_vector((50,) * codec.JOINT_COUNT),
        )
        self.readings.setdefault(
            codec.REG_CURRENT_LIMITS,
            codec.encode_u16_vector((400,) * codec.JOINT_COUNT),
        )
        self.readings.setdefault(
            codec.REG_TEMPERATURES,
            bytes(range(30, 47)),
        )
        self.readings.setdefault(
            codec.REG_CURRENTS,
            codec.encode_i16_vector(tuple(range(100, 116))),
        )
        for finger in range(5):
            front_register = codec.REG_TACTILE_FIRST + finger * 2
            matrix = bytes((finger * 72 + index) & 0xFF for index in range(72))
            self.readings.setdefault(front_register, b"\x01" + matrix[:63])
            self.readings.setdefault(front_register + 1, matrix[63:])

    @property
    def device_id(self) -> int:
        """Return the protocol device ID assigned to this side."""

        return codec.device_id_for_side(self.side)

    def respond(self, request: CANFDMessage) -> tuple[CANFDMessage, ...]:
        """Record one request and return its immediate scripted response."""

        if not request.is_extended_id:
            return ()
        try:
            device_id, register, write = codec.parse_can_id(request.arbitration_id)
        except (ProtocolError, ValidationError):
            return ()
        if device_id != self.device_id:
            return ()
        self.sent_messages.append(request)
        if write:
            self.command_messages.append(request)
            self._apply_write(register, request.data)
            return ()
        self.read_counts[register] += 1
        if register == codec.REG_DEVICE_INFO:
            self.identity_queries += 1
            data = self._device_info()
        else:
            data = self.readings.get(register)
        if data is None:
            return ()
        return (
            CANFDMessage(
                arbitration_id=request.arbitration_id,
                data=data,
                is_extended_id=True,
            ),
        )

    def _apply_write(self, register: int, data: bytes) -> None:
        if register == codec.REG_TARGET_ANGLES:
            self.readings[codec.REG_ANGLES] = data
        elif register in {
            codec.REG_CONFIGURED_SPEEDS,
            codec.REG_CURRENT_LIMITS,
            codec.REG_FAULTS,
        }:
            self.readings[register] = data

    def _device_info(self) -> bytes:
        product = "O20-L" if self.side is HandSide.LEFT else "O20-R"
        side_code = 2 if self.side is HandSide.LEFT else 1
        return (
            product.encode("ascii").ljust(10, b"\x00")
            + self.serial_number.encode("ascii").ljust(20, b"\x00")
            + self.firmware.encode("ascii").ljust(10, b"\x00")
            + self.hardware.encode("ascii").ljust(10, b"\x00")
            + bytes((side_code,))
            + self.unique_id
        )


class ScriptedO20Transport(FakeTransport[CANFDMessage]):
    """Managed fake exchanging genuine ``CANFDMessage`` values."""

    def __init__(self, script: O20DeviceScript) -> None:
        super().__init__(route_key_of=_route_key)
        self.script = script

    def send(self, message: CANFDMessage) -> SendReceipt:
        receipt = super().send(message)
        if receipt.exception() is None:
            for response in self.script.respond(message):
                self.emit(response)
        return receipt


def _route_key(message: CANFDMessage) -> CANRouteKey:
    return CANRouteKey(message.arbitration_id, message.is_extended_id)


__all__ = ["O20DeviceScript", "ScriptedO20Transport"]
