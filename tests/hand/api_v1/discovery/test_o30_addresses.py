"""Configured O30 addresses must survive discovery, construction and open."""

from dataclasses import replace

import pytest

from linkerbot_hand import (
    DeviceConfig,
    DiscoveryConfig,
    HandDevice,
    HandSide,
    TransportKind,
    ValidationError,
    discover_hands,
)
from linkerbot_hand.protocols.o30 import O30ProtocolBackend
from linkerbot_hand.protocols.o30.framing import HOPAddressing
from tests.hand.api_v1.runtime.o30_canfd_fake import (
    O30DeviceScript,
    ScriptedO30Transport,
)

from .test_discovery import _FAST, _empty_transport, _fd_channel, _Source


@pytest.mark.parametrize(
    "request_id", [-1, True, 0x3FF, 0x400, 0x401, 0x7FF, 0x800, "1"]
)
def test_invalid_unicast_ids_cannot_be_constructed_or_probed(request_id):
    with pytest.raises(ValidationError):
        O30ProtocolBackend(HandSide.LEFT, request_id=request_id)
    with pytest.raises(ValidationError):
        DiscoveryConfig(o30_request_ids=(request_id,))
    with pytest.raises(ValidationError):
        DeviceConfig(o30_request_id=request_id)


@pytest.mark.parametrize("ids", [(), (1, 1), [1]])
def test_discovery_rejects_empty_duplicate_and_mutable_address_sets(ids):
    with pytest.raises(ValidationError):
        DiscoveryConfig(o30_request_ids=ids)


def test_hop_address_rejects_same_request_response_and_wrong_response():
    for pair in [(0x401, 0x401), (0x123, 0x524), (0x7FF, 0x7FF)]:
        with pytest.raises(ValidationError):
            HOPAddressing(*pair)


def test_public_constructor_uses_o30_address_and_rejects_other_models():
    script = O30DeviceScript(request_id=0x123, response_id=0x523)
    transport = ScriptedO30Transport(script)
    config = DeviceConfig(o30_request_id=0x123)
    with pytest.raises(ValidationError, match="only valid for O30"):
        HandDevice(model="O6", side="right", transport=transport, config=config)
    assert not script.sent_messages
    hand = HandDevice(model="O30", side="right", transport=transport, config=config)
    assert not script.sent_messages
    hand.open()
    try:
        assert hand.get_angles(timeout=1).raw == tuple(range(20))
    finally:
        hand.close()


@pytest.mark.parametrize("request_id", [0, 1, 0x123, 0x3FE])
@pytest.mark.parametrize("batch", [False, True])
def test_discovery_preserves_endpoint_even_when_side_differs_from_default(
    request_id, batch
):
    script = O30DeviceScript(
        reported_side=HandSide.LEFT,
        request_id=request_id,
        response_id=request_id | 0x400,
    )
    source = _Source(
        _fd_channel(data_bitrate=2_000_000),
        {
            TransportKind.CAN: _empty_transport,
            TransportKind.CAN_FD: lambda: ScriptedO30Transport(script),
        },
    )
    report = discover_hands(
        replace(_FAST, o30_request_ids=(request_id,)), source=source
    )
    candidate = report.require_one()
    assert candidate.side is HandSide.LEFT
    assert candidate.address.request_id == request_id
    with pytest.raises(ValidationError, match="disagrees"):
        candidate.create_hand_device(
            config=DeviceConfig(o30_request_id=(request_id + 1) % 0x3FF)
        )
    # Keep the public candidate factory while substituting its transport creator.
    from unittest.mock import patch

    with patch.object(
        type(candidate.transport),
        "create_transport",
        return_value=ScriptedO30Transport(script),
    ):
        hand = (
            report.create_hand_devices()["o30l"]
            if batch
            else candidate.create_hand_device()
        )
    hand.open()
    try:
        assert hand.get_angles(timeout=1).raw == tuple(range(20))
        assert hand.get_snapshot().device_info.side is HandSide.LEFT
    finally:
        hand.close()
