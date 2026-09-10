"""Scripted O30 HOP endpoint for unified runtime integration tests."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from linkerbot_hand.api import HandSide
from linkerbot_hand.protocols.o30 import codec
from linkerbot_hand.protocols.o30.framing import (
    ERROR_MAIN_INDEX,
    FRAME_TYPE,
    MAIN_INDEX_MASK,
    RIGHT_REQUEST_ID,
    RIGHT_RESPONSE_ID,
    RTS_MASK,
)
from linkerbot_transport import CANRouteKey, SendReceipt
from linkerbot_transport.canfd import CANFDMessage
from linkerbot_transport.testing import FakeTransport

_WRITABLE_OBJECTS = {
    codec.MI_POSITION,
    codec.MI_SPEED,
    codec.MI_ACCELERATION,
    codec.MI_CURRENT,
    codec.MI_EFFORT,
    codec.MI_MOTION_TIME,
    codec.MI_STALL_TIME,
    codec.MI_STALL_THRESHOLD,
    codec.MI_STALL_HOLD_CURRENT,
}

# MI 0x31 sensor-command object sub-indices (0.0.4 layout).
_SENSOR_OBJECT_LENGTH = 0x73
_SI_SENSOR_TYPE = 0x00
_SI_DATA_UNIT = 0x20
_SI_DATA_RANGE = 0x28
_SI_MAXIMUM_ROWS = 0x29
_SI_MAXIMUM_COLUMNS = 0x2A
_SI_DESCRIPTOR_LENGTH = 0x2B
_SI_DESCRIPTOR = 0x2C
_SI_TOTAL_DATA_LENGTH = 0x6C
_SI_SELECTED_SENSOR = 0x6E
_SI_REQUESTED_ROWS = 0x6F
_SI_REQUESTED_COLUMNS = 0x70
_SI_VALID_DATA_POSITION_MODE = 0x71
_SI_RECTANGLE_FILL = 0x72

_SENSOR_FINGERS = (1, 2, 3, 4, 5)


@dataclass(frozen=True, slots=True)
class SensorFinger:
    """One fingertip sensor the fake device reports for MI 0x31~0x34."""

    sensor_type: str
    data_unit: str
    data_range: int
    maximum_rows: int
    maximum_columns: int
    descriptor_text: str
    data: bytes


def _tssp_jzg_finger(selected_sensor: int) -> SensorFinger:
    """Build one deterministic ``FnS_U16_1;Fn_U8_70`` fingertip stream."""

    normal_force_resultant = 1000 + selected_sensor
    normal_force = bytes((selected_sensor * 10 + index) & 0xFF for index in range(70))
    return SensorFinger(
        sensor_type="TSSP_JZG",
        data_unit="g",
        data_range=255,
        maximum_rows=10,
        maximum_columns=7,
        descriptor_text="FnS_U16_1;Fn_U8_70",
        data=normal_force_resultant.to_bytes(2, "little") + normal_force,
    )


def _no_sensor_finger(_selected_sensor: int) -> SensorFinger:
    return SensorFinger(
        sensor_type="NO_SENSOR",
        data_unit="",
        data_range=0,
        maximum_rows=0,
        maximum_columns=0,
        descriptor_text="",
        data=b"",
    )


_SENSOR_VARIANTS = {
    "NO_SENSOR": _no_sensor_finger,
    "TSSP_JZG": _tssp_jzg_finger,
}


@dataclass(slots=True)
class O30DeviceScript:
    """Deterministic O30 behavior using real HOP CAN FD messages."""

    device_uid: str = "DEVICE-UID-O30"
    product_model: str = "O30"
    protocol_version: str = "0.0.4"
    application_version: str = "1.2.0"
    identity_layout: str = "deployed"
    reported_side: HandSide | None = HandSide.RIGHT
    sensor_variant: str = "NO_SENSOR"
    request_id: int = RIGHT_REQUEST_ID
    response_id: int = RIGHT_RESPONSE_ID
    sent_messages: list[CANFDMessage] = field(default_factory=list)
    command_messages: list[CANFDMessage] = field(default_factory=list)
    read_counts: Counter[tuple[int, bool]] = field(default_factory=Counter)
    identity_queries: int = 0
    actual: dict[int, bytes] = field(default_factory=dict)
    targets: dict[int, bytes] = field(default_factory=dict)
    selected_sensor: int = 1

    def __post_init__(self) -> None:
        if self.identity_layout not in {"deployed", "documented"}:
            raise ValueError("identity_layout must be deployed or documented")
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
            request.arbitration_id != self.request_id
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
            return self._write(main_index, sub_index, length, request.data[3:], rts=rts)
        self.read_counts[(main_index, rts)] += 1
        return self._read(main_index, sub_index, length, rts=rts)

    def _write(
        self,
        main_index: int,
        sub_index: int,
        length: int,
        payload: bytes,
        *,
        rts: bool = False,
    ) -> tuple[CANFDMessage, ...]:
        if main_index == codec.MI_SENSOR_INFO:
            return self._write_sensor(sub_index, length, payload)
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
        return (self._response(main_index, sub_index, value, rts=rts),)

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
            data = bytearray(225 if self.identity_layout == "deployed" else 249)
            fields = [
                (0x00, 0x10, self.product_model),
                (0x10, 0x10, "12V~24V"),
                (0x20, 0x19, "0029003E594650142035334E"),
                (0x39, 0x20, self.device_uid),
                (0x59, 0x08, "HOP"),
                (0x61, 0x08, self.protocol_version),
            ]
            if self.identity_layout == "deployed":
                fields.extend(
                    (
                        (0x69, 0x08, "V1.2.0"),
                        (0x71, 0x08, "V1.1.0"),
                        (0x79, 0x08, "V0.9.0"),
                        (0x81, 0x08, "1.0.0"),
                        (0x89, 0x08, self.application_version),
                        (0x91, 0x08, "M1.0.0"),
                        (0x99, 0x20, "2026-09-03 13:55:02"),
                        (0xB9, 0x20, "CAN"),
                        (
                            0xD9,
                            0x08,
                            ""
                            if self.reported_side is None
                            else self.reported_side.value.upper(),
                        ),
                    )
                )
            else:
                fields.extend(
                    (
                        (0x69, 0x10, "IFACE V1.2.0"),
                        (0x79, 0x10, "ADAPTER V1.1.0"),
                        (0x89, 0x10, "CONTROL V0.9.0"),
                        (0x99, 0x08, "1.0.0"),
                        (0xA1, 0x08, self.application_version),
                        (0xA9, 0x08, "M1.0.0"),
                        (0xB1, 0x20, "2026-09-03 13:55:02"),
                        (0xD1, 0x20, "CAN"),
                        (
                            0xF1,
                            0x08,
                            ""
                            if self.reported_side is None
                            else self.reported_side.value.upper(),
                        ),
                    )
                )
            for start, length, value in fields:
                encoded = value.encode("ascii")
                data[start : start + min(length, len(encoded))] = encoded[:length]
            return bytes(data)
        if main_index == codec.MI_SENSOR_INFO:
            return self._sensor_object(self.selected_sensor)
        if main_index == codec.MI_SENSOR_DATA_1:
            return self._sensor_finger(self.selected_sensor).data[0:255]
        if main_index == codec.MI_SENSOR_DATA_2:
            return self._sensor_finger(self.selected_sensor).data[255:511]
        if main_index == codec.MI_SENSOR_DATA_3:
            return self._sensor_finger(self.selected_sensor).data[511:]
        return None

    def _sensor_finger(self, selected_sensor: int) -> SensorFinger:
        try:
            build = _SENSOR_VARIANTS[self.sensor_variant]
        except KeyError as error:
            raise ValueError(
                f"unsupported sensor variant {self.sensor_variant!r}"
            ) from error
        return build(selected_sensor)

    def _sensor_object(self, selected_sensor: int) -> bytes:
        """Assemble the 115-byte MI 0x31 command object for one fingertip."""

        finger = self._sensor_finger(selected_sensor)
        descriptor = finger.descriptor_text.encode("ascii")
        data = bytearray(_SENSOR_OBJECT_LENGTH)
        sensor_type = finger.sensor_type.encode("ascii")
        data[_SI_SENSOR_TYPE : _SI_SENSOR_TYPE + len(sensor_type)] = sensor_type
        unit = finger.data_unit.encode("ascii")
        data[_SI_DATA_UNIT : _SI_DATA_UNIT + len(unit)] = unit
        data[_SI_DATA_RANGE] = finger.data_range
        data[_SI_MAXIMUM_ROWS] = finger.maximum_rows
        data[_SI_MAXIMUM_COLUMNS] = finger.maximum_columns
        data[_SI_DESCRIPTOR_LENGTH] = len(descriptor)
        data[_SI_DESCRIPTOR : _SI_DESCRIPTOR + len(descriptor)] = descriptor
        data[_SI_TOTAL_DATA_LENGTH : _SI_TOTAL_DATA_LENGTH + 2] = len(
            finger.data
        ).to_bytes(2, "little")
        data[_SI_SELECTED_SENSOR] = selected_sensor
        data[_SI_REQUESTED_ROWS] = finger.maximum_rows
        data[_SI_REQUESTED_COLUMNS] = finger.maximum_columns
        data[_SI_VALID_DATA_POSITION_MODE] = 0
        data[_SI_RECTANGLE_FILL] = 1 if finger.data else 0
        return bytes(data)

    def _write_sensor(
        self,
        sub_index: int,
        length: int,
        payload: bytes,
    ) -> tuple[CANFDMessage, ...]:
        if length != 1 or len(payload) < 1:
            return (self._error(sub_index, 0x40),)
        value = payload[0]
        if sub_index == _SI_SELECTED_SENSOR:
            if value not in _SENSOR_FINGERS:
                return (self._error(sub_index, 0x40),)
            self.selected_sensor = value
        elif sub_index not in (
            _SI_REQUESTED_ROWS,
            _SI_REQUESTED_COLUMNS,
            _SI_VALID_DATA_POSITION_MODE,
            _SI_RECTANGLE_FILL,
        ):
            return (self._error(sub_index, 0x02),)
        return (self._response(codec.MI_SENSOR_INFO, sub_index, bytes((value,))),)

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
            arbitration_id=self.response_id,
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
