"""Product-selected HOP discovery restores protocol, addresses, BRS and identity."""

from contextlib import closing
from dataclasses import replace

import pytest

from linkerbot_hand import (
    DeviceConfig,
    DiscoveryConfig,
    DiscoveryStatus,
    HandSide,
    IdentityMismatchError,
    ProtocolKind,
    TransportKind,
    ValidationError,
    discover_hands,
)
from tests.hand.api_v1.runtime.hop_six_fake import HOPSixScript
from tests.hand.api_v1.runtime.o30_canfd_fake import ScriptedO30Transport

from .test_discovery import _FAST, _empty_transport, _fd_channel, _Source


@pytest.mark.parametrize("model", ["L6", "O6"])
@pytest.mark.parametrize("side", [HandSide.LEFT, HandSide.RIGHT])
@pytest.mark.parametrize(
    "request_id,brs", [(1, False), (0x123, True), (0, False), (0x3FE, False)]
)
def test_discovery_preserves_hop_protocol_and_endpoint(model, side, request_id, brs):
    script = HOPSixScript(model, side, request_id)
    source = _Source(
        _fd_channel(data_bitrate=2_000_000),
        {
            TransportKind.CAN: _empty_transport,
            TransportKind.CAN_FD: lambda: ScriptedO30Transport(script),
        },
    )
    report = discover_hands(
        config=replace(_FAST, hop_request_ids=(request_id,), hop_brs=brs),
        source=source,
    )
    candidate = report.require_one()
    assert candidate.model == model
    assert candidate.side is side
    assert candidate.capabilities.protocol is ProtocolKind.HOP
    assert candidate.hop_brs is brs
    assert not script.command_messages
    from unittest.mock import patch

    with patch.object(
        type(candidate.transport),
        "create_transport",
        return_value=ScriptedO30Transport(script),
    ):
        named = report.create_hand_devices()
    hand = named[f"{model.lower()}{'l' if side is HandSide.LEFT else 'r'}"]
    count = len(script.sent_messages)
    assert hand.get_capabilities().protocol is ProtocolKind.HOP
    assert len(script.sent_messages) == count
    with closing(hand.open()):
        assert hand.get_angles(timeout=1).raw == (10, 5, 11, 12, 13, 14)
        assert all(m.arbitration_id == request_id for m in script.sent_messages)
        assert all(
            m.frame_type == (0x0C if brs else 0x04) for m in script.sent_messages
        )
    script.device_uid = "DIFFERENT-DEVICE"
    with pytest.raises(IdentityMismatchError):
        hand.open()
    assert not hand.get_snapshot().angles.meta.valid


@pytest.mark.parametrize("model", ["L6", "O6"])
def test_missing_reported_side_is_partial_not_guessed_from_id(model):
    script = HOPSixScript(model, side=None)
    source = _Source(
        _fd_channel(),
        {
            TransportKind.CAN: _empty_transport,
            TransportKind.CAN_FD: lambda: ScriptedO30Transport(script),
        },
    )
    report = discover_hands(config=replace(_FAST, hop_request_ids=(1,)), source=source)
    assert report.create_hand_devices() == {}
    candidate = report.require_one(allow_partial=True)
    assert candidate.status is DiscoveryStatus.PARTIAL
    assert candidate.side is None
    assert candidate.problems[0].code == "missing_side"
    # Explicit side remains the documented manual override for missing-side candidates.
    from unittest.mock import patch

    with patch.object(
        type(candidate.transport),
        "create_transport",
        return_value=ScriptedO30Transport(script),
    ):
        hand = candidate.create_hand_device(side="left")
    with closing(hand.open()):
        assert hand.get_device_info(timeout=1).reported_side is None


@pytest.mark.parametrize("ids", [(), (1, 1), (0x3FF,), (True,), [1]])
def test_invalid_hop_discovery_options(ids):
    with pytest.raises(ValidationError):
        DiscoveryConfig(hop_request_ids=ids)


def test_hop_options_reject_conflicting_discovered_id():
    script = HOPSixScript()
    source = _Source(
        _fd_channel(),
        {
            TransportKind.CAN: _empty_transport,
            TransportKind.CAN_FD: lambda: ScriptedO30Transport(script),
        },
    )
    candidate = discover_hands(
        config=replace(_FAST, hop_request_ids=(1,)), source=source
    ).require_one()
    with pytest.raises(ValidationError, match="disagrees"):
        candidate.create_hand_device(config=DeviceConfig(hop_request_id=0x123))


def test_configured_nominal_bitrate_is_discoverable_without_retuning():
    from unittest.mock import patch

    script = HOPSixScript()
    script.configuration[8] = 3
    source = _Source(
        replace(_fd_channel(), nominal_bitrate=500_000),
        {
            TransportKind.CAN: _empty_transport,
            TransportKind.CAN_FD: lambda: ScriptedO30Transport(script),
        },
    )
    report = discover_hands(replace(_FAST, hop_request_ids=(1,)), source=source)
    candidate = report.require_one()
    assert candidate.transport.nominal_bitrate == 500_000
    with patch.object(
        type(candidate.transport),
        "create_transport",
        return_value=ScriptedO30Transport(script),
    ):
        hand = candidate.create_hand_device()
    with closing(hand.open()):
        assert hand.get_can_configuration().nominal_bitrate == 500_000


def test_unknown_hop_product_is_partial_with_unknown_model():
    script = HOPSixScript()
    script.product_model = "UNKNOWN"
    source = _Source(
        _fd_channel(),
        {
            TransportKind.CAN: _empty_transport,
            TransportKind.CAN_FD: lambda: ScriptedO30Transport(script),
        },
    )
    report = discover_hands(replace(_FAST, hop_request_ids=(1,)), source=source)
    candidate = report.require_one(allow_partial=True)
    assert candidate.model is None
    assert candidate.status is DiscoveryStatus.PARTIAL
    assert candidate.problems[0].code == "unsupported_product"
    assert report.create_hand_devices() == {}
