"""O6 04-B five-finger 10 by 4 tactile transaction."""

from ...capabilities.types import DeviceCapabilities
from ..base import ProtocolTransaction
from ..common.classic_can import classic_route
from ..common.tactile import tactile_transaction


def o6_tactile_transaction(
    capabilities: DeviceCapabilities,
) -> ProtocolTransaction:
    """Build the complete pipelined ``B1..B5 A4`` transaction."""

    return tactile_transaction(
        capabilities,
        route=classic_route(capabilities.side),
        selector=0xA4,
        rows=10,
        columns=4,
        response_timeout_s=0.08,
        late_response_guard_s=0.03,
    )


__all__ = ["o6_tactile_transaction"]
