"""L25 identity profile."""

from ...capabilities.types import DeviceCapabilities
from ..base import ProtocolTransaction
from ..common.classic_identity import basic_classic_identity_transaction


def l25_identity_transaction(
    capabilities: DeviceCapabilities,
) -> ProtocolTransaction:
    """Verify L25/G20 product-code variants sharing the L25 wire profile."""

    return basic_classic_identity_transaction(
        capabilities,
        public_model="L25",
        factory_models=("L25", "LHL25", "LHG20"),
    )


__all__ = ["l25_identity_transaction"]
