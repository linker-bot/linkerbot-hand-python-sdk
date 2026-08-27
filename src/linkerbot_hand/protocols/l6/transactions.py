"""L6 protocol backend for control and fixed-width state transactions."""

from __future__ import annotations

from ...api.enums import HandSide
from ...capabilities.types import CommandSemantic, TelemetrySemantic
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
            command_cache={
                CommandSemantic.SPEEDS: TelemetrySemantic.CONFIGURED_SPEEDS,
            },
            config_telemetry=CONFIG_TELEMETRY,
        )


__all__ = ["L6ProtocolBackend"]
