"""L6 protocol backend for control and fixed-width state transactions."""

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
from ..common.six_axis import ClassicSixAxisBackend
from .codec import (
    BASELINE_SEMANTICS,
    CLEAR_FAULTS_PAYLOAD,
    COMMAND_OPCODES,
    CONFIG_TELEMETRY,
    TELEMETRY_OPCODES,
)
from .identity import l6_identity_transaction
from .management import (
    echoed_configuration_transaction,
    limit_compensation_transaction,
    save_parameters_transaction,
)
from .tactile import l6_tactile_transaction


class L6ProtocolBackend(ClassicSixAxisBackend):
    """Stateless transaction profile for one configured L6 endpoint."""

    def __init__(self, side: HandSide) -> None:
        super().__init__(
            route=classic_route(side),
            identity_transaction=l6_identity_transaction,
            command_opcodes=COMMAND_OPCODES,
            telemetry_opcodes=TELEMETRY_OPCODES,
            baseline=BASELINE_SEMANTICS,
            operation_payloads={"clear_faults": CLEAR_FAULTS_PAYLOAD},
            config_telemetry=CONFIG_TELEMETRY,
            firmware_optional_reads=(
                TelemetrySemantic.TEMPERATURES,
                TelemetrySemantic.CURRENTS,
                TelemetrySemantic.LIMIT_COMPENSATIONS,
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
        """Build fixed-width state or one complete five-finger tactile read."""

        if semantic is TelemetrySemantic.FORCE:
            return l6_tactile_transaction(capabilities)
        return super().read_transaction(semantic, capabilities)

    def command_transaction(
        self,
        semantic: CommandSemantic,
        values: tuple[RawNumber, ...],
        capabilities: DeviceCapabilities,
    ) -> ProtocolTransaction:
        """Build ordinary raw writes and response-gated configuration writes."""

        if semantic is CommandSemantic.LIMIT_COMPENSATIONS:
            return limit_compensation_transaction(values, capabilities)
        echoed = {
            CommandSemantic.SPEEDS: (
                TelemetrySemantic.CONFIGURED_SPEEDS,
                0x05,
                False,
            ),
            CommandSemantic.CURRENT_LIMITS: (
                TelemetrySemantic.CURRENT_LIMITS,
                0x02,
                False,
            ),
            CommandSemantic.STALL_DETECTION_TIMES: (
                TelemetrySemantic.STALL_DETECTION_TIMES,
                0xC5,
                True,
            ),
            CommandSemantic.STALL_THRESHOLDS: (
                TelemetrySemantic.STALL_THRESHOLDS,
                0xC6,
                True,
            ),
            CommandSemantic.STALL_HOLD_CURRENTS: (
                TelemetrySemantic.STALL_HOLD_CURRENTS,
                0xC7,
                True,
            ),
        }.get(semantic)
        if echoed is not None:
            telemetry_semantic, opcode, require_exact_echo = echoed
            return echoed_configuration_transaction(
                semantic,
                telemetry_semantic,
                opcode,
                values,
                capabilities,
                require_exact_echo=require_exact_echo,
            )
        return super().command_transaction(semantic, values, capabilities)

    def operation_transaction(
        self,
        operation: str,
        capabilities: DeviceCapabilities,
    ) -> ProtocolTransaction:
        """Build dispatch-only clear-faults or acknowledged parameter save."""

        if operation == "save_parameters":
            return save_parameters_transaction(capabilities)
        return super().operation_transaction(operation, capabilities)


__all__ = ["L6ProtocolBackend"]
