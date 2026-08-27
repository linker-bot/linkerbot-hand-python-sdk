"""L20Lite identity profile."""

from ...capabilities.types import DeviceCapabilities
from ..base import ProtocolTransaction
from ..common.classic_identity import basic_classic_identity_transaction


def l20lite_identity_transaction(
    capabilities: DeviceCapabilities,
) -> ProtocolTransaction:
    """Verify the L20Lite endpoint from its documented LHT10 product code."""

    return basic_classic_identity_transaction(
        capabilities,
        public_model="L20Lite",
        factory_models=("LHT10",),
    )


__all__ = ["l20lite_identity_transaction"]
