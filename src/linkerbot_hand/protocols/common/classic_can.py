"""Classic CAN frame, identity, and fixed-width response codecs."""

from __future__ import annotations

from dataclasses import dataclass, field

import can

from linkerbot_transport import CANRouteKey

from ...api.device_info import VersionField
from ...api.enums import HandSide
from ...api.errors import IdentityMismatchError, ProtocolError, ValidationError

_LEFT_CAN_ID = 0x28
_RIGHT_CAN_ID = 0x27
_SERIAL_FRAGMENT_SIZE = 6
_SERIAL_OFFSETS = (0, 6, 12, 18)


def classic_route(side: HandSide) -> CANRouteKey:
    """Return the exact standard-frame route assigned to a hand side."""

    if side is HandSide.LEFT:
        arbitration_id = _LEFT_CAN_ID
    elif side is HandSide.RIGHT:
        arbitration_id = _RIGHT_CAN_ID
    else:
        raise ValidationError("classic six-axis hands require a left or right side")
    return CANRouteKey(arbitration_id=arbitration_id, is_extended_id=False)


def build_classic_message(route: CANRouteKey, data: bytes) -> can.Message:
    """Build one classic CAN request without performing transport I/O."""

    if not isinstance(route, CANRouteKey) or route.is_extended_id:
        raise ValidationError("route must identify a standard CAN frame")
    if not isinstance(data, bytes) or not 1 <= len(data) <= 8:
        raise ValidationError("classic CAN payload must contain 1..8 bytes")
    return can.Message(
        arbitration_id=route.arbitration_id,
        data=data,
        is_extended_id=False,
    )


def matching_data(message: object, route: CANRouteKey) -> bytes | None:
    """Return bytes for one exact classic route, or ``None`` when unmatched."""

    if not isinstance(message, can.Message):
        return None
    if (
        message.arbitration_id != route.arbitration_id
        or message.is_extended_id
        or message.is_error_frame
        or message.is_remote_frame
        or message.is_fd
    ):
        return None
    return bytes(message.data)


def decode_u8_vector(
    message: object,
    *,
    route: CANRouteKey,
    opcode: int,
    count: int,
) -> tuple[int, ...] | None:
    """Decode an exact opcode plus unsigned-byte vector response."""

    data = matching_data(message, route)
    if data is None or not data or data[0] != opcode:
        return None
    expected = count + 1
    if len(data) != expected:
        raise ProtocolError(
            f"opcode 0x{opcode:02X} response must contain {expected} bytes"
        )
    return tuple(data[1:])


@dataclass(frozen=True, slots=True)
class FactoryIdentity:
    """Structured fields from one classic factory product code."""

    product_code: str
    product_model: str
    product_version: str
    serial_number: str
    side: HandSide
    sensor_variant: str | None
    sensor_revision: str | None
    factory_code: str | None


def parse_factory_identity(
    product_code: str,
    *,
    expected_model: str | tuple[str, ...],
    configured_side: HandSide,
) -> FactoryIdentity:
    """Parse and verify the common dash-separated classic product code."""

    if not isinstance(product_code, str):
        raise ProtocolError("factory product code must be text")
    normalized = product_code.strip().strip("\x00")
    parts = tuple(part.strip() for part in normalized.split("-"))
    if len(parts) < 4 or any(not value for value in parts[:4]):
        raise ProtocolError("factory product code is incomplete")
    expected_models = (
        (expected_model,) if isinstance(expected_model, str) else expected_model
    )
    if not expected_models or any(
        not isinstance(value, str) or not value for value in expected_models
    ):
        raise ValidationError("expected_model must contain factory model names")
    normalized_models = tuple(value.upper() for value in expected_models)
    if parts[0].upper() not in normalized_models:
        raise IdentityMismatchError(
            f"expected factory model {' or '.join(expected_models)}, got {parts[0]}"
        )
    side = {"L": HandSide.LEFT, "R": HandSide.RIGHT}.get(parts[3].upper())
    if side is None:
        raise ProtocolError("factory product code has an unknown side")
    if side is not configured_side:
        raise IdentityMismatchError(
            "factory product code side does not match the configured endpoint"
        )
    return FactoryIdentity(
        product_code=normalized,
        product_model=parts[0],
        product_version=parts[1],
        serial_number=parts[2],
        side=side,
        sensor_variant=(parts[4].upper() or None) if len(parts) > 4 else None,
        sensor_revision=(parts[5] or None) if len(parts) > 5 else None,
        factory_code=(parts[6].upper() or None) if len(parts) > 6 else None,
    )


@dataclass(slots=True)
class _SerialFragments:
    fragments: dict[int, bytes] = field(default_factory=dict)
    terminal_offset: int | None = None

    def add(self, marker: int, payload: bytes) -> None:
        offset = _serial_offset(marker)
        if offset is None:
            raise ProtocolError("serial fragment marker is neither an index nor offset")
        if not 1 <= len(payload) <= _SERIAL_FRAGMENT_SIZE:
            raise ProtocolError("serial fragment must contain 1..6 payload bytes")
        previous = self.fragments.get(offset)
        if previous is not None and previous != payload:
            raise ProtocolError("serial fragment conflicts with a duplicate")
        self.fragments[offset] = payload
        if len(payload) < _SERIAL_FRAGMENT_SIZE or b"\x00" in payload:
            self.terminal_offset = offset

    @property
    def complete(self) -> bool:
        offsets = tuple(
            offset for offset in _SERIAL_OFFSETS if offset in self.fragments
        )
        if offsets == _SERIAL_OFFSETS:
            return True
        if self.terminal_offset is None:
            return False
        expected = _SERIAL_OFFSETS[: self.terminal_offset // _SERIAL_FRAGMENT_SIZE + 1]
        return offsets == expected

    @property
    def legacy_short_complete(self) -> bool:
        """Return whether three contiguous full frames form an old L6 code."""

        offsets = tuple(
            offset for offset in _SERIAL_OFFSETS if offset in self.fragments
        )
        return offsets == _SERIAL_OFFSETS[:3]

    def result(self, *, allow_legacy_short: bool = False) -> bytes:
        if not self.complete and not (
            allow_legacy_short and self.legacy_short_complete
        ):
            raise ProtocolError("serial response is incomplete")
        data = b"".join(
            self.fragments[offset]
            for offset in _SERIAL_OFFSETS
            if offset in self.fragments
        )
        return data.rstrip(b"\x00")


class ClassicIdentityCollector:
    """Collect one C0/C1/C2/C4 identity transaction without I/O ownership."""

    def __init__(self, route: CANRouteKey, *, l6_pcb_format: bool) -> None:
        self._route = route
        self._l6_pcb_format = l6_pcb_format
        self._serial = _SerialFragments()
        self._versions: dict[int, VersionField] = {}

    def offer(self, message: object) -> bool:
        """Consume one identity frame and return whether it matched this transaction."""

        data = matching_data(message, self._route)
        if data is None or not data:
            return False
        opcode = data[0]
        if opcode == 0xC0:
            if len(data) < 3:
                raise ProtocolError("serial response must include marker and payload")
            self._serial.add(data[1], data[2:])
            return True
        if opcode not in {0xC1, 0xC2, 0xC4}:
            return False
        self._store_version(opcode, data)
        return True

    @property
    def complete(self) -> bool:
        """Return whether product code and all supported versions are complete."""

        serial_complete = self._serial.complete or (
            self._l6_pcb_format and self._serial.legacy_short_complete
        )
        return serial_complete and set(self._versions) == {0xC1, 0xC2, 0xC4}

    @property
    def product_code_bytes(self) -> bytes:
        """Return the exact assembled factory product code bytes."""

        return self._serial.result(allow_legacy_short=self._l6_pcb_format)

    def product_code(self) -> str:
        """Decode the complete factory product code as strict ASCII."""

        try:
            value = self.product_code_bytes.decode("ascii")
        except UnicodeDecodeError as error:
            raise ProtocolError("factory product code is not ASCII") from error
        if not value:
            raise ProtocolError("factory product code is empty")
        return value

    def version(self, opcode: int) -> VersionField:
        """Return a collected version field by its C1/C2/C4 opcode."""

        try:
            return self._versions[opcode]
        except KeyError as error:
            raise ProtocolError(
                f"version response 0x{opcode:02X} is missing"
            ) from error

    def _store_version(self, opcode: int, data: bytes) -> None:
        if opcode == 0xC1 and self._l6_pcb_format:
            if len(data) != 5 or data[1] != 0x01:
                raise ProtocolError("L6 PCB version response has an invalid format")
            raw = tuple(data[2:5])
        else:
            if len(data) != 4:
                raise ProtocolError(
                    f"version response 0x{opcode:02X} must contain four bytes"
                )
            raw = tuple(data[1:4])
        version = VersionField(
            raw=raw,
            text=".".join(str(value) for value in raw),
            major=raw[0],
            minor=raw[1],
            patch=raw[2],
        )
        previous = self._versions.get(opcode)
        if previous is not None and previous != version:
            raise ProtocolError(
                f"version response 0x{opcode:02X} conflicts with a duplicate"
            )
        self._versions[opcode] = version


def _serial_offset(marker: int) -> int | None:
    if type(marker) is not int:
        return None
    if 0 <= marker < len(_SERIAL_OFFSETS):
        return marker * _SERIAL_FRAGMENT_SIZE
    if marker in _SERIAL_OFFSETS:
        return marker
    return None


__all__ = [
    "ClassicIdentityCollector",
    "FactoryIdentity",
    "build_classic_message",
    "classic_route",
    "decode_u8_vector",
    "matching_data",
    "parse_factory_identity",
]
