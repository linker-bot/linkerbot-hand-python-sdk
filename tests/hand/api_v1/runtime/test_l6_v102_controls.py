"""L6 V1.0.2 controls through the production codec, runtime and public API."""

import time
from contextlib import closing
from dataclasses import replace

import pytest

from linkerbot_hand import (
    AckSupport,
    CommandFamily,
    CommandSemantic,
    CommandStage,
    DataOrigin,
    HandDevice,
    HandSide,
    PollingPlan,
    PositionKdSample,
    PositionKpSample,
    ProtocolError,
    TelemetrySemantic,
    TimeoutError,
    TorqueKdSample,
    TorqueSample,
    UnsupportedFirmwareError,
    ValidationError,
    get_declared_capabilities,
)
from linkerbot_hand.protocols.l6 import L6ProtocolBackend
from linkerbot_hand.runtime.command_scheduler import CommandIngress

from .classic_can_fake import ScriptedClassicCANTransport, make_script


class RecordingTransport(ScriptedClassicCANTransport):
    def __init__(self, script):
        super().__init__(script)
        self.frames = []

    def send(self, message):
        self.frames.append((time.monotonic(), bytes(message.data)))
        return super().send(message)


def make_hand(side=HandSide.LEFT):
    script = make_script("L6", side)
    transport = RecordingTransport(script)
    hand = HandDevice(model="L6", side=side, transport=transport)
    return hand, script, transport


CASES = (
    ("torques", "torques", 0x03, 1, TorqueSample),
    ("position_kp", "position_kp", 0x06, 49, PositionKpSample),
    ("position_kd", "position_kd", 0x07, 20, PositionKdSample),
    ("torque_kd", "torque_kd", 0x08, 2, TorqueKdSample),
)


@pytest.mark.parametrize("side", [HandSide.LEFT, HandSide.RIGHT])
@pytest.mark.parametrize("command,channel,opcode,value,sample_type", CASES)
def test_raw_writes_preserve_prior_value_responses_and_query_fresh_settings(
    side,
    command,
    channel,
    opcode,
    value,
    sample_type,
):
    hand, script, _ = make_hand(side)
    original = script.readings[opcode]
    with closing(hand.open()):
        initial = getattr(hand.get_snapshot(), channel)
        assert isinstance(initial, sample_type)
        assert not initial.meta.valid
        values = (value,) * 6
        handle = getattr(hand, f"set_{command}")(values)
        assert handle.wait(1) is CommandStage.ACKNOWLEDGED
        # V2.4.5 replies with the pre-write setting. Do not fabricate the target
        # as an observed sample, or reject a valid receipt because it differs.
        cached = getattr(hand.get_snapshot(), channel)
        assert cached.raw == original
        assert cached.meta.origin is DataOrigin.DEVICE_CONFIG_RESPONSE
        assert script.command_payloads[-1] == bytes((opcode, *values))
        assert script.read_counts[opcode] == 0
        queried = getattr(hand, f"get_{channel}")(timeout=1)
        assert isinstance(queried, sample_type)
        assert queried.raw == values
        assert script.read_counts[opcode] == 1
        assert queried.meta.sequence > cached.meta.sequence
        assert getattr(hand.get_snapshot(), channel).raw == values
        if command == "torques":
            assert hand.get_currents(timeout=1).raw == script.readings[0x36]
            assert script.read_counts[0x36] == 1
            assert hand.get_current_limits(timeout=1).raw == script.readings[0x02]
            snapshot = hand.get_snapshot()
            assert snapshot.torques.raw == values
            assert snapshot.currents.raw == script.readings[0x36]
            assert snapshot.current_limits.raw == script.readings[0x02]


@pytest.mark.parametrize("command,channel,opcode,value,sample_type", CASES)
def test_optional_new_queries_degrade_individually_on_old_firmware(
    command,
    channel,
    opcode,
    value,
    sample_type,
):
    hand, script, _ = make_hand()
    script.firmware = (2, 3, 3)
    script.unsupported_queries.add(opcode)
    with closing(hand.open()):
        getter = getattr(hand, f"get_{channel}")
        for _ in range(2):
            with pytest.raises(TimeoutError):
                getter(timeout=1)
        with pytest.raises(UnsupportedFirmwareError):
            getter(timeout=1)
        with pytest.raises(UnsupportedFirmwareError):
            getter(timeout=1)
        assert script.read_counts[opcode] == 3
        assert hand.get_health().connected
        assert hand.get_angles(timeout=1).meta.valid
        assert hand.get_capabilities().firmware_unsupported_telemetry == (
            TelemetrySemantic(channel),
        )
        if command == "torques":
            assert hand.get_currents(timeout=1).meta.valid
            assert hand.get_current_limits(timeout=1).meta.valid


def test_torque_current_and_limit_capabilities_keep_distinct_meanings():
    caps = get_declared_capabilities("L6", side="left")
    torques = caps.telemetry_channel(TelemetrySemantic.TORQUES)
    currents = caps.telemetry_channel(TelemetrySemantic.CURRENTS)
    limits = caps.telemetry_channel(TelemetrySemantic.CURRENT_LIMITS)
    assert torques.device_queried and not torques.device_measured
    assert torques.raw_unit == "count"
    assert "measured physical torque is unverified" in torques.raw_scale_note
    assert currents.device_queried and currents.device_measured
    assert "1400/255 mA/count" in currents.raw_scale_note
    assert limits.device_queried and not limits.device_measured
    assert not caps.command(CommandSemantic.TORQUES).returns_current_state
    o30 = get_declared_capabilities("O30", side="right")
    assert o30.telemetry_channel(TelemetrySemantic.TORQUES).device_measured
    assert o30.command(CommandSemantic.TORQUES) is None


def test_torque_and_current_polling_do_not_write_modes_or_query_limits():
    hand, script, _ = make_hand()
    script.readings[0x03] = (1, 0, 2, 0, 3, 0)
    script.readings[0x36] = (8, 7, 6, 5, 4, 3)
    with closing(hand.open()):
        limit_queries = script.read_counts[0x02]
        hand.start_polling(PollingPlan(torques_hz=10, current_hz=20))
        deadline = time.monotonic() + 2
        while script.read_counts[0x03] < 2 or script.read_counts[0x36] < 3:
            assert time.monotonic() < deadline
            time.sleep(0.005)
        hand.stop_polling()
        snapshot = hand.get_snapshot()
        assert isinstance(snapshot.torques, TorqueSample)
        assert snapshot.torques.raw == script.readings[0x03]
        assert snapshot.currents.raw == script.readings[0x36]
        assert snapshot.torques.meta.origin is DataOrigin.DEVICE_CONFIG_RESPONSE
        assert snapshot.currents.meta.origin is DataOrigin.DEVICE_RESPONSE
        assert snapshot.joints[0].torque_raw == 1
        assert snapshot.joints[0].current_raw == 8
        assert script.read_counts[0x02] == limit_queries
        assert not script.command_payloads


@pytest.mark.parametrize(
    "values", [(201,) * 6, (-1,) * 6, (True,) * 6, (50.0,) * 6, (50,) * 5]
)
def test_kp_declared_physical_range_and_raw_integer_contract(values):
    hand, script, _ = make_hand()
    with closing(hand.open()):
        before = tuple(script.sent_payloads)
        with pytest.raises(ValidationError):
            hand.set_position_kp(values)
        assert tuple(script.sent_payloads) == before


def test_gain_units_and_kd_recommendation_do_not_apply_hidden_conversion():
    caps = get_declared_capabilities("L6", side="left")
    kp = caps.command(CommandSemantic.POSITION_KP)
    assert kp.range_for(0).maximum == 200
    assert "raw * 10" in kp.raw_scale_note
    assert "500" in kp.raw_scale_note
    kd = caps.command(CommandSemantic.POSITION_KD)
    assert kd.range_for(0).contains(41)  # 40 is a recommendation, not a clamp.
    assert "21" in kd.raw_scale_note and "40" in kd.raw_scale_note


def test_mode_writes_are_fifo_and_zero_transition_is_not_superseded():
    hand, script, _ = make_hand()
    with closing(hand.open()):
        handles = [hand.set_torques((value,) * 6) for value in (1, 0, 2)]
        assert all(handle.family is CommandFamily.TORQUE_TARGET for handle in handles)
        assert all(handle.wait(2) is CommandStage.ACKNOWLEDGED for handle in handles)
        assert [data for data in script.command_payloads if data[0] == 0x03] == [
            bytes((0x03, *((value,) * 6))) for value in (1, 0, 2)
        ]


def test_angle_priority_and_replacement_cannot_skip_a_queued_mode_change():
    ingress = CommandIngress(
        discrete_capacity=4, monotonic=time.monotonic, wake=lambda: None
    )
    ingress.activate(generation=1)
    mode = ingress.submit(
        CommandSemantic.TORQUES, (0,) * 6, ack_support=AckSupport.SUPPORTED
    )
    angle = ingress.submit(
        CommandSemantic.ANGLES, (100,) * 6, ack_support=AckSupport.UNSUPPORTED
    )
    assert ingress.take_latest(CommandFamily.ANGLE_TARGET) is None
    assert ingress.take_next().handle is mode
    assert ingress.take_latest(CommandFamily.ANGLE_TARGET).handle is angle


def test_gain_query_retains_out_of_range_device_evidence():
    hand, script, _ = make_hand()
    script.readings[0x06] = (255,) * 6
    with closing(hand.open()):
        sample = hand.get_position_kp(timeout=1)
        assert sample.raw == (255,) * 6
        assert sample.meta.valid and not sample.meta.within_declared_range


@pytest.mark.parametrize(
    "values,interval", [((1,) * 6, 0.02), ((0,) * 6, 0.15), ((1, 0, 1, 1, 1, 1), 0.15)]
)
def test_next_angle_command_waits_for_mode_settling(values, interval):
    hand, _, transport = make_hand()
    with closing(hand.open()):
        assert hand.set_torques(values).wait(1) is CommandStage.ACKNOWLEDGED
        assert hand.set_angles((100,) * 6).wait(1) is CommandStage.DISPATCHED
        mode_at = next(
            at for at, data in transport.frames if len(data) == 7 and data[0] == 3
        )
        angle_at = next(
            at for at, data in transport.frames if data == bytes([1] + [100] * 6)
        )
        assert angle_at - mode_at >= interval - 0.001


def test_settling_survives_lost_ack_and_does_not_block_close():
    hand, script, transport = make_hand()
    script.dropped_command_responses.add(0x03)
    with closing(hand.open()):
        assert hand.set_torques((0,) * 6).wait(1) is CommandStage.FAILED
        assert hand.set_angles((100,) * 6).wait(1) is CommandStage.DISPATCHED
        mode_at = next(
            at for at, data in transport.frames if len(data) == 7 and data[0] == 3
        )
        angle_at = next(
            at for at, data in transport.frames if data == bytes([1] + [100] * 6)
        )
        assert angle_at - mode_at >= 0.149
        script.dropped_command_responses.clear()
        assert hand.set_torques((0,) * 6).wait(1) is CommandStage.ACKNOWLEDGED
        gain = hand.set_position_kp((49,) * 6)
        time.sleep(0.01)  # Let the actor enter the device-settling wait.
        hand.close()
        assert gain.wait(1) is CommandStage.FAILED
        assert all(data[0] != 0x06 for data in script.command_payloads)


@pytest.mark.parametrize("reopen", [False, True])
def test_recovery_and_reopen_preserve_the_outstanding_device_settling(reopen):
    hand, _, transport = make_hand()
    with closing(hand.open()):
        assert hand.set_torques((0,) * 6).wait(1) is CommandStage.ACKNOWLEDGED
        mode_at = next(
            at for at, data in transport.frames if len(data) == 7 and data[0] == 3
        )
        start = len(transport.frames)
        if reopen:
            hand.close()
            hand.open()
        else:
            hand.recover(timeout=2)
        assert transport.frames[start][0] - mode_at >= 0.149
        assert hand.get_health().connected


def test_new_response_requires_own_opcode_and_complete_six_byte_vector():
    caps = get_declared_capabilities("L6", side="left")
    backend = L6ProtocolBackend(HandSide.LEFT)
    script = make_script("L6", HandSide.LEFT)
    for semantic in (
        CommandSemantic.TORQUES,
        CommandSemantic.POSITION_KP,
        CommandSemantic.POSITION_KD,
        CommandSemantic.TORQUE_KD,
    ):
        tx = backend.command_transaction(semantic, (1,) * 6, caps)
        assert not tx.consume(script.frame(bytes([0x02] + [1] * 6))).matched
        opcode = tx.request_messages[0].data[0]
        with pytest.raises(ProtocolError):
            tx.consume(script.frame(bytes([opcode, 1])))
        for name in ("pre_dispatch_delay_s", "post_dispatch_delay_s"):
            with pytest.raises(ValidationError):
                replace(tx, **{name: -1})
