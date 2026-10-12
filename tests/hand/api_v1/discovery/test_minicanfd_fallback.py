"""Linux MiniCANFD checks channels 0 then 1 until an identity candidate is found."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from linkerbot_hand import (
    CompositeDiscoverySource,
    IncompleteIdentityError,
    MiniCANFDDiscoveryConfig,
    MiniCANFDDiscoverySource,
    NoHandFoundError,
    TransportKind,
    discover_hands,
)
from linkerbot_transport import TransportState
from linkerbot_transport.canfd import CANFDTransport, CANFDTransportKey
from linkerbot_transport.errors import TransportError
from tests.hand.api_v1.runtime.o30_canfd_fake import (
    O30DeviceScript,
    ScriptedO30Transport,
)

from .test_discovery import _FAST, _empty_transport
from .test_platform_discovery import PeerDispatcher
from .test_platform_sources import minicanfd_library as _minicanfd_library

minicanfd_library = _minicanfd_library


class MiniPeers(MiniCANFDDiscoverySource):
    def __init__(self, peers):
        self.peers = peers
        self.created = []

    def create_transport(self, channel, wire_protocol):
        assert wire_protocol is TransportKind.CAN_FD
        key = channel.transport_specs[0].connection_key
        assert isinstance(key, CANFDTransportKey)
        if self.created:
            assert self.created[-1][1].get_health().state is TransportState.CLOSED
        coordinates = (key.device_index, key.channel_index)
        transport = self.peers.get(coordinates, _empty_transport)()
        self.created.append((coordinates, transport))
        return transport


def hand_peer():
    return ScriptedO30Transport(O30DeviceScript())


def created_channels(source):
    return [coordinates for coordinates, _ in source.created]


def test_channel_zero_success_does_not_query_or_open_channel_one(minicanfd_library):
    lib = minicanfd_library
    lib.CAN_ScanDevice.return_value = 1
    source = MiniPeers({(0, 0): hand_peer, (0, 1): hand_peer})

    report = discover_hands(_FAST, source=source)

    assert report.require_one().channel.name == "minicanfd:0:0"
    assert not report.failures
    assert created_channels(source) == [(0, 0)]
    lib.CAN_GetCHType.assert_called_once_with(0, 0)
    lib.CAN_ScanDevice.assert_called_once_with()


def test_partial_identity_on_channel_zero_does_not_probe_another_hand(
    minicanfd_library,
):
    lib = minicanfd_library
    lib.CAN_ScanDevice.return_value = 1
    source = MiniPeers(
        {
            (0, 0): lambda: ScriptedO30Transport(O30DeviceScript(reported_side=None)),
            (0, 1): hand_peer,
        }
    )

    report = discover_hands(_FAST, source=source)

    assert report.require_one(allow_partial=True).channel.name == "minicanfd:0:0"
    with pytest.raises(IncompleteIdentityError):
        report.require_one()
    assert created_channels(source) == [(0, 0)]
    lib.CAN_GetCHType.assert_called_once_with(0, 0)


def test_channel_one_fallback_checks_type_after_channel_zero_closes(minicanfd_library):
    lib = minicanfd_library
    lib.CAN_ScanDevice.return_value = 1
    source = MiniPeers({(0, 1): hand_peer})

    def channel_type(device, channel):
        if channel == 1:
            assert created_channels(source) == [(0, 0)]
            assert source.created[0][1].get_health().state is TransportState.CLOSED
        return 1

    lib.CAN_GetCHType.side_effect = channel_type

    report = discover_hands(_FAST, source=source)

    candidate = report.require_one()
    assert candidate.channel.name == "minicanfd:0:1"
    assert candidate.transport.connection_key.channel_index == 1
    assert candidate.transport.connection_key.library_path == lib._name
    assert not report.failures
    assert created_channels(source) == [(0, 0), (0, 1)]
    assert [channel.name for channel in report.channels] == [
        "minicanfd:0:0",
        "minicanfd:0:1",
    ]
    assert all(t.get_health().state is TransportState.CLOSED for _, t in source.created)


@pytest.mark.parametrize("zero_type", [0, 2, -1])
def test_unavailable_channel_zero_still_discovers_a_hand_on_channel_one(
    minicanfd_library, zero_type
):
    lib = minicanfd_library
    lib.CAN_ScanDevice.return_value = 1
    lib.CAN_GetCHType.side_effect = lambda device, channel: (
        zero_type if channel == 0 else 1
    )
    source = MiniPeers({(0, 1): hand_peer})

    report = discover_hands(_FAST, source=source)

    assert report.require_one().channel.name == "minicanfd:0:1"
    assert created_channels(source) == [(0, 1)]
    assert [call.args for call in lib.CAN_GetCHType.call_args_list] == [(0, 0), (0, 1)]
    assert all(f.code != "no_hand_found" for f in report.failures)


def test_unavailable_channel_zero_and_unanswered_one_report_no_hand(
    minicanfd_library,
):
    lib = minicanfd_library
    lib.CAN_ScanDevice.return_value = 1
    lib.CAN_GetCHType.side_effect = lambda device, channel: 0 if channel == 0 else 1
    source = MiniPeers({})

    report = discover_hands(_FAST, source=source)

    assert not report.candidates
    assert created_channels(source) == [(0, 1)]
    assert [f.code for f in report.failures] == ["no_hand_found"]
    assert [call.args for call in lib.CAN_GetCHType.call_args_list] == [(0, 0), (0, 1)]
    with pytest.raises(NoHandFoundError):
        report.require_one()


def test_two_non_fd_channels_return_a_type_error(minicanfd_library):
    lib = minicanfd_library
    lib.CAN_ScanDevice.return_value = 1
    lib.CAN_GetCHType.return_value = 0
    source = MiniPeers({})

    report = discover_hands(_FAST, source=source)

    assert not report.candidates
    assert not source.created
    assert report.failures[0].code == "type"
    assert "channels 0 and 1" in report.failures[0].message
    with pytest.raises(NoHandFoundError):
        report.require_one()


@pytest.mark.parametrize("stage", ["library", "busy", "scan", "cleanup"])
def test_fallback_driver_failure_does_not_claim_a_completed_empty_search(
    minicanfd_library, monkeypatch, stage
):
    from linkerbot_transport import canfd

    lib = minicanfd_library
    lib.CAN_ScanDevice.return_value = 1
    enumerate_channels = canfd.enumerate_minicanfd_channels

    def enumerate_with_fallback_failure(*args, **kwargs):
        if kwargs.get("linux_channels") == {0: (1,)}:
            return canfd.MiniCANFDEnumerationResult(
                failures=(
                    canfd.MiniCANFDEnumerationFailure(
                        stage, TransportError(f"fallback {stage} failed")
                    ),
                )
            )
        return enumerate_channels(*args, **kwargs)

    monkeypatch.setattr(
        canfd, "enumerate_minicanfd_channels", enumerate_with_fallback_failure
    )
    source = MiniPeers({})

    report = discover_hands(_FAST, source=source)

    assert created_channels(source) == [(0, 0)]
    assert [f.code for f in report.failures] == [stage]


def test_both_channels_without_a_hand_report_adapter_error(minicanfd_library):
    minicanfd_library.CAN_ScanDevice.return_value = 1
    source = MiniPeers({})

    report = discover_hands(_FAST, source=source)

    assert not report.candidates
    assert created_channels(source) == [(0, 0), (0, 1)]
    assert len(report.failures) == 1
    failure = report.failures[0]
    assert failure.code == "no_hand_found"
    assert failure.backend == "minicanfd"
    assert "minicanfd:0" in failure.message and "0 or 1" in failure.message
    with pytest.raises(NoHandFoundError):
        report.require_one()


def test_one_adapter_failure_preserves_other_adapters_success(minicanfd_library):
    source = MiniPeers({(0, 0): hand_peer, (1, 1): hand_peer})

    report = discover_hands(_FAST, source=source)

    assert [c.channel.name for c in report.verified_candidates] == [
        "minicanfd:0:0",
        "minicanfd:1:1",
    ]
    assert created_channels(source) == [(0, 0), (1, 0), (1, 1), (2, 0), (2, 1)]
    assert [f.code for f in report.failures] == ["no_hand_found"]
    assert "minicanfd:2" in report.failures[0].message


def test_composite_source_fallback_uses_real_transport_recipes(
    minicanfd_library, monkeypatch
):
    from linkerbot_hand.discovery import sources

    minicanfd_library.CAN_ScanDevice.return_value = 1
    opened = []

    def constructor(*args, **kwargs):
        coordinates = (kwargs["device_index"], kwargs["channel_index"])

        def dispatcher_factory(on_error):
            if opened:
                assert opened[-1][1].stop_count == 1
            script = (
                O30DeviceScript()
                if coordinates[1] == 1
                else SimpleNamespace(respond=lambda message: ())
            )
            dispatcher = PeerDispatcher(on_error, script)
            opened.append((coordinates, dispatcher))
            return dispatcher

        return CANFDTransport(*args, **kwargs, dispatcher_factory=dispatcher_factory)

    monkeypatch.setattr(sources, "CANFDTransport", constructor)

    report = discover_hands(
        _FAST,
        source=CompositeDiscoverySource({"minicanfd": MiniCANFDDiscoverySource()}),
    )

    assert report.require_one().transport.connection_key.channel_index == 1
    assert not report.failures
    assert [coordinates for coordinates, _ in opened] == [(0, 0), (0, 1)]
    assert all(dispatcher.stop_count == 1 for _, dispatcher in opened)


@pytest.mark.parametrize("selection", ["layout", "global"])
def test_explicit_channel_selection_does_not_enable_fallback(
    minicanfd_library, selection
):
    lib = minicanfd_library
    lib.CAN_ScanDevice.return_value = 1
    source = MiniPeers({(0, 1): hand_peer})
    config = (
        replace(_FAST, minicanfd=MiniCANFDDiscoveryConfig(linux_channels={0: (0,)}))
        if selection == "layout"
        else replace(_FAST, channels=("minicanfd:0:0",))
    )

    report = discover_hands(config, source=source)

    assert not report.candidates
    assert not report.failures
    assert created_channels(source) == [(0, 0)]
    lib.CAN_GetCHType.assert_called_once_with(0, 0)


@pytest.mark.parametrize("channel_type", [0, 2, -1])
def test_invalid_channel_one_is_not_opened(minicanfd_library, channel_type):
    lib = minicanfd_library
    lib.CAN_ScanDevice.return_value = 1
    lib.CAN_GetCHType.side_effect = lambda device, channel: (
        1 if channel == 0 else channel_type
    )
    source = MiniPeers({(0, 1): hand_peer})

    report = discover_hands(_FAST, source=source)

    assert not report.candidates
    assert created_channels(source) == [(0, 0)]
    assert report.failures[-1].code == "no_hand_found"
    if channel_type == -1:
        assert report.failures[0].code == "type"


def test_legacy_library_reports_unsupported_channel_one(minicanfd_library):
    lib = minicanfd_library
    lib.CAN_ScanDevice.return_value = 1
    del lib.LibCANbus_Init, lib.LibCANbus_Exit, lib.CAN_GetCHType
    lib.CAN_GetDevType = Mock(return_value=1)
    source = MiniPeers({})

    report = discover_hands(_FAST, source=source)

    assert created_channels(source) == [(0, 0)]
    assert [f.code for f in report.failures] == ["layout", "no_hand_found"]
    assert "only confirmed channel 0" in report.failures[0].message
    lib.CAN_GetDevType.assert_called_once_with(0)


def test_windows_does_not_apply_linux_channel_fallback(minicanfd_library, monkeypatch):
    from linkerbot_transport.canfd import ctypes_backend

    lib = minicanfd_library
    lib.CAN_ScanDevice.return_value = 1
    lib.CAN_GetDevType = Mock(return_value=1)
    monkeypatch.setattr(ctypes_backend.platform, "system", lambda: "Windows")
    source = MiniPeers({})

    report = discover_hands(_FAST, source=source)

    assert not report.candidates
    assert not report.failures
    assert created_channels(source) == [(0, 0)]
    lib.CAN_GetDevType.assert_called_once_with(0)


@pytest.mark.parametrize("failed_channel", [0, 1])
def test_open_failure_preserves_diagnostics_and_cleans_before_next_channel(
    minicanfd_library, failed_channel
):
    minicanfd_library.CAN_ScanDevice.return_value = 1
    transport = _empty_transport()

    def fail_open():
        raise TransportError(f"channel {failed_channel} open failed")

    transport.open = fail_open
    peers = {(0, 1): hand_peer, (0, failed_channel): lambda: transport}
    source = MiniPeers(peers)

    report = discover_hands(_FAST, source=source)

    assert created_channels(source) == [(0, 0), (0, 1)]
    assert transport.get_health().state is TransportState.CLOSED
    assert f"channel {failed_channel} open failed" in report.failures[0].message
    if failed_channel == 0:
        assert report.require_one().channel.name == "minicanfd:0:1"
        assert all(f.code != "no_hand_found" for f in report.failures)
    else:
        assert not report.candidates
        assert report.failures[-1].code == "no_hand_found"


def test_failed_channel_zero_cleanup_prevents_fallback(minicanfd_library):
    lib = minicanfd_library
    lib.CAN_ScanDevice.return_value = 1
    transport = _empty_transport()
    close = transport.close

    def fail_close():
        raise TransportError("channel 0 close failed")

    transport.close = fail_close
    source = MiniPeers({(0, 0): lambda: transport, (0, 1): hand_peer})
    try:
        report = discover_hands(_FAST, source=source)
        assert created_channels(source) == [(0, 0)]
        assert report.failures[-1].cleanup is not None
        assert report.failures[-1].operation == "close_can_fd_probe_transport"
        lib.CAN_GetCHType.assert_called_once_with(0, 0)
    finally:
        transport.close = close
        close()


def test_expired_scan_budget_prevents_channel_one_fallback(minicanfd_library):
    from linkerbot_hand.discovery import _budget

    lib = minicanfd_library
    lib.CAN_ScanDevice.return_value = 1
    transport = _empty_transport()
    close = transport.close

    def close_at_deadline():
        close()
        _budget.deadline.set(0)

    transport.close = close_at_deadline
    source = MiniPeers({(0, 0): lambda: transport, (0, 1): hand_peer})

    try:
        report = discover_hands(_FAST, source=source)
    finally:
        transport.close = close

    assert created_channels(source) == [(0, 0)]
    assert any(f.code == "scan_timeout" for f in report.failures)
    lib.CAN_GetCHType.assert_called_once_with(0, 0)
