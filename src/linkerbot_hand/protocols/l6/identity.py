"""L6 identity profile."""

from ...capabilities.types import DeviceCapabilities
from ..base import ProtocolTransaction
from ..common.classic_identity import basic_classic_identity_transaction


def l6_identity_transaction(
    capabilities: DeviceCapabilities,
) -> ProtocolTransaction:
    """Verify the L6 product code and versions over the classic wire profile."""

    return basic_classic_identity_transaction(
        capabilities,
        public_model="L6",
        factory_models=("LHL6",),
        # L6's PCB version object and its C1 request (C1 01) differ from the
        # rest of the classic family; the flag switches both.
        l6_pcb_format=True,
    )


__all__ = ["l6_identity_transaction"]
