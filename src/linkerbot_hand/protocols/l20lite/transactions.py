"""L20Lite protocol backend."""

from dataclasses import replace

from ...api.enums import HandSide
from ...api.readings import RawNumber
from ...capabilities.types import CommandSemantic, DeviceCapabilities, TelemetrySemantic
from ..base import ProtocolTransaction
from ..common.classic_can import classic_route
from ..common.classic_multi_axis import ClassicMultiAxisBackend
from ..common.tactile import response_gated_tactile_transaction
from .codec import (
    BASELINE_SEMANTICS,
    COMMAND_LAYOUTS,
    CONFIG_TELEMETRY,
    FORCE_SCALAR_LAYOUTS,
    STALL_COMMANDS,
    TELEMETRY_LAYOUTS,
    decode_fault_statuses,
)
from .identity import l20lite_identity_transaction
from .stall import stall_transaction


class L20LiteProtocolBackend(ClassicMultiAxisBackend):
    """Stateless transaction profile for one L20Lite endpoint."""

    def __init__(self, side: HandSide) -> None:
        super().__init__(
            route=classic_route(side),
            identity_transaction=l20lite_identity_transaction,
            command_layouts=COMMAND_LAYOUTS,
            telemetry_layouts=TELEMETRY_LAYOUTS,
            baseline=BASELINE_SEMANTICS,
            config_telemetry=CONFIG_TELEMETRY,
            firmware_optional_reads=(
                TelemetrySemantic.MEASURED_SPEEDS,
                TelemetrySemantic.TEMPERATURES,
                *FORCE_SCALAR_LAYOUTS,
            ),
            # LHT10 specifies 1 ms between vector frames. Firmware 4.6.10
            # intermittently omits replies at the shared 0.5 ms default.
            request_interval_s=0.001,
            decode_fault_statuses=decode_fault_statuses,
        )

    def command_transaction(
        self,
        semantic: CommandSemantic,
        values: tuple[RawNumber, ...],
        capabilities: DeviceCapabilities,
    ) -> ProtocolTransaction:
        """Dispatch grouped stall settings through exact write echoes."""
        if semantic in STALL_COMMANDS:
            return stall_transaction(semantic, values, capabilities)
        return super().command_transaction(semantic, values, capabilities)

    def read_transaction(
        self,
        semantic: TelemetrySemantic,
        capabilities: DeviceCapabilities,
    ) -> ProtocolTransaction:
        """Build a vector read or one complete five-finger tactile read."""

        if semantic is TelemetrySemantic.FORCE:
            transaction = response_gated_tactile_transaction(
                capabilities,
                route=classic_route(capabilities.side),
                selector=0xC6,
                rows=12,
                columns=6,
                response_timeout_s=0.15,
                late_response_guard_s=0.1,
            )
            return replace(transaction, firmware_optional=True)
        return super().read_transaction(semantic, capabilities)


__all__ = ["L20LiteProtocolBackend"]
