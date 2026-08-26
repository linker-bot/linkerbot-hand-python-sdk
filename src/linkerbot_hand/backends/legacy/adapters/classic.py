"""Adapter for classic CAN hand drivers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ....contracts import DeviceIdentity, HandSide
from ....errors import DeviceNotReadyError, ValidationError


@dataclass(frozen=True, slots=True)
class ClassicAdapter:
    """Metadata and identity hooks for classic CAN model classes.

    Attributes:
        model: Canonical unified model name.
        module_name: Import path containing the legacy class.
        class_name: Legacy class name.
        serial_tokens: Normalized serial-number tokens accepted for the model.
    """

    model: str
    module_name: str
    class_name: str
    serial_tokens: tuple[str, ...]

    def prepare_options(
        self,
        side: HandSide,
        options: dict[str, Any],
    ) -> dict[str, Any]:
        """Inject explicit side and disable legacy constructor polling.

        Args:
            side: Required hand side for classic drivers.
            options: Caller-supplied constructor options.

        Returns:
            Copied options with safe no-polling defaults.

        Raises:
            ValidationError: If no explicit side was supplied.
        """
        if side is HandSide.UNKNOWN:
            raise ValidationError(f"{self.model} requires an explicit hand side")
        prepared = dict(options)
        prepared.setdefault("side", side.value)
        prepared.setdefault("auto_start_polling", False)
        return prepared

    def read_identity(self, hand: Any) -> DeviceIdentity:
        """Read version data and verify the model from its serial number.

        Args:
            hand: Open legacy classic hand instance.

        Returns:
            Verified device identity.

        Raises:
            DeviceNotReadyError: If the serial number does not identify the model.
        """
        info = hand.version.get_device_info()
        serial_number = str(info.serial_number)
        normalized = "".join(
            character for character in serial_number.lower() if character.isalnum()
        )
        if not any(token in normalized for token in self.serial_tokens):
            raise DeviceNotReadyError(
                f"serial number {serial_number!r} does not identify {self.model}"
            )
        return DeviceIdentity(
            model=self.model,
            firmware=str(info.firmware_version),
            side=HandSide.UNKNOWN,
            serial_number=serial_number,
        )


__all__ = ["ClassicAdapter"]
