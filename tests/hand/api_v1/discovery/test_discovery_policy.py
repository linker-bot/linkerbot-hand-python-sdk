"""Wire-mode fallback must follow identity evidence and resource ownership."""

import errno
from dataclasses import replace

import can
import pytest

from linkerbot_hand import HandSide, TransportKind, discover_hands
from linkerbot_transport import TransportState
from linkerbot_transport.testing import FakeTransport
from tests.hand.api_v1.runtime.classic_can_fake import (
    ScriptedClassicCANTransport,
    make_script,
)
from tests.hand.api_v1.runtime.o30_canfd_fake import (
    O30DeviceScript,
    ScriptedO30Transport,
)

from .test_classic_broadcast import _BroadcastTransport
from .test_discovery import (
    _FAST,
    _empty_transport,
    _fd_channel,
    _ProductOnlyClassicTransport,
    _route_key,
    _Source,
)


def test_partial_classic_identity_does_not_block_fd_identity():
    classic = _ProductOnlyClassicTransport(make_script("O6", HandSide.LEFT))
    flexible = ScriptedO30Transport(O30DeviceScript())
    source = _Source(
        _fd_channel(),
        {
            TransportKind.CAN: lambda: classic,
            TransportKind.CAN_FD: lambda: flexible,
        },
    )
    report = discover_hands(_FAST, source=source)
    assert [c.model for c in report.verified_candidates] == ["O30"]
    assert report.partial_candidates
    assert classic.get_health().state is TransportState.CLOSED


def test_fd_first_policy_closes_fd_before_classic_fallback():
    flexible = _empty_transport()
    classic = ScriptedClassicCANTransport(make_script("L6", HandSide.LEFT))

    def create_classic():
        assert flexible.get_health().state is TransportState.CLOSED
        return classic

    channel = replace(
        _fd_channel(), probe_protocols=(TransportKind.CAN_FD, TransportKind.CAN)
    )
    source = _Source(
        channel,
        {TransportKind.CAN: create_classic, TransportKind.CAN_FD: lambda: flexible},
    )
    report = discover_hands(_FAST, source=source)
    assert report.require_one().model == "L6"
    assert [wire for wire, _ in source.created] == [
        TransportKind.CAN_FD,
        TransportKind.CAN,
    ]


@pytest.mark.parametrize("fatal", [False, True])
def test_classic_send_error_only_suppresses_fd_for_a_dead_link(fatal):
    classic = _empty_transport()
    open_classic = classic.open

    def open_with_error():
        open_classic()
        classic.current_dispatcher.send_error = (
            OSError(errno.ENETDOWN, "link down")
            if fatal
            else ValueError("classic format rejected")
        )

    classic.open = open_with_error
    source = _Source(
        _fd_channel(),
        {
            TransportKind.CAN: lambda: classic,
            TransportKind.CAN_FD: lambda: ScriptedO30Transport(O30DeviceScript()),
        },
    )
    report = discover_hands(_FAST, source=source)
    assert len(source.created) == (1 if fatal else 2)
    assert [c.model for c in report.verified_candidates] == ([] if fatal else ["O30"])


@pytest.mark.parametrize("fail_during", ["create", "open"])
def test_fd_initialization_failure_allows_explicit_classic_fallback(fail_during):
    class UnsupportedFD(FakeTransport):
        def __init__(self):
            super().__init__(route_key_of=_route_key)

        def open(self):
            raise can.CanInitializationError("CAN FD mode is unavailable")

    classic = _BroadcastTransport("O6")
    channel = replace(
        _fd_channel(),
        probe_protocols=(TransportKind.CAN_FD, TransportKind.CAN),
    )

    def create_fd():
        if fail_during == "create":
            raise can.CanInitializationError("CAN FD mode is unavailable")
        return UnsupportedFD()

    source = _Source(
        channel,
        {
            TransportKind.CAN_FD: create_fd,
            TransportKind.CAN: lambda: classic,
        },
    )

    report = discover_hands(_FAST, source=source)

    assert [candidate.model for candidate in report.verified_candidates] == ["O6"]
    expected = (
        [TransportKind.CAN]
        if fail_during == "create"
        else [TransportKind.CAN_FD, TransportKind.CAN]
    )
    assert [wire for wire, _ in source.created] == expected
