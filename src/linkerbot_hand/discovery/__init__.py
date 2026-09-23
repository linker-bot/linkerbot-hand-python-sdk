"""Explicit, identity-only discovery for Linkerbot hand endpoints."""

from ._classic_routes import classic_discovery_route_key
from ._engine import discover_hands
from .contracts import (
    DiscoveryAddress,
    DiscoveryCandidate,
    DiscoveryChannel,
    DiscoveryConfig,
    DiscoveryEnumeration,
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
from .options import MiniCANFDDiscoveryConfig, PythonCANDiscoveryConfig
from .socketcan import SocketCANDiscoverySource
from .sources import (
    CompositeDiscoverySource,
    MiniCANFDDiscoverySource,
    PCANDiscoverySource,
    PlatformDiscoverySource,
    SLCANDiscoverySource,
)

__all__ = [
    "AmbiguousHandError",
    "CompositeDiscoverySource",
    "DiscoveryAddress",
    "DiscoveryCandidate",
    "DiscoveryChannel",
    "DiscoveryConfig",
    "DiscoveryEnumeration",
    "DiscoveryError",
    "DiscoveryFailure",
    "DiscoveryIdentity",
    "DiscoveryProblem",
    "DiscoveryReport",
    "DiscoverySource",
    "DiscoveryStatus",
    "IncompleteIdentityError",
    "MiniCANFDDiscoveryConfig",
    "MiniCANFDDiscoverySource",
    "NoHandFoundError",
    "PCANDiscoverySource",
    "PlatformDiscoverySource",
    "PythonCANDiscoveryConfig",
    "SLCANDiscoverySource",
    "SocketCANDiscoverySource",
    "TransportSpec",
    "classic_discovery_route_key",
    "discover_hands",
]
