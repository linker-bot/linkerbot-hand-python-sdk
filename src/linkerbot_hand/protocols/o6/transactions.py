"""O6 protocol backend for control and fixed-width state transactions."""

from __future__ import annotations

from ...api.enums import HandSide
from ..common.classic_can import classic_route
from ..common.six_axis import ClassicSixAxisBackend
from .codec import (
    BASELINE_SEMANTICS,
    COMMAND_OPCODES,
    CONFIG_TELEMETRY,
    TELEMETRY_OPCODES,
)
from .identity import o6_identity_transaction


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
        )


__all__ = ["O6ProtocolBackend"]
