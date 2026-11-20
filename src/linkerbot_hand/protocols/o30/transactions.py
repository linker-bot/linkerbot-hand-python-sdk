"""O30 installed V25 objects atop the shared HOP endpoint mechanics."""

from ...api.enums import HandSide
from ...api.errors import UnsupportedOperationError
from ...api.readings import RawNumber
from ...capabilities.types import CommandSemantic, DeviceCapabilities, TelemetrySemantic
from ..base import ProtocolTransaction
from ..hop.backend import HOPBackend
from .control import command_transaction
from .framing import addressing_for_side
from .telemetry import read_transaction


class O30ProtocolBackend(HOPBackend):
    """Preserve deployed O30 defaults: right ID=1, left ID=2, BRS off."""

    def __init__(
        self,
        side: HandSide,
        *,
        request_id: int | None = None,
        brs: bool = False,
        can_fd: bool = True,
    ) -> None:
        super().__init__(
            side,
            request_id=addressing_for_side(side).request_id
            if request_id is None
            else request_id,
            brs=brs,
            can_fd=can_fd,
        )

    def command_transaction(
        self,
        semantic: CommandSemantic,
        values: tuple[RawNumber, ...],
        capabilities: DeviceCapabilities,
    ) -> ProtocolTransaction:
        return command_transaction(semantic, values, capabilities, self._addressing)

    def read_transaction(
        self, semantic: TelemetrySemantic, capabilities: DeviceCapabilities
    ) -> ProtocolTransaction:
        if semantic is TelemetrySemantic.FORCE:
            return self.sensor_transaction(capabilities)
        return read_transaction(semantic, capabilities, self._addressing)

    def operation_transaction(
        self, operation: str, capabilities: DeviceCapabilities
    ) -> ProtocolTransaction:
        raise UnsupportedOperationError(f"O30 does not support {operation}")


__all__ = ["O30ProtocolBackend"]
