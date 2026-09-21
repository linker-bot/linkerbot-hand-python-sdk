"""L6 protocol backend for control and fixed-width state transactions."""

from __future__ import annotations

from dataclasses import replace

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
    CLEAR_FAULTS_PAYLOAD,
    COMMAND_OPCODES,
    CONFIG_TELEMETRY,
    TELEMETRY_OPCODES,
    decode_fault_statuses,
)
from .identity import l6_identity_transaction
from .management import limit_compensation_transaction
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
            decode_fault_statuses=decode_fault_statuses,
            firmware_optional_reads=(
                TelemetrySemantic.TORQUES,
                TelemetrySemantic.POSITION_KP,
                TelemetrySemantic.POSITION_KD,
                TelemetrySemantic.TORQUE_KD,
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
        # Only the telemetry pairing lives here. The wire
        # opcode comes from COMMAND_OPCODES so there is one source for it; a
        # second copy here is what let the codec table drift without any test
        # noticing, and L6 and O6 assign 0xC5/0xC6 to opposite semantics.
        response_semantic = {
            CommandSemantic.TORQUES: TelemetrySemantic.TORQUES,
            CommandSemantic.POSITION_KP: TelemetrySemantic.POSITION_KP,
            CommandSemantic.POSITION_KD: TelemetrySemantic.POSITION_KD,
            CommandSemantic.TORQUE_KD: TelemetrySemantic.TORQUE_KD,
            CommandSemantic.SPEEDS: TelemetrySemantic.CONFIGURED_SPEEDS,
            CommandSemantic.CURRENT_LIMITS: TelemetrySemantic.CURRENT_LIMITS,
            CommandSemantic.STALL_DETECTION_TIMES: (
                TelemetrySemantic.STALL_DETECTION_TIMES
            ),
            CommandSemantic.STALL_THRESHOLDS: TelemetrySemantic.STALL_THRESHOLDS,
            CommandSemantic.STALL_HOLD_CURRENTS: (
                TelemetrySemantic.STALL_HOLD_CURRENTS
            ),
        }.get(semantic)
        if response_semantic is not None:
            transaction = configuration_transaction(
                semantic,
                response_semantic,
                COMMAND_OPCODES[semantic],
                values,
                capabilities,
            )
            if semantic is CommandSemantic.TORQUES:
                # Do not infer the device mode from a prior ACK: V2.4.5 replies
                # with the previous settings. Any zero may return a joint to
                # position mode, including mixed-mode vectors. Guard both sides
                # of the transition without gating on a firmware version.
                settle_s = 0.15 if 0 in values else 0.02
                transaction = replace(
                    transaction,
                    pre_dispatch_delay_s=0.02,
                    post_dispatch_delay_s=settle_s,
                    estimated_duration_s=0.02 + settle_s + 0.005,
                )
            return transaction
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
