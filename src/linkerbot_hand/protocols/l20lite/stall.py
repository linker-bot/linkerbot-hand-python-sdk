"""Two-group LHT10 configuration writes with strict atomic echo publication."""

from ...api.enums import DataOrigin
from ...api.errors import ProtocolError, UnsupportedOperationError
from ...api.readings import RawNumber
from ...capabilities.types import CommandSemantic, DeviceCapabilities
from ..base import ProtocolTransaction, ResponseProgress, SamplePayload
from ..common.classic_can import build_classic_message, classic_route, matching_data
from ..common.classic_multi_axis import _validated_u8
from .codec import STALL_COMMANDS


def stall_transaction(
    semantic: CommandSemantic,
    values: tuple[RawNumber, ...],
    capabilities: DeviceCapabilities,
) -> ProtocolTransaction:
    """Acknowledge only when both five-joint groups exactly echo the write."""
    command = capabilities.command(semantic)
    if command is None:
        raise UnsupportedOperationError(
            f"{capabilities.model} does not support {semantic.value}"
        )
    raw = _validated_u8(values, command, semantic.value)
    opcode, telemetry = STALL_COMMANDS[semantic]
    route = classic_route(capabilities.side)
    expected = {
        group: bytes((opcode, group, *raw[offset : offset + 5]))
        for group, offset in ((1, 0), (2, 5))
    }
    received: set[int] = set()

    def consume(message: object) -> ResponseProgress:
        data = matching_data(message, route)
        if not data or data[0] != opcode:
            return ResponseProgress(matched=False)
        if len(data) != 7:
            raise ProtocolError(f"L20Lite 0x{opcode:02X} echo must contain 7 bytes")
        group = data[1]
        if group not in expected:
            raise ProtocolError(
                f"L20Lite 0x{opcode:02X} echo has invalid group {group}"
            )
        if data != expected[group]:
            raise ProtocolError(
                f"L20Lite {semantic.value} group {group} did not echo values"
            )
        received.add(group)
        if len(received) < 2:
            return ResponseProgress(matched=True)
        return ResponseProgress(
            matched=True,
            complete=True,
            acknowledged=True,
            result=SamplePayload(
                semantic=telemetry,
                raw=raw,
                origin=DataOrigin.DEVICE_CONFIG_RESPONSE,
            ),
        )

    return ProtocolTransaction(
        key=("command", semantic),
        correlation_key=("l20lite_stall", route, opcode),
        request_messages=tuple(
            build_classic_message(route, data) for data in expected.values()
        ),
        request_interval_s=0.001,
        response_timeout_s=0.1,
        estimated_duration_s=0.006,
        response_consumer=consume,
        response_acknowledges_command=True,
        late_response_guard_s=0.005,
    )
