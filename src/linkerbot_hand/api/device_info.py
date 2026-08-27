"""Immutable device identity and version samples."""

from __future__ import annotations

from dataclasses import dataclass

from .enums import HandSide
from .errors import ValidationError
from .readings import SampleMeta

VersionRaw = bytes | str | tuple[int, ...]


@dataclass(frozen=True, slots=True)
class VersionField:
    """One version field preserving both wire data and readable text."""

    raw: VersionRaw
    text: str
    major: int | None = None
    minor: int | None = None
    patch: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.raw, (bytes, str, tuple)):
            raise ValidationError("version raw has an unsupported type")
        if isinstance(self.raw, tuple) and any(
            type(value) is not int or not 0 <= value <= 255 for value in self.raw
        ):
            raise ValidationError("version raw tuple must contain bytes")
        if not isinstance(self.text, str) or not self.text:
            raise ValidationError("version text must be a non-empty str")
        parts = (self.major, self.minor, self.patch)
        if any(
            value is not None and (type(value) is not int or value < 0)
            for value in parts
        ):
            raise ValidationError("semantic version parts must be non-negative ints")
        if self.minor is not None and self.major is None:
            raise ValidationError("minor version requires major")
        if self.patch is not None and self.minor is None:
            raise ValidationError("patch version requires minor")


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    """Verified device identity; unsupported protocol fields remain ``None``."""

    model: str
    serial_number: str | None
    pcb_version: VersionField | None
    firmware_version: VersionField | None
    mechanical_version: VersionField | None
    product_code: str | None
    sensor_variant: str | None
    side: HandSide
    raw_identity: bytes | tuple[int, ...] | None
    meta: SampleMeta

    def __post_init__(self) -> None:
        if not isinstance(self.model, str) or not self.model:
            raise ValidationError("device model must be a non-empty str")
        for name in (
            "serial_number",
            "product_code",
            "sensor_variant",
        ):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value):
                raise ValidationError(f"{name} must be a non-empty str or None")
        for name in (
            "pcb_version",
            "firmware_version",
            "mechanical_version",
        ):
            value = getattr(self, name)
            if value is not None and not isinstance(value, VersionField):
                raise ValidationError(f"{name} must be VersionField or None")
        if not isinstance(self.side, HandSide):
            raise ValidationError("side must be HandSide")
        if isinstance(self.raw_identity, tuple) and any(
            type(value) is not int or not 0 <= value <= 255
            for value in self.raw_identity
        ):
            raise ValidationError("raw_identity tuple must contain bytes")
        if self.raw_identity is not None and not isinstance(
            self.raw_identity, (bytes, tuple)
        ):
            raise ValidationError("raw_identity must be bytes, tuple, or None")
        if not isinstance(self.meta, SampleMeta):
            raise ValidationError("meta must be SampleMeta")


__all__ = ["DeviceInfo", "VersionField", "VersionRaw"]
