"""O6 identity profile."""

from ...capabilities.types import DeviceCapabilities
from ..base import ProtocolTransaction
from ..common.classic_identity import basic_classic_identity_transaction


def o6_identity_transaction(
    capabilities: DeviceCapabilities,
) -> ProtocolTransaction:
    """Verify the O6 product code and versions over the classic wire profile.

    The factory code does not gate the tactile channel: sensor variants other
    than 04-B carry tactile hardware too. A hand whose firmware does not answer
    the tactile query is downgraded at runtime by the firmware-optional path.
    """

    return basic_classic_identity_transaction(
        capabilities,
        public_model="O6",
        factory_models=("LHO6",),
    )


__all__ = ["o6_identity_transaction"]
