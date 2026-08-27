"""Scripted L30 CAN FD device for unified runtime integration tests."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from linkerbot_hand.api import ProtocolError, ValidationError
from linkerbot_hand.protocols.l30 import codec
from linkerbot_hand.protocols.l30.framing import (
    ACCESS_READ,
    ACCESS_WRITE,
    build_can_id,
    parse_can_id,
)
from linkerbot_transport import CANRouteKey, SendReceipt
from linkerbot_transport.canfd import CANFDMessage
from linkerbot_transport.testing import FakeTransport


@dataclass(slots=True)
class L30DeviceScript:
    """Deterministic L30 wire behavior expressed as genuine CAN FD messages."""

    serial_number: int = 1
    product_version: str = "06"
    side_code: int = 0
    sensor_variant: str = "B"
    origin: str = "A"
    sent_messages: list[CANFDMessage] = field(default_factory=list)
    command_messages: list[CANFDMessage] = field(default_factory=list)
    read_counts: Counter[tuple[int, int]] = field(default_factory=Counter)
    identity_queries: int = 0
    enabled: bool = True
    unlocked: bool = False
    readings: dict[tuple[int, int], bytes] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.readings.setdefault(
            (codec.PARENT_QUERY, codec.SUB_POSITION),
            codec.encode_i16_vector(tuple(range(100, 117))),
        )
        self.readings.setdefault(
            (codec.PARENT_QUERY, codec.SUB_CURRENT),
            codec.encode_i16_vector(tuple(range(200, 217))),
        )
        self.readings.setdefault(
            (codec.PARENT_QUERY, codec.SUB_SPEED),
            codec.encode_i16_vector(tuple(range(30, 47))),
        )
        self.readings.setdefault(
            (codec.PARENT_QUERY, codec.SUB_TEMPERATURE),
            bytes(range(35, 52)),
        )
        self.readings.setdefault(
            (codec.PARENT_QUERY, codec.SUB_FAULT),
            bytes(codec.JOINT_COUNT),
        )

    @property
    def product_code(self) -> str:
        """Return the product label assembled from current identity fields."""

        side = "L" if self.side_code == 0 else "R"
        return (
            f"LHT30-{self.product_version}-{self.serial_number:03d}-"
            f"{side}-{self.sensor_variant}-3-{self.origin}"
        )

    def respond(self, request: CANFDMessage) -> tuple[CANFDMessage, ...]:
        """Record one request and return its immediate scripted responses."""

        if not request.is_extended_id:
            return ()
        try:
            frame_id = parse_can_id(request.arbitration_id)
        except (ProtocolError, ValidationError):
            return ()
        if frame_id.destination_id != 1 or frame_id.source_id != 0:
            return ()
        self.sent_messages.append(request)
        if frame_id.access == ACCESS_WRITE:
            self.command_messages.append(request)
            return self._write(frame_id.parent, frame_id.subcommand, request.data)
        self.read_counts[(frame_id.parent, frame_id.subcommand)] += 1
        return self._read(frame_id.parent, frame_id.subcommand)

    def _write(
        self,
        parent: int,
        subcommand: int,
        data: bytes,
    ) -> tuple[CANFDMessage, ...]:
        payload = data[2 : 2 + data[0]]
        if parent == codec.PARENT_CONTROL and subcommand == codec.SUB_POSITION:
            self.readings[(codec.PARENT_QUERY, codec.SUB_POSITION)] = payload
            return ()
        if parent == codec.PARENT_CONTROL and subcommand in {
            codec.SUB_CURRENT_LIMIT,
            codec.SUB_SPEED,
        }:
            return ()
        if parent == codec.PARENT_CONTROL and subcommand == codec.SUB_ENABLE:
            self.enabled = True
            return (self._response(parent, subcommand, b"\x00\x00\x00", write=True),)
        if parent == codec.PARENT_CONTROL and subcommand == codec.SUB_DISABLE:
            self.enabled = False
            return (self._response(parent, subcommand, b"\x00\x00\x00", write=True),)
        if parent == codec.PARENT_CONFIG and subcommand == codec.SUB_CONFIG_UNLOCK:
            self.unlocked = payload == codec.CONFIG_UNLOCK_PASSWORD
            status = 0 if self.unlocked else 0x20
            return (
                self._response(
                    parent,
                    subcommand,
                    bytes((0, 0, status)),
                    write=True,
                ),
            )
        if parent == codec.PARENT_CONFIG and subcommand == codec.SUB_CALIBRATE:
            status = 0 if self.unlocked and not self.enabled else 0x23
            return (
                self._response(
                    parent,
                    subcommand,
                    bytes((0, 0, status)),
                    write=True,
                ),
            )
        return ()

    def _read(self, parent: int, subcommand: int) -> tuple[CANFDMessage, ...]:
        if parent == codec.PARENT_CONFIG and subcommand == codec.SUB_DEVICE_INFO:
            self.identity_queries += 1
            payload = self._device_info()
        elif parent == codec.PARENT_CONFIG and subcommand == codec.SUB_PRODUCT_CODE:
            payload = self.product_code.encode("ascii")
        elif parent == codec.PARENT_TACTILE and 1 <= subcommand <= 5:
            matrix = bytes(
                ((subcommand - 1) * 72 + index) & 0xFF for index in range(72)
            )
            return (
                self._response(
                    parent,
                    subcommand,
                    b"\x3d\x10\x00" + matrix[:61],
                ),
                self._response(
                    parent,
                    subcommand,
                    b"\x0b\x11\x00" + matrix[61:],
                ),
            )
        else:
            payload = self.readings.get((parent, subcommand))
        if payload is None:
            return ()
        return (self._response(parent, subcommand, _single_frame(payload)),)

    def _device_info(self) -> bytes:
        return (
            b"\x13"
            + self.serial_number.to_bytes(4, "big")
            + bytes((1, 0, 6))
            + bytes((0, 0, 3))
            + bytes((1, 0, 2))
            + bytes((1, self.side_code))
            + self.sensor_variant.encode("ascii")
            + self.origin.encode("ascii")
        )

    def _response(
        self,
        parent: int,
        subcommand: int,
        data: bytes,
        *,
        write: bool = False,
    ) -> CANFDMessage:
        return CANFDMessage(
            arbitration_id=build_can_id(
                access=ACCESS_WRITE if write else ACCESS_READ,
                parent=parent,
                subcommand=subcommand,
                destination_id=0,
                source_id=1,
            ),
            data=data,
            is_extended_id=True,
        )


class ScriptedL30Transport(FakeTransport[CANFDMessage]):
    """Managed fake exchanging genuine L30 CAN FD messages."""

    def __init__(self, script: L30DeviceScript) -> None:
        super().__init__(route_key_of=_route_key)
        self.script = script

    def send(self, message: CANFDMessage) -> SendReceipt:
        receipt = super().send(message)
        if receipt.exception() is None:
            for response in self.script.respond(message):
                self.emit(response)
        return receipt


def _single_frame(payload: bytes, *, status: int = 0) -> bytes:
    return bytes((len(payload), 0, status)) + payload


def _route_key(message: CANFDMessage) -> CANRouteKey:
    return CANRouteKey(message.arbitration_id, message.is_extended_id)


__all__ = ["L30DeviceScript", "ScriptedL30Transport"]
