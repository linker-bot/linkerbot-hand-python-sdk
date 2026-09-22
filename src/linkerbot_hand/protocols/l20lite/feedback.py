"""Separate LHT10 setting echoes from measurements on the same opcodes."""

from ...api.enums import DataOrigin
from ...api.errors import UnsupportedOperationError
from ...api.readings import RawNumber
from ...capabilities.types import CommandSemantic, DeviceCapabilities, TelemetrySemantic
from ..base import ProtocolTransaction, ResponseProgress, SamplePayload
from ..common.classic_can import classic_route, matching_data
from ..common.classic_layout import ClassicObjectAssembler
from ..common.classic_multi_axis import _validated_u8
from .codec import COMMAND_LAYOUTS

SETTING_FEEDBACK = {
    CommandSemantic.SPEEDS: TelemetrySemantic.CONFIGURED_SPEEDS,
    CommandSemantic.CURRENT_LIMITS: TelemetrySemantic.CURRENT_LIMITS,
}


def setting_feedback_transaction(
    semantic: CommandSemantic,
    values: tuple[RawNumber, ...],
    capabilities: DeviceCapabilities,
) -> ProtocolTransaction:
    """Acknowledge two complete echoes without treating them as live readings."""

    command = capabilities.command(semantic)
    if command is None:
        raise UnsupportedOperationError(
            f"{capabilities.model} does not support {semantic.value}"
        )
    raw = _validated_u8(values, command, semantic.value)
    route = classic_route(capabilities.side)
    layout = COMMAND_LAYOUTS[semantic]
    assembler = ClassicObjectAssembler(route=route, layout=layout)

    def consume(message: object) -> ResponseProgress:
        matched, reported = assembler.offer(message)
        if reported is None:
            return ResponseProgress(matched=matched)
        return ResponseProgress(
            matched=True,
            complete=True,
            acknowledged=True,
            result=SamplePayload(
                semantic=SETTING_FEEDBACK[semantic],
                raw=reported,
                origin=DataOrigin.DEVICE_CONFIG_RESPONSE,
            ),
        )

    def discard_late_echo(message: object) -> ResponseProgress:
        # Once this write has failed, no remaining group can establish its
        # result. Drain even malformed/duplicate echoes for the full guard.
        data = matching_data(message, route)
        return ResponseProgress(matched=bool(data and data[0] in layout.opcodes))

    return ProtocolTransaction(
        key=("command", semantic),
        correlation_key=("classic_object", route, layout.opcodes),
        request_messages=layout.encode(raw, route),
        request_interval_s=0.001,
        response_timeout_s=0.1,
        estimated_duration_s=0.006,
        response_consumer=consume,
        response_acknowledges_command=True,
        late_response_guard_s=0.1,
        late_response_consumer=discard_late_echo,
    )
