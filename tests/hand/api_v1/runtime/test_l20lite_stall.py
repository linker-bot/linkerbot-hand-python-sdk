"""Grouped LHT10 stall writes confirm all ten joints without invented reads."""

from contextlib import closing

import can
import pytest

from linkerbot_hand import (
    CommandFamily,
    CommandSemantic,
    CommandStage,
    DataOrigin,
    HandDevice,
    HandSide,
    ProtocolError,
    ReadMode,
    UnsupportedOperationError,
    ValidationError,
    get_declared_capabilities,
)
from linkerbot_hand.protocols.l20lite.transactions import L20LiteProtocolBackend

from .classic_can_fake import (
    ClassicCANDeviceScript,
    ScriptedClassicCANTransport,
)

SETTINGS = (
    ("stall_thresholds", 0xC5),
    ("stall_detection_times", 0xC6),
    ("stall_hold_torques", 0xC7),
)
VALUES = (0, 10, 20, 30, 40, 50, 60, 70, 80, 255)


class StallScript(ClassicCANDeviceScript):
    def respond(self, request):
        data = bytes(request.data)
        if data and data[0] in (0xC5, 0xC6, 0xC7):
            self.sent_payloads.append(data)
            assert len(data) == 7 and data[1] in (1, 2)
            if data[0] in self.dropped_command_responses:
                return ()
            return (self._frame(data),)
        return super().respond(request)


@pytest.mark.parametrize("side", [HandSide.LEFT, HandSide.RIGHT])
def test_configurations_are_fifo_acknowledged_and_cache_only(side):
    script = StallScript(
        "L20Lite",
        side,
        f"LHT10-07-98-{'L' if side is HandSide.LEFT else 'R'}-A-1-C",
        (4, 6, 10),
    )
    hand = HandDevice(
        model="L20Lite", side=side, transport=ScriptedClassicCANTransport(script)
    )
    with closing(hand.open()):
        caps = hand.get_capabilities()
        assert caps.stall_protection.hold_current is None
        assert caps.stall_protection.hold_torque is not None
        assert not caps.stall_protection.read_supported
        for name, _ in SETTINGS:
            with pytest.raises(UnsupportedOperationError):
                getattr(hand, f"get_{name}")(timeout=1)
        count = len(script.sent_payloads)
        handles = [getattr(hand, f"set_{name}")(VALUES) for name, _ in SETTINGS]
        for handle in handles:
            assert handle.family is CommandFamily.STALL_CONFIGURATION
            assert handle.wait(1) is CommandStage.ACKNOWLEDGED
        assert script.sent_payloads[count:] == [
            bytes((opcode, group, *VALUES[offset : offset + 5]))
            for _, opcode in SETTINGS
            for group, offset in ((1, 0), (2, 5))
        ]
        count = len(script.sent_payloads)
        for name, _ in SETTINGS:
            sample = getattr(hand, f"get_{name}")(timeout=1)
            assert sample.raw == VALUES
            assert sample.meta.origin is DataOrigin.DEVICE_CONFIG_RESPONSE
            assert getattr(hand.get_snapshot(), name).raw == VALUES
            channel = next(c for c in caps.telemetry if c.semantic.value == name)
            assert channel.read_mode is ReadMode.COMMAND_RESPONSE
            assert not channel.device_queried and not channel.device_measured
        assert len(script.sent_payloads) == count
        with pytest.raises(UnsupportedOperationError):
            hand.set_stall_hold_currents(VALUES)
        for invalid in [
            VALUES[:-1],
            VALUES + (0,),
            (-1,) * 10,
            (256,) * 10,
            (True,) * 10,
        ]:
            with pytest.raises(ValidationError):
                hand.set_stall_thresholds(invalid)
        assert len(script.sent_payloads) == count


def transaction(name):
    caps = get_declared_capabilities("L20Lite", side="left")
    return L20LiteProtocolBackend(HandSide.LEFT).command_transaction(
        CommandSemantic(name), VALUES, caps
    )


def frame(data, *, identifier=0x28):
    return can.Message(arbitration_id=identifier, is_extended_id=False, data=data)


@pytest.mark.parametrize("name,opcode", SETTINGS)
def test_exact_groups_can_arrive_reordered_but_never_publish_partial(name, opcode):
    tx = transaction(name)
    assert tx.request_interval_s == 0.001
    group1, group2 = (bytes(m.data) for m in tx.request_messages)
    assert not tx.consume(frame(group1, identifier=0x27)).matched
    assert not tx.consume(frame(bytes((0x99,)))).matched
    assert not tx.consume(frame(group2)).complete
    assert not tx.consume(frame(group2)).complete
    complete = tx.consume(frame(group1))
    assert complete.complete and complete.acknowledged
    assert complete.result.raw == VALUES


@pytest.mark.parametrize("name,opcode", SETTINGS)
@pytest.mark.parametrize("fault", ["short", "long", "bad_group", "wrong_value"])
def test_bad_echo_is_rejected(name, opcode, fault):
    tx = transaction(name)
    data = bytes(tx.request_messages[0].data)
    data = {
        "short": data[:-1],
        "long": data + b"\x00",
        "bad_group": bytes((opcode, 3)) + data[2:],
        "wrong_value": data[:-1] + b"\x01",
    }[fault]
    with pytest.raises(ProtocolError):
        tx.consume(frame(data))


def test_missing_echo_does_not_create_confirmed_configuration():
    script = StallScript("L20Lite", HandSide.LEFT, "LHT10-07-98-L-A-1-C", (4, 6, 10))
    script.dropped_command_responses.add(0xC5)
    hand = HandDevice(
        model="L20Lite", side="left", transport=ScriptedClassicCANTransport(script)
    )
    with closing(hand.open()):
        handle = hand.set_stall_thresholds(VALUES)
        assert handle.wait(1) is CommandStage.FAILED
        assert not hand.get_snapshot().stall_thresholds.meta.valid
