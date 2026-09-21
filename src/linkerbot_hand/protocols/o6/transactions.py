"""O6 protocol backend for control and fixed-width state transactions."""

from __future__ import annotations

from ...api.enums import HandSide
from ...api.readings import RawNumber
from ...capabilities.types import (
    CommandSemantic,
    DeviceCapabilities,
    TelemetrySemantic,
)
from ..base import ProtocolTransaction
from ..common.classic_can import classic_route
from ..common.classic_management import (
    configuration_transaction,
    save_parameters_transaction,
)
from ..common.six_axis import ClassicSixAxisBackend
from .codec import (
    BASELINE_SEMANTICS,
    COMMAND_OPCODES,
    CONFIG_TELEMETRY,
    TELEMETRY_OPCODES,
    decode_fault_statuses,
)
from .identity import o6_identity_transaction
from .tactile import o6_tactile_transaction


class O6ProtocolBackend(ClassicSixAxisBackend):
    """Stateless transaction profile for one configured O6 endpoint."""

    def __init__(self, side: HandSide) -> None:
        super().__init__(
            route=classic_route(side),
            identity_transaction=o6_identity_transaction,
            command_opcodes=COMMAND_OPCODES,
            telemetry_opcodes=TELEMETRY_OPCODES,
            baseline=BASELINE_SEMANTICS,
            config_telemetry=CONFIG_TELEMETRY,
            decode_fault_statuses=decode_fault_statuses,
            firmware_optional_reads=(
                TelemetrySemantic.CONFIGURED_SPEEDS,
                TelemetrySemantic.CONFIGURED_ACCELERATIONS,
                TelemetrySemantic.TEMPERATURES,
                TelemetrySemantic.STALL_DETECTION_TIMES,
                TelemetrySemantic.STALL_THRESHOLDS,
                TelemetrySemantic.STALL_HOLD_CURRENTS,
            ),
        )

    def read_transaction(
        self,
        semantic: TelemetrySemantic,
        capabilities: DeviceCapabilities,
    ) -> ProtocolTransaction:
        """Build fixed-width state or one complete O6 tactile read."""

        if semantic is TelemetrySemantic.FORCE:
            return o6_tactile_transaction(capabilities)
        return super().read_transaction(semantic, capabilities)

    def command_transaction(
        self,
        semantic: CommandSemantic,
        values: tuple[RawNumber, ...],
        capabilities: DeviceCapabilities,
    ) -> ProtocolTransaction:
        """Build ordinary angle writes or response-gated configuration writes."""

        # Only the telemetry pairing lives here. The wire opcode comes from
        # COMMAND_OPCODES so there is one source for it; a second copy here is
        # what let the codec table drift without any test noticing, and L6 and
        # O6 assign 0xC5/0xC6 to opposite semantics.
        echoed = {
            CommandSemantic.CURRENT_LIMITS: TelemetrySemantic.CURRENT_LIMITS,
            CommandSemantic.SPEEDS: TelemetrySemantic.CONFIGURED_SPEEDS,
            CommandSemantic.ACCELERATIONS: (TelemetrySemantic.CONFIGURED_ACCELERATIONS),
            CommandSemantic.STALL_THRESHOLDS: TelemetrySemantic.STALL_THRESHOLDS,
            CommandSemantic.STALL_DETECTION_TIMES: (
                TelemetrySemantic.STALL_DETECTION_TIMES
            ),
            CommandSemantic.STALL_HOLD_CURRENTS: (
                TelemetrySemantic.STALL_HOLD_CURRENTS
            ),
        }.get(semantic)
        if echoed is not None:
            return configuration_transaction(
                semantic,
                echoed,
                COMMAND_OPCODES[semantic],
                values,
                capabilities,
            )
        return super().command_transaction(semantic, values, capabilities)

    def operation_transaction(
        self,
        operation: str,
        capabilities: DeviceCapabilities,
    ) -> ProtocolTransaction:
        """Build acknowledged parameter save or reject unsupported maintenance."""

        if operation == "save_parameters":
            return save_parameters_transaction(capabilities)
        return super().operation_transaction(operation, capabilities)


__all__ = ["O6ProtocolBackend"]
