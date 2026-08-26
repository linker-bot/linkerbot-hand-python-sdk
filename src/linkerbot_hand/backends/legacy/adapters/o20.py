"""Adapter for the O20 CAN FD driver."""

from __future__ import annotations

from typing import Any

from ....contracts import DeviceIdentity, HandSide
from ....errors import DeviceNotReadyError

_PRODUCT_MODELS = frozenset({"o20", "o20-l", "o20-r"})


class O20Adapter:
    """Legacy O20 constructor and product-identity hooks."""

    model = "O20"
    module_name = "linkerbot.hand.o20"
    class_name = "O20"

    def prepare_options(
        self,
        side: HandSide,
        options: dict[str, Any],
    ) -> dict[str, Any]:
        """Copy constructor options and attach a known hand side."""
        prepared = dict(options)
        if side is not HandSide.UNKNOWN:
            prepared.setdefault("side", side.value)
        return prepared

    def read_identity(self, hand: Any) -> DeviceIdentity:
        """Read and verify an O20 product identity.

        Args:
            hand: Open legacy O20 instance.

        Returns:
            Verified model, firmware, side, and serial identity.

        Raises:
            DeviceNotReadyError: If product information is not for an O20.
        """
        info = hand.version.get_device_info()
        if info.product_model.strip().casefold() not in _PRODUCT_MODELS:
            raise DeviceNotReadyError(
                f"expected O20 device information, found {info.product_model!r}"
            )
        return DeviceIdentity(
            model=self.model,
            firmware=info.software_version,
            side=HandSide(info.hand_side.value),
            serial_number=info.serial_number,
        )


__all__ = ["O20Adapter"]
