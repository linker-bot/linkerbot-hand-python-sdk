"""O6 04-B five-finger 10 by 4 tactile transaction."""

from ...capabilities.types import DeviceCapabilities
from ..base import ProtocolTransaction
from ..common.classic_can import classic_route
from ..common.tactile import tactile_transaction

_FINGER_QUERY_INTERVAL_S = 0.002


def o6_tactile_transaction(
    capabilities: DeviceCapabilities,
) -> ProtocolTransaction:
    """Build the complete pipelined ``B1..B5 A4`` transaction."""

    # O6 04-B can drop one B1 row when all five selectors arrive back-to-back.
    return tactile_transaction(
        capabilities,
        route=classic_route(capabilities.side),
        selector=0xA4,
        rows=10,
        columns=4,
        request_interval_s=_FINGER_QUERY_INTERVAL_S,
        response_timeout_s=0.08,
        late_response_guard_s=0.03,
    )


__all__ = ["o6_tactile_transaction"]
