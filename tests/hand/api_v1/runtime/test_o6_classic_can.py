from contextlib import closing
from dataclasses import replace

import can
import pytest

from linkerbot_hand import (
    DeviceConfig,
    HandDevice,
    HandSide,
    ProtocolKind,
    TimeoutError,
    TransportKind,
    discover_hands,
)
from linkerbot_hand.api.commands import CommandStage
from linkerbot_transport import CANRouteKey
from linkerbot_transport.canfd import CANFDMessage
from linkerbot_transport.testing import FakeTransport
from tests.hand.api_v1.discovery.test_discovery import _FAST, _classic_channel, _Source
from tests.hand.api_v1.runtime.hop_six_fake import HOPSixScript


class ClassicSixTransport(FakeTransport):
    def __init__(self, script):
        super().__init__(
            route_key_of=lambda message: CANRouteKey(
                message.arbitration_id, message.is_extended_id
            )
        )
        self.script = script
        self.requests = []

    def send(self, message):
        assert isinstance(message, can.Message)
        assert not message.is_fd and not message.bitrate_switch
        assert len(message.data) <= 8
        receipt = super().send(message)
        if receipt.exception() is not None:
            return receipt
        self.requests.append(message)
        for reply in self.script.respond(
            CANFDMessage(
                arbitration_id=message.arbitration_id,
                data=bytes(message.data),
                is_extended_id=False,
                frame_type=4,
            )
        ):
            control, start, size = reply.data[:3]
            payload = reply.data[3 : 3 + size]
            for offset in range(0, len(payload), 5):
                chunk = payload[offset : offset + 5]
                self.emit(
                    can.Message(
                        arbitration_id=reply.arbitration_id,
                        data=bytes((control, start + offset, len(chunk))) + chunk,
                        is_extended_id=False,
                    )
                )
        return receipt


def make_hand(side=HandSide.LEFT, request_id=1, sensor_variant="NO_SENSOR"):
    script = HOPSixScript("O6", side, request_id, sensor_variant)
    script.configuration[6] = 1
    transport = ClassicSixTransport(script)
    hand = HandDevice(
        model="O6",
        side=side,
        protocol="hop",
        transport=transport,
        config=DeviceConfig(hop_transport=TransportKind.CAN, hop_request_id=request_id),
    )
    return script, transport, hand


@pytest.mark.parametrize("side", [HandSide.LEFT, HandSide.RIGHT])
@pytest.mark.parametrize("request_id", [1, 0x123])
def test_classic_identity_control_and_reopen(side, request_id):
    script, transport, hand = make_hand(side, request_id)
    with closing(hand.open()):
        assert hand.get_device_info(timeout=1).reported_side is side
        assert hand.get_capabilities().protocol is ProtocolKind.HOP
        assert hand.get_capabilities().transport.kind is TransportKind.CAN
        assert hand.get_capabilities().transport.data_bitrate is None
        before = script.targets[1]
        command = hand.set_angles([100] * 6)
        command.wait(1)
        assert command.stage is CommandStage.ACKNOWLEDGED
        assert hand.get_angles(timeout=1).raw == (100,) * 6
        assert script.targets[1][6:10] == before[6:10]
        assert hand.get_can_configuration().interface_type == 1
        assert all(
            message.arbitration_id == request_id for message in transport.requests
        )
    with closing(hand.open()):
        assert hand.get_angles(timeout=1).raw == (100,) * 6


def test_classic_tactile_long_reads():
    _, transport, hand = make_hand(sensor_variant="TSSP_JZG")
    with closing(hand.open()):
        sample = hand.get_normal_force_resultant(timeout=2)
        assert sample.raw == tuple(1000 + index for index in range(1, 6))
        assert all(message.data[2] <= 5 for message in transport.requests)


def test_incomplete_second_range_is_not_acknowledged():
    script, transport, hand = make_hand()
    with closing(hand.open()):
        previous = hand.get_angles(timeout=1)
        script.drop_range = (1, 10)
        command = hand.set_angles([100] * 6)
        command.wait(1)
        assert command.stage is not CommandStage.ACKNOWLEDGED
        assert hand.get_snapshot().angles.raw == previous.raw
        writes = [message for message in transport.requests if len(message.data) > 3]
        assert len(writes) == 2
        with pytest.raises(TimeoutError):
            hand.get_angles(timeout=0.2)


@pytest.mark.parametrize("side", [HandSide.LEFT, HandSide.RIGHT])
def test_classic_discovery_preserves_mode_and_endpoint(side):
    script = HOPSixScript("O6", side, 0x123)
    source = _Source(
        _classic_channel(),
        {TransportKind.CAN: lambda: ClassicSixTransport(script)},
    )
    report = discover_hands(replace(_FAST, hop_request_ids=(0x123,)), source=source)
    candidate = report.require_one()
    assert candidate.model == "O6" and candidate.side is side
    assert candidate.wire_protocol is TransportKind.CAN
    with closing(candidate.create_hand_device().open()) as hand:
        assert hand.get_angles(timeout=1).raw == (10, 5, 11, 12, 13, 14)
