"""Read-only Linux SocketCAN channel discovery source."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Sequence

from linkerbot_transport.can import CANTransport
from linkerbot_transport.canfd import SocketCANFDTransport

from ..api.enums import TransportKind
from ..api.errors import ValidationError
from .contracts import DiscoveryChannel, DiscoveryConfig
from .errors import DiscoveryError

_CAN_MTU = 16
_CANFD_MTU = 72
_CommandRunner = Callable[[Sequence[str]], str]


class SocketCANDiscoverySource:
    """Enumerate active Linux CAN links without changing kernel link state."""

    def __init__(self, *, command_runner: _CommandRunner | None = None) -> None:
        if command_runner is not None and not callable(command_runner):
            raise ValidationError("command_runner must be callable or None")
        self._command_runner = (
            _run_command if command_runner is None else command_runner
        )

    def list_channels(
        self,
        config: DiscoveryConfig,
    ) -> tuple[DiscoveryChannel, ...]:
        """Return active CAN/CAN FD links from ``ip -d -j link`` output."""

        if not isinstance(config, DiscoveryConfig):
            raise ValidationError("config must be DiscoveryConfig")
        try:
            raw = self._command_runner(
                ("ip", "-d", "-j", "link", "show", "type", "can")
            )
            payload = json.loads(raw)
        except DiscoveryError:
            raise
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            raise DiscoveryError(
                "could not decode SocketCAN link metadata",
                operation="list_discovery_channels",
                cause=error,
            ) from error
        if not isinstance(payload, list):
            raise DiscoveryError(
                "SocketCAN link metadata must be a JSON list",
                operation="list_discovery_channels",
            )
        channels = tuple(
            sorted(
                (
                    channel
                    for item in payload
                    if (channel := _parse_channel(item)) is not None
                ),
                key=lambda value: value.name,
            )
        )
        if config.channels is None:
            return channels
        selected = tuple(
            channel for channel in channels if channel.name in config.channels
        )
        missing = tuple(
            name
            for name in config.channels
            if name not in {value.name for value in selected}
        )
        if missing:
            raise DiscoveryError(
                f"requested SocketCAN channels are not active: {', '.join(missing)}",
                operation="list_discovery_channels",
            )
        return selected

    def create_transport(
        self,
        channel: DiscoveryChannel,
        wire_protocol: TransportKind,
    ) -> object:
        """Construct a closed SocketCAN transport without reconfiguration."""

        if not isinstance(channel, DiscoveryChannel):
            raise ValidationError("channel must be DiscoveryChannel")
        if channel.provider != "socketcan":
            raise ValidationError("SocketCAN source received a foreign channel")
        if wire_protocol is TransportKind.CAN:
            return CANTransport(channel.name)
        if wire_protocol is not TransportKind.CAN_FD:
            raise ValidationError("wire_protocol must be CAN or CAN FD")
        if channel.kind is not TransportKind.CAN_FD:
            raise ValidationError("classic CAN channels cannot carry CAN FD probes")
        if channel.nominal_bitrate is None or channel.data_bitrate is None:
            raise DiscoveryError(
                f"SocketCAN FD channel {channel.name!r} lacks bitrate metadata",
                operation="create_discovery_transport",
            )
        return SocketCANFDTransport(
            channel.name,
            bitrate=channel.nominal_bitrate,
            data_bitrate=channel.data_bitrate,
            fd=True,
            auto_reconfigure=False,
        )


def _run_command(command: Sequence[str]) -> str:
    try:
        completed = subprocess.run(
            tuple(command),
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as error:
        raise DiscoveryError(
            "the ip command is required for SocketCAN discovery",
            operation="list_discovery_channels",
            cause=error,
        ) from error
    except subprocess.CalledProcessError as error:
        detail = error.stderr.strip() or str(error)
        raise DiscoveryError(
            f"could not read SocketCAN link metadata: {detail}",
            operation="list_discovery_channels",
            cause=error,
        ) from error
    return completed.stdout


def _parse_channel(item: object) -> DiscoveryChannel | None:
    if not isinstance(item, dict):
        return None
    linkinfo = item.get("linkinfo")
    if not isinstance(linkinfo, dict) or linkinfo.get("info_kind") != "can":
        return None
    flags = item.get("flags", ())
    if not isinstance(flags, list) or "UP" not in flags:
        return None
    name = item.get("ifname")
    mtu = item.get("mtu")
    if not isinstance(name, str) or not name:
        raise DiscoveryError(
            "SocketCAN link has no interface name",
            operation="list_discovery_channels",
        )
    if mtu == _CAN_MTU:
        kind = TransportKind.CAN
    elif mtu == _CANFD_MTU:
        kind = TransportKind.CAN_FD
    else:
        raise DiscoveryError(
            f"SocketCAN channel {name!r} has unsupported MTU {mtu!r}",
            operation="list_discovery_channels",
        )
    info_data = linkinfo.get("info_data")
    if not isinstance(info_data, dict):
        info_data = {}
    nominal = _bitrate(info_data.get("bittiming"))
    data = (
        _bitrate(info_data.get("data_bittiming"))
        if kind is TransportKind.CAN_FD
        else None
    )
    return DiscoveryChannel(
        provider="socketcan",
        name=name,
        kind=kind,
        nominal_bitrate=nominal,
        data_bitrate=data,
    )


def _bitrate(value: object) -> int | None:
    if not isinstance(value, dict):
        return None
    bitrate = value.get("bitrate")
    if type(bitrate) is int and bitrate > 0:
        return bitrate
    return None


__all__ = ["SocketCANDiscoverySource"]
