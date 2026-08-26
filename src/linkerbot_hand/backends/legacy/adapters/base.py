"""Contract implemented by each legacy driver family adapter."""

from __future__ import annotations

from typing import Any, Protocol

from ....contracts import DeviceIdentity, HandSide


class LegacyModelAdapter(Protocol):
    """Model-family hooks required by :class:`LegacyHandBackend`."""

    @property
    def model(self) -> str:
        """Return the canonical unified model name."""
        ...

    @property
    def module_name(self) -> str:
        """Return the import path containing the legacy model class."""
        ...

    @property
    def class_name(self) -> str:
        """Return the legacy model class name."""
        ...

    def prepare_options(
        self,
        side: HandSide,
        options: dict[str, Any],
    ) -> dict[str, Any]:
        """Prepare legacy constructor options without opening the device.

        Args:
            side: Requested hand side.
            options: Caller-supplied transport and driver options.

        Returns:
            A new options mapping suitable for the legacy constructor.
        """
        ...

    def read_identity(self, hand: Any) -> DeviceIdentity:
        """Read and verify identity from an opened legacy hand instance."""
        ...


__all__ = ["LegacyModelAdapter"]
