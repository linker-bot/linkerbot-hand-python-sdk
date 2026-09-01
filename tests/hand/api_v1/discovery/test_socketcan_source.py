"""Read-only SocketCAN discovery-source contract tests."""

from __future__ import annotations

import json

import pytest

from linkerbot_hand import (
    DiscoveryConfig,
    DiscoveryError,
    SocketCANDiscoverySource,
    TransportKind,
)
from linkerbot_transport.can import CANTransport
from linkerbot_transport.canfd import SocketCANFDTransport


def _link(
    name: str,
    *,
    mtu: int,
    up: bool = True,
    bitrate: int = 1_000_000,
    data_bitrate: int | None = None,
) -> dict[str, object]:
    info_data: dict[str, object] = {"bittiming": {"bitrate": bitrate}}
    if data_bitrate is not None:
        info_data["data_bittiming"] = {"bitrate": data_bitrate}
    return {
        "ifname": name,
        "mtu": mtu,
        "flags": ["UP", "LOWER_UP"] if up else ["NOARP"],
        "linkinfo": {"info_kind": "can", "info_data": info_data},
    }


def test_source_lists_only_active_can_links_and_preserves_bitrates() -> None:
    payload = json.dumps(
        [
            _link("can2", mtu=72, data_bitrate=5_000_000),
            _link("can0", mtu=16),
            _link("can1", mtu=16, up=False),
            {"ifname": "eth0", "mtu": 1500, "flags": ["UP"]},
        ]
    )
    source = SocketCANDiscoverySource(command_runner=lambda command: payload)

    channels = source.list_channels(DiscoveryConfig())

    assert tuple(channel.name for channel in channels) == ("can0", "can2")
    assert channels[0].kind is TransportKind.CAN
    assert channels[0].nominal_bitrate == 1_000_000
    assert channels[0].data_bitrate is None
    assert channels[1].kind is TransportKind.CAN_FD
    assert channels[1].data_bitrate == 5_000_000


def test_source_channel_filter_rejects_missing_or_down_links() -> None:
    payload = json.dumps([_link("can0", mtu=16), _link("can1", mtu=16, up=False)])
    source = SocketCANDiscoverySource(command_runner=lambda command: payload)

    with pytest.raises(DiscoveryError, match="not active"):
        source.list_channels(DiscoveryConfig(channels=("can1",)))


def test_transport_factories_are_closed_and_never_auto_reconfigure() -> None:
    payload = json.dumps([_link("can0", mtu=72, data_bitrate=5_000_000)])
    source = SocketCANDiscoverySource(command_runner=lambda command: payload)
    channel = source.list_channels(DiscoveryConfig())[0]

    classic = source.create_transport(channel, TransportKind.CAN)
    can_fd = source.create_transport(channel, TransportKind.CAN_FD)

    assert isinstance(classic, CANTransport)
    assert isinstance(can_fd, SocketCANFDTransport)
    assert can_fd.pool_key.auto_reconfigure is False
    assert can_fd.pool_key.bitrate == 1_000_000
    assert can_fd.pool_key.data_bitrate == 5_000_000


def test_source_rejects_unknown_can_mtu() -> None:
    payload = json.dumps([_link("can0", mtu=64)])
    source = SocketCANDiscoverySource(command_runner=lambda command: payload)

    with pytest.raises(DiscoveryError, match="unsupported MTU"):
        source.list_channels(DiscoveryConfig())
