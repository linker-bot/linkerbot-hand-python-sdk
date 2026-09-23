"""Explicit, identity-only discovery for Linkerbot hand endpoints."""

from ._classic_routes import classic_discovery_route_key
from ._engine import discover_hands
from .contracts import (
    DiscoveryAddress,
    DiscoveryCandidate,
    DiscoveryChannel,
    DiscoveryConfig,
    DiscoveryFailure,
    DiscoveryIdentity,
    DiscoveryProblem,
    DiscoveryReport,
    DiscoverySource,
    DiscoveryStatus,
    TransportSpec,
)
from .errors import (
    AmbiguousHandError,
    DiscoveryError,
    IncompleteIdentityError,
    NoHandFoundError,
)
from .socketcan import SocketCANDiscoverySource

__all__ = [
    "AmbiguousHandError",
    "DiscoveryAddress",
    "DiscoveryCandidate",
    "DiscoveryChannel",
    "DiscoveryConfig",
    "DiscoveryError",
    "DiscoveryFailure",
    "DiscoveryIdentity",
    "DiscoveryProblem",
    "DiscoveryReport",
    "DiscoverySource",
    "DiscoveryStatus",
    "IncompleteIdentityError",
    "NoHandFoundError",
    "SocketCANDiscoverySource",
    "TransportSpec",
    "classic_discovery_route_key",
    "discover_hands",
]
