"""L20 identity profile."""

from ...capabilities.types import DeviceCapabilities
from ..base import ProtocolTransaction
from ..common.classic_identity import basic_classic_identity_transaction


def l20_identity_transaction(
    capabilities: DeviceCapabilities,
) -> ProtocolTransaction:
    """Verify L20 product-code variants sharing the classic wire profile."""

    return basic_classic_identity_transaction(
        capabilities,
        public_model="L20",
        # These are factory identifiers found in deployed devices.  They are
        # deliberately kept as wire-level aliases while the public model is
        # normalized to L20.
        factory_models=("L20", "L25", "LHL25", "LHG20"),
    )


__all__ = ["l20_identity_transaction"]
