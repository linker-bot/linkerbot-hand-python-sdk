"""LHT10 acceleration writes retain both five-joint responses."""

from ...api.enums import DataOrigin
from ...api.errors import UnsupportedOperationError
from ...api.readings import RawNumber
from ...capabilities.types import CommandSemantic, DeviceCapabilities, TelemetrySemantic
from ..base import ProtocolTransaction, ResponseProgress, SamplePayload
from ..common.classic_can import classic_route
from ..common.classic_layout import ClassicObjectAssembler
from ..common.classic_multi_axis import _validated_u8
from .codec import ACCELERATION_LAYOUT


def acceleration_transaction(
    values: tuple[RawNumber, ...], capabilities: DeviceCapabilities
) -> ProtocolTransaction:
    """Publish complete device replies unchanged; never construct a read request."""
    semantic = CommandSemantic.ACCELERATIONS
    command = capabilities.command(semantic)
    if command is None:
        raise UnsupportedOperationError(
            f"{capabilities.model} does not support {semantic.value}"
        )
    raw = _validated_u8(values, command, semantic.value)
    route = classic_route(capabilities.side)
    assembler = ClassicObjectAssembler(route=route, layout=ACCELERATION_LAYOUT)

    def consume(message: object) -> ResponseProgress:
        matched, reported = assembler.offer(message)
        if not matched:
            return ResponseProgress(matched=False)
        if reported is None:
            return ResponseProgress(matched=True)
        return ResponseProgress(
            matched=True,
            complete=True,
            acknowledged=True,
            result=SamplePayload(
                semantic=TelemetrySemantic.CONFIGURED_ACCELERATIONS,
                raw=reported,
                origin=DataOrigin.DEVICE_CONFIG_RESPONSE,
            ),
        )

    return ProtocolTransaction(
        key=("command", semantic),
        correlation_key=("classic_object", route, ACCELERATION_LAYOUT.opcodes),
        request_messages=ACCELERATION_LAYOUT.encode(raw, route),
        request_interval_s=0.001,
        response_timeout_s=0.1,
        estimated_duration_s=0.006,
        response_consumer=consume,
        response_acknowledges_command=True,
        late_response_guard_s=0.005,
    )
