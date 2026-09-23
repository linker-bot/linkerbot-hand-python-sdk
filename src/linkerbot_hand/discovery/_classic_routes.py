"""Bounded receive routing for classic CAN broadcast identity replies."""

from __future__ import annotations

import can

from linkerbot_transport import CANRouteKey

from ..api.enums import HandSide
from ..protocols.common.classic_can import classic_route, matching_data

CLASSIC_BROADCAST_ROUTE = CANRouteKey(0xFF, False)
CLASSIC_DEFAULT_ROUTES = (
    classic_route(HandSide.RIGHT),
    classic_route(HandSide.LEFT),
)
CLASSIC_DISCOVERY_ROUTES = (*CLASSIC_DEFAULT_ROUTES, CLASSIC_BROADCAST_ROUTE)


def classic_discovery_route_key(message: can.Message) -> CANRouteKey:
    """Keep default routes and collect other C0 replies on the broadcast route.

    The original CAN ID and payload remain unchanged. Custom discovery sources
    can pass this extractor to CANTransport without registering every CAN ID.
    Normal device transactions on the default addresses retain their routes.
    """

    route = CANRouteKey(message.arbitration_id, message.is_extended_id)
    if route not in CLASSIC_DEFAULT_ROUTES:
        data = matching_data(message, route)
        if data is not None and data.startswith(b"\xc0"):
            return CLASSIC_BROADCAST_ROUTE
    return route
