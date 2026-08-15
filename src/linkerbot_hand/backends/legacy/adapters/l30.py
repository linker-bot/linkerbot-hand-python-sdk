"""Adapter for the L30 CAN FD driver."""

from __future__ import annotations

from typing import Any

from ....contracts import DeviceIdentity, HandSide
from ....errors import DeviceNotReadyError

_PRODUCT_ID = 0x13


class L30Adapter:
    """Legacy L30 constructor and product-identity hooks."""

    model = "L30"
    module_name = "linkerbot.hand.l30"
    class_name = "L30"
    classic_raw_fallback = False

    def prepare_options(
        self,
        side: HandSide,
        options: dict[str, Any],
    ) -> dict[str, Any]:
        """Disable the L30 driver's constructor-started periodic activity."""
        prepared = dict(options)
        prepared.setdefault("auto_start_periodic", False)
        return prepared

    def read_identity(self, hand: Any) -> DeviceIdentity:
        """Read and verify an L30 product identity.

        Args:
            hand: Open legacy L30 instance.

        Returns:
            Verified model, firmware, side, and serial identity.

        Raises:
            DeviceNotReadyError: If the product identifier is not L30.
        """
        info = hand.version.get_device_info()
        if info.product_id != _PRODUCT_ID:
            raise DeviceNotReadyError(
                f"expected L30 product id 0x{_PRODUCT_ID:02X}, "
                f"got 0x{info.product_id:02X}"
            )
        return DeviceIdentity(
            model=self.model,
            firmware=str(info.software_version),
            side=HandSide(info.hand_side.value),
            serial_number=str(info.serial_number),
        )


__all__ = ["L30Adapter"]
