"""L20Lite protocol backend."""

from ...api.enums import HandSide
from ...capabilities.types import DeviceCapabilities, TelemetrySemantic
from ..base import ProtocolTransaction
from ..common.classic_can import classic_route
from ..common.classic_multi_axis import ClassicMultiAxisBackend
from ..common.tactile import tactile_transaction
from .codec import (
    BASELINE_SEMANTICS,
    COMMAND_LAYOUTS,
    CONFIG_TELEMETRY,
    TELEMETRY_LAYOUTS,
)
from .identity import l20lite_identity_transaction


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
            # LHT10 specifies 1 ms between vector frames. Firmware 4.6.10
            # intermittently omits replies at the shared 0.5 ms default.
            request_interval_s=0.001,
        )

    def read_transaction(
        self,
        semantic: TelemetrySemantic,
        capabilities: DeviceCapabilities,
    ) -> ProtocolTransaction:
        """Build a vector read or one complete five-finger tactile read."""

        if semantic is TelemetrySemantic.FORCE:
            return tactile_transaction(
                capabilities,
                route=classic_route(capabilities.side),
                selector=0xC6,
                rows=12,
                columns=6,
                request_interval_s=0,
                response_timeout_s=0.15,
                late_response_guard_s=0.1,
            )
        return super().read_transaction(semantic, capabilities)


__all__ = ["L20LiteProtocolBackend"]
