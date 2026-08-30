"""L6 five-finger 12 by 6 tactile transaction."""

from dataclasses import replace

from ...capabilities.types import DeviceCapabilities
from ..base import ProtocolTransaction
from ..common.classic_can import classic_route
from ..common.tactile import response_gated_tactile_transaction


def l6_tactile_transaction(
    capabilities: DeviceCapabilities,
) -> ProtocolTransaction:
    """Build a complete ``B1..B5 C6`` transaction gated by each finger."""

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


__all__ = ["l6_tactile_transaction"]
