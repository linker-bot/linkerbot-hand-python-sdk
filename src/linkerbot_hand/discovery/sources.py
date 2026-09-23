"""Platform adapter sources; identity probes remain in the discovery engine."""

from __future__ import annotations

import platform
from collections.abc import Mapping
from dataclasses import replace
from functools import partial
from typing import Any

import can

from linkerbot_transport.can import CANTransport, CANTransportKey
from linkerbot_transport.canfd import (
    CANFDTransport,
    PythonCANFDTransport,
    PythonCANFDTransportKey,
)
from linkerbot_transport.errors import ValidationError as TransportValidationError

from ..api.enums import TransportKind
from ..api.errors import ValidationError
from . import _budget
from ._classic_routes import classic_discovery_route_key
from .contracts import (
    DiscoveryChannel,
    DiscoveryConfig,
    DiscoveryEnumeration,
    DiscoveryFailure,
    DiscoverySource,
    TransportSpec,
)
from .errors import DiscoveryError
from .options import PythonCANDiscoveryConfig
from .socketcan import SocketCANDiscoverySource


def enumerate_source(
    source: DiscoverySource, config: DiscoveryConfig
) -> DiscoveryEnumeration:
    """Retain the original custom-source protocol and its optional diagnostics."""
    enumerate_channels = getattr(source, "enumerate_channels", None)
    if callable(enumerate_channels):
        result = enumerate_channels(config)
        if not isinstance(result, DiscoveryEnumeration):
            raise DiscoveryError("source returned invalid enumeration result")
        return result
    return DiscoveryEnumeration(channels=source.list_channels(config))


def _failure(
    backend: str, stage: str, error: Exception, native_channel: str | None = None
) -> DiscoveryFailure:
    return DiscoveryFailure(
        operation=f"enumerate_{backend}_{stage}",
        code=stage,
        message=str(error) or type(error).__name__,
        error_type=type(error).__name__,
        backend=backend,
        native_channel=native_channel,
    )


def _recipe(channel: DiscoveryChannel, wire_protocol: TransportKind) -> TransportSpec:
    for spec in channel.transport_specs:
        if spec.kind is wire_protocol:
            return spec
    raise ValidationError("channel has no recipe for the requested wire protocol")


def _select_channels(
    result: DiscoveryEnumeration, config: DiscoveryConfig
) -> DiscoveryEnumeration:
    if config.channels is None:
        return result
    channels = tuple(c for c in result.channels if c.name in config.channels)
    found = {c.name for c in channels}
    failures = list(result.failures)
    for missing in config.channels:
        if missing not in found:
            failures.append(
                _failure(
                    "selection",
                    "unavailable",
                    DiscoveryError("requested discovery channel was not enumerated"),
                    missing,
                )
            )
    return DiscoveryEnumeration(channels, tuple(failures))


class _AdapterSource:
    def list_channels(self, config: DiscoveryConfig) -> tuple[DiscoveryChannel, ...]:
        return self.enumerate_channels(config).channels

    def enumerate_channels(self, config: DiscoveryConfig) -> DiscoveryEnumeration:
        raise NotImplementedError

    def create_transport(
        self, channel: DiscoveryChannel, wire_protocol: TransportKind
    ) -> object:
        return _recipe(channel, wire_protocol).create_transport()


def _python_channel(
    interface: str,
    native_name: str,
    supports_fd: bool | None,
    options: PythonCANDiscoveryConfig,
) -> DiscoveryChannel:
    name = f"{interface}:{native_name}"
    order = options.probe_protocols or (
        (TransportKind.CAN,) if interface == "pcan" else (TransportKind.CAN_FD,)
    )
    if TransportKind.CAN_FD in order and supports_fd is False:
        # Keep classic detection on classic-only adapters under a mixed policy.
        order = tuple(p for p in order if p is not TransportKind.CAN_FD)
    if not order:
        raise ValidationError("adapter does not support the requested FD-only policy")
    recipes = []
    # Freeze primitive options and timing in each factory, independent of source
    # reuse and caller mutations. Every factory constructs a CLOSED transport.
    timing = options.fd_timing
    if TransportKind.CAN_FD in order and timing is None:
        timing = can.BitTimingFd.from_sample_point(
            f_clock=80_000_000,
            nom_bitrate=options.bitrate,
            nom_sample_point=80,
            data_bitrate=options.data_bitrate,
            data_sample_point=75,
        )
    for wire in order:
        try:
            # Constructors validate and freeze parameters without loading drivers.
            if wire is TransportKind.CAN:
                closed = CANTransport(
                    native_name,
                    interface,
                    bitrate=options.bitrate,
                    bus_options=options.bus_options,
                    route_key_of=classic_discovery_route_key,
                )
            else:
                closed = PythonCANFDTransport(
                    native_name,
                    interface,
                    bitrate=options.bitrate,
                    data_bitrate=options.data_bitrate,
                    bus_options=options.bus_options,
                    timing=timing,
                )
        except TransportValidationError as error:
            raise ValidationError(str(error)) from error
        recipes.append(
            TransportSpec(
                provider=interface,
                channel=name,
                kind=wire,
                nominal_bitrate=options.bitrate,
                data_bitrate=options.data_bitrate
                if wire is TransportKind.CAN_FD
                else None,
                factory=partial(_python_transport_from_key, closed.pool_key),
                connection_key=closed.pool_key,
            )
        )
    fd = supports_fd is True or TransportKind.CAN_FD in order
    return DiscoveryChannel(
        provider=interface,
        name=name,
        kind=TransportKind.CAN_FD if fd else TransportKind.CAN,
        nominal_bitrate=options.bitrate,
        data_bitrate=options.data_bitrate if fd else None,
        supports_fd=supports_fd,
        probe_protocols=order,
        transport_specs=tuple(recipes),
    )


def _python_transport_from_key(
    key: CANTransportKey | PythonCANFDTransportKey,
) -> CANTransport | PythonCANFDTransport:
    if isinstance(key, CANTransportKey):
        return CANTransport(
            key.interface_name,
            key.interface_type,
            bitrate=key.bitrate,
            bus_options=dict(key.bus_options),
            receive_own_messages=key.receive_own_messages,
            route_key_of=classic_discovery_route_key,
        )
    timing_values: dict[str, Any] = dict(key.timing)
    return PythonCANFDTransport(
        key.channel,
        key.interface,
        bitrate=key.bitrate,
        data_bitrate=key.data_bitrate,
        fd=key.fd,
        receive_own_messages=key.receive_own_messages,
        bus_options=dict(key.bus_options),
        timing=can.BitTimingFd(**timing_values) if timing_values else None,
    )


class PCANDiscoverySource(_AdapterSource):
    """Enumerate available PCAN channels; default to classic CAN probes."""

    def enumerate_channels(self, config: DiscoveryConfig) -> DiscoveryEnumeration:
        from linkerbot_transport.discovery import enumerate_pcan_channels

        result = enumerate_pcan_channels()
        options = config.pcan
        failures = [
            _failure("pcan", f.stage, f.error, f.channel) for f in result.failures
        ]
        channels = []
        found = set()
        for item in result.channels:
            if options.channels is not None and item.channel not in options.channels:
                continue
            found.add(item.channel)
            if item.supports_fd is False and options.probe_protocols == (
                TransportKind.CAN_FD,
            ):
                failures.append(
                    _failure(
                        "pcan",
                        "unsupported_protocol",
                        DiscoveryError("PCAN adapter does not support CAN FD"),
                        item.channel,
                    )
                )
                continue
            try:
                channels.append(
                    _python_channel("pcan", item.channel, item.supports_fd, options)
                )
            except ValidationError:
                raise
            except Exception as error:  # noqa: BLE001 - per-adapter diagnostic boundary
                failures.append(_failure("pcan", "channel", error, item.channel))
        if options.channels is not None:
            diagnosed = {f.native_channel for f in failures}
            for name in options.channels:
                if name not in found | diagnosed:
                    failures.append(
                        _failure(
                            "pcan",
                            "unavailable",
                            DiscoveryError("requested PCAN channel was not enumerated"),
                            name,
                        )
                    )
        return _select_channels(
            DiscoveryEnumeration(tuple(channels), tuple(failures)), config
        )


class SLCANDiscoverySource(_AdapterSource):
    """Enumerate known or explicit serial candidates; default to CAN FD probes."""

    def enumerate_channels(self, config: DiscoveryConfig) -> DiscoveryEnumeration:
        from linkerbot_transport.discovery import enumerate_slcan_channels

        result = enumerate_slcan_channels(ports=config.slcan.channels)
        channels = tuple(
            _python_channel("slcan", item.channel, item.supports_fd, config.slcan)
            for item in result.channels
        )
        failures = tuple(
            _failure("slcan", f.stage, f.error, f.channel) for f in result.failures
        )
        return _select_channels(DiscoveryEnumeration(channels, failures), config)


class MiniCANFDDiscoverySource(_AdapterSource):
    """Enumerate native vendor channels and probe only CAN FD hand protocols."""

    def enumerate_channels(self, config: DiscoveryConfig) -> DiscoveryEnumeration:
        from linkerbot_transport.canfd import enumerate_minicanfd_channels

        options = config.minicanfd
        result = enumerate_minicanfd_channels(
            None if options.library_path is None else str(options.library_path),
            linux_channels=options.linux_channels,
        )
        channels = []
        for item in result.channels:
            name = f"minicanfd:{item.device_index}:{item.channel_index}"
            factory = partial(
                CANFDTransport,
                device_index=item.device_index,
                channel_index=item.channel_index,
                library_path=item.library_path,
                config=options.config,
            )
            spec = TransportSpec(
                provider="minicanfd",
                channel=name,
                kind=TransportKind.CAN_FD,
                nominal_bitrate=options.config.nom_baud,
                data_bitrate=options.config.dat_baud,
                factory=factory,
                connection_key=factory().pool_key,
            )
            channels.append(
                DiscoveryChannel(
                    provider="minicanfd",
                    name=name,
                    kind=TransportKind.CAN_FD,
                    nominal_bitrate=options.config.nom_baud,
                    data_bitrate=options.config.dat_baud,
                    supports_classic=False,
                    supports_fd=True,
                    probe_protocols=(TransportKind.CAN_FD,),
                    transport_specs=(spec,),
                )
            )
        failures = tuple(
            _failure(
                "minicanfd",
                f.stage,
                f.error,
                None
                if f.device_index is None
                else f"{f.device_index}:{f.channel_index}",
            )
            for f in result.failures
        )
        # A cleanup failure means the library is not ready to open safely.
        if any(f.stage == "cleanup" for f in result.failures):
            channels.clear()
        return _select_channels(DiscoveryEnumeration(tuple(channels), failures), config)


class CompositeDiscoverySource:
    """Combine independent sources, retaining failures and filtering globally."""

    def __init__(self, sources: Mapping[str, DiscoverySource]) -> None:
        if not isinstance(sources, Mapping) or any(
            not isinstance(s, DiscoverySource) for s in sources.values()
        ):
            raise ValidationError(
                "sources must map backend names to DiscoverySource values"
            )
        self._sources = dict(sources)

    def list_channels(self, config: DiscoveryConfig) -> tuple[DiscoveryChannel, ...]:
        return self.enumerate_channels(config).channels

    def enumerate_channels(self, config: DiscoveryConfig) -> DiscoveryEnumeration:
        channels: dict[tuple[str, str], DiscoveryChannel] = {}
        conflicting: set[tuple[str, str]] = set()
        failures: list[DiscoveryFailure] = []
        selected = config.backends or tuple(self._sources)
        source_config = replace(config, channels=None)
        for name in selected:
            try:
                _budget.remaining(config.total_timeout_s)
                if name not in self._sources:
                    raise DiscoveryError(
                        f"backend {name} is unavailable on this platform"
                    )
                result = enumerate_source(self._sources[name], source_config)
                failures.extend(result.failures)
                for channel in result.channels:
                    key = (channel.provider, channel.name)
                    if key in channels and channels[key] != channel:
                        channels.pop(key)
                        conflicting.add(key)
                        failures.append(
                            _failure(
                                name,
                                "conflicting_channel",
                                DiscoveryError(
                                    f"conflicting channel descriptors: {channel.name}"
                                ),
                                channel.name,
                            )
                        )
                    elif key not in conflicting:
                        channels[key] = channel
            except ValidationError:
                raise
            except Exception as error:  # noqa: BLE001 - independent source boundary
                failures.append(
                    _failure(
                        name,
                        "timeout" if isinstance(error, TimeoutError) else "source",
                        error,
                    )
                )
                if _budget.expired():
                    break
        values = tuple(sorted(channels.values(), key=lambda c: (c.provider, c.name)))
        return _select_channels(DiscoveryEnumeration(values, tuple(failures)), config)

    def create_transport(
        self, channel: DiscoveryChannel, wire_protocol: TransportKind
    ) -> object:
        if channel.transport_specs:
            return _recipe(channel, wire_protocol).create_transport()
        return self._sources[channel.provider].create_transport(channel, wire_protocol)


class PlatformDiscoverySource(CompositeDiscoverySource):
    """Linux: SocketCAN/MiniCANFD; Windows: PCAN/SLCAN/MiniCANFD."""

    def __init__(self) -> None:
        system = platform.system()
        if system == "Linux":
            sources = {
                "socketcan": SocketCANDiscoverySource(),
                "minicanfd": MiniCANFDDiscoverySource(),
            }
        elif system == "Windows":
            sources = {
                "pcan": PCANDiscoverySource(),
                "slcan": SLCANDiscoverySource(),
                "minicanfd": MiniCANFDDiscoverySource(),
            }
        else:
            raise DiscoveryError(f"default discovery is unavailable on {system}")
        super().__init__(sources)


__all__ = [
    "CompositeDiscoverySource",
    "MiniCANFDDiscoverySource",
    "PCANDiscoverySource",
    "PlatformDiscoverySource",
    "SLCANDiscoverySource",
]
