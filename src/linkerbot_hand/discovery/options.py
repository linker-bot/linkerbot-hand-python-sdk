"""Immutable backend settings for explicit cross-platform discovery."""

from __future__ import annotations

import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

import can

from linkerbot_transport.canfd import CANFDConfigOptions

from ..api.enums import TransportKind
from ..api.errors import ValidationError

BusOption = str | int | float | bool | None


def protocol_order(value: object) -> tuple[TransportKind, ...]:
    if (
        not isinstance(value, tuple)
        or not value
        or any(
            not isinstance(item, TransportKind)
            or item not in {TransportKind.CAN, TransportKind.CAN_FD}
            for item in value
        )
        or len(set(value)) != len(value)
    ):
        raise ValidationError("probe_protocols must contain unique CAN/CAN_FD values")
    return value


@dataclass(frozen=True, slots=True)
class PythonCANDiscoveryConfig:
    """PCAN/SLCAN configuration; None order uses the backend default.

    ``channels`` contains native names (PCAN_USBBUS1 or COM5). Global report
    filtering uses DiscoveryConfig.channels instead. SLCAN serial baudrate is
    ``bus_options={'tty_baudrate': 115200}``, distinct from CAN bitrates.
    """

    channels: tuple[str, ...] | None = None
    bitrate: int = 1_000_000
    data_bitrate: int = 5_000_000
    probe_protocols: tuple[TransportKind, ...] | None = None
    bus_options: Mapping[str, BusOption] = field(default_factory=dict)
    fd_timing: can.BitTimingFd | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.channels is not None and (
            not isinstance(self.channels, tuple)
            or not self.channels
            or any(
                not isinstance(name, str) or not name.strip() for name in self.channels
            )
        ):
            raise ValidationError("backend channels must be a non-empty tuple of names")
        for name in ("bitrate", "data_bitrate"):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValidationError(f"{name} must be a positive integer")
        if self.probe_protocols is not None:
            protocol_order(self.probe_protocols)
        if not isinstance(self.bus_options, Mapping):
            raise ValidationError("bus_options must be a mapping")
        options = dict(self.bus_options)
        reserved = {
            "channel",
            "interface",
            "bitrate",
            "data_bitrate",
            "fd",
            "timing",
            "receive_own_messages",
            "ignore_config",
        }
        for key, value in options.items():
            if not isinstance(key, str) or not key or key in reserved:
                raise ValidationError(f"invalid or reserved bus option: {key}")
            if not isinstance(value, (str, int, float, bool, type(None))) or (
                isinstance(value, float) and not math.isfinite(value)
            ):
                raise ValidationError(
                    "bus_options values must be finite immutable scalars"
                )
        object.__setattr__(self, "bus_options", MappingProxyType(options))
        if self.fd_timing is not None:
            if not isinstance(self.fd_timing, can.BitTimingFd):
                raise ValidationError("fd_timing must be BitTimingFd or None")
            timing_values: dict[str, Any] = dict(self.fd_timing)
            timing = can.BitTimingFd(**timing_values)
            if (
                timing.nom_bitrate != self.bitrate
                or timing.data_bitrate != self.data_bitrate
            ):
                raise ValidationError("fd_timing must match the configured bitrates")
            object.__setattr__(self, "fd_timing", timing)


@dataclass(frozen=True, slots=True)
class MiniCANFDDiscoveryConfig:
    """Vendor settings with optional explicit Linux channel layouts.

    ``linux_channels=None`` starts at channel 0 on every scanned Linux adapter;
    Channel 1 is checked if channel 0 cannot be enumerated as FD, or after
    a clean, unanswered channel 0 identity probe.
    Callers need not know the adapter count. Explicit channel selections
    override this default. Windows uses native enumeration and rejects layouts.
    """

    library_path: str | os.PathLike[str] | None = None
    linux_channels: Mapping[int, Sequence[int]] | None = None
    config: CANFDConfigOptions = field(default_factory=CANFDConfigOptions)

    def __post_init__(self) -> None:
        if self.library_path is not None:
            if not isinstance(self.library_path, (str, os.PathLike)) or not os.fspath(
                self.library_path
            ):
                raise ValidationError("library_path must be a non-empty path")
            object.__setattr__(
                self,
                "library_path",
                os.path.abspath(os.path.expanduser(self.library_path)),
            )
        if not isinstance(self.config, CANFDConfigOptions):
            raise ValidationError("config must be CANFDConfigOptions")
        if self.linux_channels is not None:
            if not isinstance(self.linux_channels, Mapping):
                raise ValidationError(
                    "linux_channels must map device indices to channel indices"
                )
            layouts = {}
            for device, channels in self.linux_channels.items():
                if type(device) is not int or not 0 <= device <= 0xFFFFFFFF:
                    raise ValidationError("MiniCANFD device indices must fit uint32")
                if not isinstance(channels, Sequence) or isinstance(
                    channels, (str, bytes)
                ):
                    raise ValidationError("MiniCANFD channels must be sequences")
                values = tuple(channels)
                if any(
                    type(c) is not int or not 0 <= c <= 0xFFFFFFFF for c in values
                ) or len(set(values)) != len(values):
                    raise ValidationError(
                        "MiniCANFD channels must be unique uint32 indices"
                    )
                layouts[device] = values
            object.__setattr__(self, "linux_channels", MappingProxyType(layouts))


__all__ = ["MiniCANFDDiscoveryConfig", "PythonCANDiscoveryConfig"]
