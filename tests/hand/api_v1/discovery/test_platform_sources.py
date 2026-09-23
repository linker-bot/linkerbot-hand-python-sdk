"""Cross-platform enumeration and actual transport recipes with simulated peers."""

import can
import pytest

from linkerbot_hand import (
    CompositeDiscoverySource,
    DiscoveryConfig,
    DiscoveryEnumeration,
    MiniCANFDDiscoveryConfig,
    MiniCANFDDiscoverySource,
    PCANDiscoverySource,
    PlatformDiscoverySource,
    PythonCANDiscoveryConfig,
    SLCANDiscoverySource,
    TransportKind,
    ValidationError,
)
from linkerbot_hand.discovery import sources
from linkerbot_transport import TransportState
from linkerbot_transport import discovery as adapter_discovery
from linkerbot_transport.canfd import (
    MiniCANFDChannel,
    MiniCANFDEnumerationResult,
)
from linkerbot_transport.errors import TransportError


def pcan_result(fd=True):
    return adapter_discovery.PythonCANEnumerationResult(
        channels=(adapter_discovery.PythonCANChannel("pcan", "PCAN_USBBUS1", fd),)
    )


def slcan_result():
    return adapter_discovery.PythonCANEnumerationResult(
        channels=(adapter_discovery.PythonCANChannel("slcan", "COM5", None),)
    )


@pytest.fixture
def enumerators(monkeypatch):
    from linkerbot_transport import canfd

    monkeypatch.setattr(
        adapter_discovery, "enumerate_pcan_channels", lambda: pcan_result()
    )
    monkeypatch.setattr(
        adapter_discovery, "enumerate_slcan_channels", lambda **kw: slcan_result()
    )
    monkeypatch.setattr(
        canfd,
        "enumerate_minicanfd_channels",
        lambda *args, **kw: MiniCANFDEnumerationResult(
            channels=(MiniCANFDChannel(0, 0, "/fake/libcanbus.so"),)
        ),
    )


@pytest.mark.parametrize(
    "source_type,expected",
    [
        (PCANDiscoverySource, (TransportKind.CAN,)),
        (SLCANDiscoverySource, (TransportKind.CAN_FD,)),
        (MiniCANFDDiscoverySource, (TransportKind.CAN_FD,)),
    ],
)
def test_default_probe_policy_and_closed_connection_recipes(
    enumerators, source_type, expected
):
    source = source_type()
    result = source.enumerate_channels(DiscoveryConfig())
    assert not result.failures
    (channel,) = result.channels
    assert channel.probe_protocols == expected
    if channel.provider == "slcan":
        assert channel.supports_fd is None
    for spec in channel.transport_specs:
        transport = spec.create_transport()
        assert transport.get_health().state is TransportState.CLOSED
        assert transport.pool_key == spec.connection_key
        assert (
            source.create_transport(channel, spec.kind).pool_key == spec.connection_key
        )


def test_connection_settings_survive_caller_mutation_and_reenumeration(enumerators):
    original = {"tty_baudrate": 460800}
    options = PythonCANDiscoveryConfig(bus_options=original, data_bitrate=2_000_000)
    original["tty_baudrate"] = 9600
    source = SLCANDiscoverySource()
    (channel,) = source.list_channels(DiscoveryConfig(slcan=options))
    (spec,) = channel.transport_specs
    source.list_channels(DiscoveryConfig())
    transport = spec.create_transport()
    assert transport.pool_key.data_bitrate == 2_000_000
    assert dict(transport.pool_key.bus_options)["tty_baudrate"] == 460800
    assert transport.pool_key == spec.connection_key


def test_pcan_explicit_fd_uses_matching_timing(enumerators):
    options = PythonCANDiscoveryConfig(
        probe_protocols=(TransportKind.CAN, TransportKind.CAN_FD),
        data_bitrate=2_000_000,
    )
    (channel,) = PCANDiscoverySource().list_channels(DiscoveryConfig(pcan=options))
    spec = channel.transport_specs[1]
    key = spec.create_transport().pool_key
    timing = can.BitTimingFd(**dict(key.timing))
    assert timing.nom_bitrate == 1_000_000
    assert timing.data_bitrate == 2_000_000


def test_pcan_fd_only_skips_classic_only_hardware(monkeypatch):
    monkeypatch.setattr(
        adapter_discovery, "enumerate_pcan_channels", lambda: pcan_result(fd=False)
    )
    result = PCANDiscoverySource().enumerate_channels(
        DiscoveryConfig(
            pcan=PythonCANDiscoveryConfig(probe_protocols=(TransportKind.CAN_FD,))
        )
    )
    assert not result.channels
    assert result.failures[0].code == "unsupported_protocol"


@pytest.mark.parametrize(
    "system,expected",
    [
        ("Linux", ("socketcan", "minicanfd")),
        ("Windows", ("pcan", "slcan", "minicanfd")),
    ],
)
def test_platform_defaults_only_enumerate_selected_sources(
    monkeypatch, system, expected
):
    monkeypatch.setattr(sources.platform, "system", lambda: system)
    calls = []
    for name, cls in (
        ("socketcan", sources.SocketCANDiscoverySource),
        ("pcan", PCANDiscoverySource),
        ("slcan", SLCANDiscoverySource),
        ("minicanfd", MiniCANFDDiscoverySource),
    ):

        def enumerate_channels(self, config, name=name):
            calls.append(name)
            return DiscoveryEnumeration()

        monkeypatch.setattr(
            cls, "enumerate_channels", enumerate_channels, raising=False
        )
    source = PlatformDiscoverySource()
    assert calls == []
    assert source.enumerate_channels(DiscoveryConfig()).failures == ()
    assert tuple(calls) == expected
    calls.clear()
    source.enumerate_channels(DiscoveryConfig(backends=(expected[0],)))
    assert calls == [expected[0]]


def test_backend_failure_does_not_hide_other_channels_and_filter_is_global(enumerators):
    class Broken:
        def list_channels(self, config):
            raise OSError("missing ip command")

        def create_transport(self, channel, wire):
            pytest.fail("broken source cannot create a transport")

    source = CompositeDiscoverySource(
        {"socketcan": Broken(), "slcan": SLCANDiscoverySource()}
    )
    result = source.enumerate_channels(DiscoveryConfig(channels=("slcan:COM5",)))
    assert [c.name for c in result.channels] == ["slcan:COM5"]
    assert len(result.failures) == 1
    assert result.failures[0].backend == "socketcan"


def test_pcan_driver_diagnostic_survives_composition(monkeypatch):
    failure = adapter_discovery.PythonCANEnumerationFailure(
        "pcan", "driver", TransportError("DLL missing")
    )
    monkeypatch.setattr(
        adapter_discovery,
        "enumerate_pcan_channels",
        lambda: adapter_discovery.PythonCANEnumerationResult(failures=(failure,)),
    )
    report = CompositeDiscoverySource(
        {"pcan": PCANDiscoverySource()}
    ).enumerate_channels(DiscoveryConfig())
    assert report.failures[0].backend == "pcan"
    assert report.failures[0].code == "driver"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"total_timeout_s": 0},
        {"total_timeout_s": float("nan")},
        {"backends": ("unknown",)},
        {"backends": ([],)},
        {"pcan": {}},
    ],
)
def test_invalid_discovery_configuration(kwargs):
    with pytest.raises(ValidationError):
        DiscoveryConfig(**kwargs)


@pytest.mark.parametrize(
    "source_type", [PCANDiscoverySource, SLCANDiscoverySource, MiniCANFDDiscoverySource]
)
def test_direct_usb_source_respects_global_channel_filter(enumerators, source_type):
    result = source_type().enumerate_channels(DiscoveryConfig(channels=("absent",)))
    assert not result.channels
    assert result.failures[-1].native_channel == "absent"


def test_mini_layout_is_snapshotted():
    channels = [0]
    layouts = {0: channels}
    options = MiniCANFDDiscoveryConfig(linux_channels=layouts)
    channels.append(1)
    layouts[1] = [0]
    assert dict(options.linux_channels) == {0: (0,)}
