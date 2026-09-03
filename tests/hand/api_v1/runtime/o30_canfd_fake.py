"""Scripted O30 HOP endpoint for unified runtime integration tests."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from linkerbot_hand.protocols.o30 import codec
from linkerbot_hand.protocols.o30.framing import (
    ERROR_MAIN_INDEX,
    FRAME_TYPE,
    MAIN_INDEX_MASK,
    REQUEST_ID,
    RESPONSE_ID,
    RTS_MASK,
)
from linkerbot_transport import CANRouteKey, SendReceipt
from linkerbot_transport.canfd import CANFDMessage
from linkerbot_transport.testing import FakeTransport

_WRITABLE_OBJECTS = {
    codec.MI_POSITION,
    codec.MI_SPEED,
    codec.MI_ACCELERATION,
    codec.MI_EFFORT,
    codec.MI_MOTION_TIME,
    codec.MI_STALL_TIME,
    codec.MI_STALL_THRESHOLD,
    codec.MI_STALL_HOLD_CURRENT,
}


@dataclass(slots=True)
class O30DeviceScript:
    """Deterministic O30 behavior using real HOP CAN FD messages."""

    device_uid: str = "DEVICE-UID-O30"
    product_model: str = "O30"
    protocol_version: str = "0.0.3"
    application_version: str = "1.1.0"
    sensor_variant: str = "NO_SENSOR"
    sent_messages: list[CANFDMessage] = field(default_factory=list)
    command_messages: list[CANFDMessage] = field(default_factory=list)
    read_counts: Counter[tuple[int, bool]] = field(default_factory=Counter)
    identity_queries: int = 0
    actual: dict[int, bytes] = field(default_factory=dict)
    targets: dict[int, bytes] = field(default_factory=dict)

    def __post_init__(self) -> None:
        defaults = {
            codec.MI_POSITION: tuple(range(20)),
            codec.MI_SPEED: tuple(range(30, 50)),
            codec.MI_ACCELERATION: (5,) * 20,
            codec.MI_CURRENT: tuple(range(80, 100)),
            codec.MI_VOLTAGE: (12,) * 20,
            codec.MI_EFFORT: (20,) * 20,
            codec.MI_TEMPERATURE: tuple(range(25, 45)),
            codec.MI_MOTION_TIME: (10,) * 20,
            codec.MI_STALL_TIME: (11,) * 20,
            codec.MI_STALL_THRESHOLD: (12,) * 20,
            codec.MI_STALL_HOLD_CURRENT: (13,) * 20,
            codec.MI_FAULT: (0,) * 20,
        }
        for main_index, values in defaults.items():
            encoded = codec.encode_v25(values)
            self.actual.setdefault(main_index, encoded)
            self.targets.setdefault(main_index, encoded)

    def respond(self, request: CANFDMessage) -> tuple[CANFDMessage, ...]:
        """Record one request and return its immediate scripted response."""

        if (
            request.arbitration_id != REQUEST_ID
            or request.is_extended_id
            or len(request.data) < 3
        ):
            return ()
        control, sub_index, length = request.data[:3]
        main_index = control & MAIN_INDEX_MASK
        rts = bool(control & RTS_MASK)
        self.sent_messages.append(request)
        if len(request.data) > 3:
            self.command_messages.append(request)
            return self._write(main_index, sub_index, length, request.data[3:])
        self.read_counts[(main_index, rts)] += 1
        return self._read(main_index, sub_index, length, rts=rts)

    def _write(
        self,
        main_index: int,
        sub_index: int,
        length: int,
        payload: bytes,
    ) -> tuple[CANFDMessage, ...]:
        if len(payload) < length or main_index not in _WRITABLE_OBJECTS:
            return (self._error(sub_index, 0x02),)
        value = payload[:length]
        if sub_index != 0 or length != codec.WIRE_SLOT_COUNT:
            return (self._error(sub_index, 0x02),)
        if any(value[index] for index in (1, 2, 3, 4, 15)):
            return (self._error(sub_index, 0x40),)
        self.targets[main_index] = value
        if main_index == codec.MI_POSITION:
            self.actual[main_index] = value
        return (self._response(main_index, sub_index, value),)

    def _read(
        self,
        main_index: int,
        sub_index: int,
        length: int,
        *,
        rts: bool,
    ) -> tuple[CANFDMessage, ...]:
        source = self._identity_object(main_index)
        if source is None:
            source = (self.targets if rts else self.actual).get(main_index)
        if source is None or sub_index + length > len(source):
            return (self._error(sub_index, 0x02),)
        if main_index == codec.MI_PRODUCT_INFO and sub_index == 0:
            self.identity_queries += 1
        return (
            self._response(
                main_index, sub_index, source[sub_index : sub_index + length], rts=rts
            ),
        )

    def _identity_object(self, main_index: int) -> bytes | None:
        if main_index == codec.MI_PRODUCT_INFO:
            data = bytearray(225)
            fields = (
                (0x00, 0x10, self.product_model),
                (0x39, 0x20, self.device_uid),
                (0x59, 0x08, "HOP"),
                (0x61, 0x08, self.protocol_version),
                (0x69, 0x08, "V1.2.0"),
                (0x89, 0x08, self.application_version),
                (0x91, 0x08, "M1.0.0"),
            )
            for start, length, value in fields:
                encoded = value.encode("ascii")
                data[start : start + min(length, len(encoded))] = encoded[:length]
            return bytes(data)
        if main_index == codec.MI_SENSOR_INFO:
            data = bytearray(50)
            encoded = self.sensor_variant.encode("ascii")
            data[: min(32, len(encoded))] = encoded[:32]
            return bytes(data)
        return None

    def _error(self, sub_index: int, code: int) -> CANFDMessage:
        return self._response(ERROR_MAIN_INDEX, sub_index, bytes((code,)))

    def _response(
        self,
        main_index: int,
        sub_index: int,
        payload: bytes,
        *,
        rts: bool = False,
    ) -> CANFDMessage:
        control = main_index | (RTS_MASK if rts else 0)
        return CANFDMessage(
            arbitration_id=RESPONSE_ID,
            data=bytes((control, sub_index, len(payload))) + payload,
            is_extended_id=False,
            frame_type=FRAME_TYPE,
        )


class ScriptedO30Transport(FakeTransport[CANFDMessage]):
    """Managed fake exchanging genuine O30 HOP messages."""

    def __init__(self, script: O30DeviceScript) -> None:
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


__all__ = ["O30DeviceScript", "ScriptedO30Transport"]
