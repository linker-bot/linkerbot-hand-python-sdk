"""Pure parsing of O6 factory serial-number identities."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class O6SerialIdentity:
    """Decoded fields from an O6 factory serial number.

    O6 labels use the form ``LHO6-04.1-730-R-B-1-D``. Older or incomplete
    labels remain readable: fields that cannot be decoded are returned as
    ``None`` instead of making the version query fail.

    Attributes:
        product_model: Product token, such as ``LHO6``.
        product_version: Product revision token, such as ``04.1``.
        serial_number: Per-device serial token, such as ``730``.
        hand_side: ``left`` or ``right`` when the side token is recognized.
        sensor_type: Sensor option token, such as ``B`` or ``Z``.
        sensor_revision: Sensor revision token, when present.
        factory_code: Final factory or assembly token, when present.
    """

    product_model: str | None = None
    product_version: str | None = None
    serial_number: str | None = None
    hand_side: str | None = None
    sensor_type: str | None = None
    sensor_revision: str | None = None
    factory_code: str | None = None

    @property
    def capability_variant(self) -> str | None:
        """Return the verified model variant used by unified capabilities."""
        if self.product_version is None or self.sensor_type is None:
            return None
        version_family = self.product_version.split(".", 1)[0]
        if version_family != "04":
            return None
        return f"04-{self.sensor_type.upper()}"

    @property
    def force_sensor_shape(self) -> tuple[int, int] | None:
        """Return the implemented tactile matrix shape for this variant."""
        if self.capability_variant == "04-B":
            return (10, 4)
        return None


def parse_o6_serial_identity(serial_number: str) -> O6SerialIdentity:
    """Decode an O6 factory serial number without performing device I/O.

    Args:
        serial_number: ASCII value assembled from the ``0xC0`` response.

    Returns:
        Parsed identity. Missing or unknown fields are represented by ``None``.
    """
    parts = tuple(part.strip() for part in serial_number.strip().split("-"))
    side = None
    if len(parts) > 3:
        side = {"L": "left", "R": "right"}.get(parts[3].upper())
    return O6SerialIdentity(
        product_model=(parts[0] or None) if parts else None,
        product_version=(parts[1] or None) if len(parts) > 1 else None,
        serial_number=(parts[2] or None) if len(parts) > 2 else None,
        hand_side=side,
        sensor_type=(parts[4].upper() or None) if len(parts) > 4 else None,
        sensor_revision=(parts[5] or None) if len(parts) > 5 else None,
        factory_code=(parts[6].upper() or None) if len(parts) > 6 else None,
    )


__all__ = ["O6SerialIdentity", "parse_o6_serial_identity"]
