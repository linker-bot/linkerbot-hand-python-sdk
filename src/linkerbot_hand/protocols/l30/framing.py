"""L30 CAN FD arbitration identifier and single-frame request helpers."""

from __future__ import annotations

from dataclasses import dataclass

from linkerbot_transport import CANRouteKey
from linkerbot_transport.canfd import CANFDMessage

from ...api.errors import ProtocolError, ValidationError

DEFAULT_NODE_ID = 1
DEFAULT_HOST_ID = 0
ACCESS_READ = 0
ACCESS_WRITE = 1

_PRIORITY = 0
_PRIORITY_MAX = 0x7
_PARENT_MAX = 0xF
_SUBCOMMAND_MAX = 0xFF
_NODE_ID_MAX = 0x1F
_RESERVED_ID_MASK = 0x7


@dataclass(frozen=True, slots=True)
class FrameId:
    """Decoded fields from one L30 29-bit arbitration ID."""

    priority: int
    access: int
    parent: int
    subcommand: int
    destination_id: int
    source_id: int


def build_can_id(
    *,
    access: int,
    parent: int,
    subcommand: int,
    destination_id: int,
    source_id: int,
    priority: int = _PRIORITY,
) -> int:
    """Pack one validated L30 extended arbitration ID."""

    _require_int_range(priority, "priority", 0, _PRIORITY_MAX)
    _require_int_range(access, "access", ACCESS_READ, ACCESS_WRITE)
    _require_int_range(parent, "parent", 0, _PARENT_MAX)
    _require_int_range(subcommand, "subcommand", 0, _SUBCOMMAND_MAX)
    _require_int_range(destination_id, "destination_id", 0, _NODE_ID_MAX)
    _require_int_range(source_id, "source_id", 0, _NODE_ID_MAX)
    return (
        (priority << 26)
        | (access << 25)
        | (parent << 21)
        | (subcommand << 13)
        | (destination_id << 8)
        | (source_id << 3)
    )


def parse_can_id(arbitration_id: int) -> FrameId:
    """Decode one valid L30 arbitration ID and reject reserved low bits."""

    _require_int_range(arbitration_id, "arbitration_id", 0, 0x1FFFFFFF)
    if arbitration_id & _RESERVED_ID_MASK:
        raise ProtocolError("L30 arbitration ID reserved bits must be zero")
    return FrameId(
        priority=(arbitration_id >> 26) & _PRIORITY_MAX,
        access=(arbitration_id >> 25) & ACCESS_WRITE,
        parent=(arbitration_id >> 21) & _PARENT_MAX,
        subcommand=(arbitration_id >> 13) & _SUBCOMMAND_MAX,
        destination_id=(arbitration_id >> 8) & _NODE_ID_MAX,
        source_id=(arbitration_id >> 3) & _NODE_ID_MAX,
    )


def response_route(
    *,
    parent: int,
    subcommand: int,
    write: bool,
    node_id: int = DEFAULT_NODE_ID,
    host_id: int = DEFAULT_HOST_ID,
) -> CANRouteKey:
    """Return the exact response route with source and destination swapped."""

    if not isinstance(write, bool):
        raise ValidationError("write must be bool")
    return CANRouteKey(
        arbitration_id=build_can_id(
            access=ACCESS_WRITE if write else ACCESS_READ,
            parent=parent,
            subcommand=subcommand,
            destination_id=host_id,
            source_id=node_id,
        ),
        is_extended_id=True,
    )


def build_request(
    *,
    parent: int,
    subcommand: int,
    write: bool,
    payload: bytes = b"",
    dlc: int | None = None,
    node_id: int = DEFAULT_NODE_ID,
    host_id: int = DEFAULT_HOST_ID,
) -> CANFDMessage:
    """Build one single-frame request with its length and transaction header."""

    if not isinstance(write, bool):
        raise ValidationError("write must be bool")
    if not isinstance(payload, bytes):
        raise ValidationError("payload must be bytes")
    if len(payload) > 62:
        raise ValidationError("L30 single-frame payload cannot exceed 62 bytes")
    return CANFDMessage(
        arbitration_id=build_can_id(
            access=ACCESS_WRITE if write else ACCESS_READ,
            parent=parent,
            subcommand=subcommand,
            destination_id=node_id,
            source_id=host_id,
        ),
        data=bytes((len(payload), 0x00)) + payload,
        dlc=dlc,
        is_extended_id=True,
    )


def matching_response_data(
    message: object,
    *,
    parent: int,
    subcommand: int,
    write: bool,
    node_id: int = DEFAULT_NODE_ID,
    host_id: int = DEFAULT_HOST_ID,
) -> bytes | None:
    """Extract data from one exact L30 response and ignore unrelated traffic."""

    if not isinstance(message, CANFDMessage) or not message.is_extended_id:
        return None
    route = response_route(
        parent=parent,
        subcommand=subcommand,
        write=write,
        node_id=node_id,
        host_id=host_id,
    )
    if message.arbitration_id != route.arbitration_id:
        return None
    return message.data


def _require_int_range(value: object, name: str, minimum: int, maximum: int) -> None:
    if type(value) is not int:
        raise ValidationError(f"{name} must be int")
    if value < minimum or value > maximum:
        raise ValidationError(f"{name} must be within {minimum}..{maximum}")


__all__ = [
    "ACCESS_READ",
    "ACCESS_WRITE",
    "DEFAULT_HOST_ID",
    "DEFAULT_NODE_ID",
    "FrameId",
    "build_can_id",
    "build_request",
    "matching_response_data",
    "parse_can_id",
    "response_route",
]
