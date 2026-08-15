"""Adapter for the O30i HandProtocol driver."""

from __future__ import annotations

from typing import Any

from ....contracts import DeviceIdentity, HandSide
from ....errors import DeviceNotReadyError

_PRODUCT_MODELS = frozenset({"o30i"})


class O30iAdapter:
    """Legacy O30i constructor and product-identity hooks."""

    model = "O30i"
    module_name = "linkerbot.hand.o30i"
    class_name = "O30i"
    classic_raw_fallback = False

    def prepare_options(
        self,
        side: HandSide,
        options: dict[str, Any],
    ) -> dict[str, Any]:
        """Return an isolated copy of O30i constructor options."""
        return dict(options)

    def read_identity(self, hand: Any) -> DeviceIdentity:
        """Read and verify an O30i product identity.

        Args:
            hand: Open legacy O30i instance.

        Returns:
            Verified model, firmware, and device UID identity.

        Raises:
            DeviceNotReadyError: If product information is not for an O30i.
        """
        info = hand.version.get_device_info()
        if info.product_model.strip().casefold() not in _PRODUCT_MODELS:
            raise DeviceNotReadyError(
                f"expected O30i device information, found {info.product_model!r}"
            )
        return DeviceIdentity(
            model=self.model,
            firmware=info.application_version,
            side=HandSide.UNKNOWN,
            serial_number=info.device_uid,
        )


__all__ = ["O30iAdapter"]
