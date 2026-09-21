"""O6 current measurements stay distinct from current-limit commands."""

from contextlib import closing

import pytest

from linkerbot_hand import (
    CommandSemantic,
    CommandStage,
    CurrentLimitSample,
    CurrentSample,
    DataOrigin,
    HandDevice,
    HandSide,
    PollingPlan,
    ReadMode,
    TelemetrySemantic,
    UnsupportedOperationError,
)

from .classic_can_fake import ScriptedClassicCANTransport, make_script, wait_until


@pytest.mark.parametrize("side", [HandSide.LEFT, HandSide.RIGHT])
def test_o6_baseline_and_active_read_publish_measured_current(side):
    script = make_script("O6", side)
    script.readings[0x02] = (0, 1, 2, 127, 254, 255)
    hand = HandDevice(
        model="O6", side=side, transport=ScriptedClassicCANTransport(script)
    )
    with closing(hand.open()):
        caps = hand.get_capabilities()
        current = caps.telemetry_channel(TelemetrySemantic.CURRENTS)
        assert current is not None and current.device_measured
        assert current.device_queried
        assert current.raw_unit == "count"
        limits = caps.telemetry_channel(TelemetrySemantic.CURRENT_LIMITS)
        assert limits is not None and not limits.device_measured
        assert not limits.device_queried
        assert limits.read_mode is ReadMode.COMMAND_RESPONSE
        assert not caps.command(CommandSemantic.CURRENT_LIMITS).returns_current_state

        baseline = hand.get_snapshot()
        assert isinstance(baseline.currents, CurrentSample)
        assert baseline.currents.raw == script.readings[0x02]
        assert baseline.currents.meta.origin is DataOrigin.DEVICE_RESPONSE
        assert isinstance(baseline.current_limits, CurrentLimitSample)
        assert not baseline.current_limits.meta.valid
        assert not script.command_payloads

        before = len(script.sent_payloads)
        with pytest.raises(UnsupportedOperationError):
            hand.get_current_limits(timeout=1)
        assert len(script.sent_payloads) == before

        script.readings[0x02] = (5, 4, 3, 2, 1, 0)
        sample = hand.get_currents(timeout=1)
        assert isinstance(sample, CurrentSample)
        assert sample.raw == script.readings[0x02]
        assert sample.meta.origin is DataOrigin.DEVICE_RESPONSE
        assert sample.meta.sequence > baseline.currents.meta.sequence
        assert script.sent_payloads[before:] == [b"\x02"]
        assert tuple(joint.current_raw for joint in hand.get_snapshot().joints) == (
            sample.raw
        )


@pytest.mark.parametrize("side", [HandSide.LEFT, HandSide.RIGHT])
def test_o6_limit_write_and_cached_getter_do_not_replace_current(side):
    script = make_script("O6", side)
    script.readings[0x02] = (2, 4, 6, 8, 10, 12)
    hand = HandDevice(
        model="O6", side=side, transport=ScriptedClassicCANTransport(script)
    )
    with closing(hand.open()):
        previous = hand.get_snapshot().currents
        before = len(script.sent_payloads)
        handle = hand.set_current_limits((100,) * 6)
        assert handle.wait(timeout=1) is CommandStage.ACKNOWLEDGED
        assert handle.error is None
        snapshot = hand.get_snapshot()
        assert isinstance(snapshot.currents, CurrentSample)
        assert snapshot.currents.raw == (2, 4, 6, 8, 10, 12)
        assert snapshot.currents.meta.sequence == previous.meta.sequence
        assert snapshot.currents.meta.origin is DataOrigin.DEVICE_RESPONSE
        limits = hand.get_current_limits(timeout=1)
        assert isinstance(limits, CurrentLimitSample)
        assert limits.raw == (100,) * 6
        assert limits.meta.origin is DataOrigin.DEVICE_CONFIG_RESPONSE
        assert snapshot.current_limits.raw == limits.raw
        assert script.sent_payloads[before:] == [bytes((0x02, *([100] * 6)))]

        # A later measurement must not overwrite the configuration response.
        script.readings[0x02] = (1, 3, 5, 7, 9, 11)
        assert hand.get_currents(timeout=1).raw == script.readings[0x02]
        assert hand.get_current_limits(timeout=1).raw == (100,) * 6
        assert script.sent_payloads[before:] == [
            bytes((0x02, *([100] * 6))),
            b"\x02",
        ]
        snapshot = hand.get_snapshot()
        assert tuple(j.current_raw for j in snapshot.joints) == script.readings[0x02]
        assert tuple(j.current_limit_raw for j in snapshot.joints) == (100,) * 6

        commands_before_recovery = tuple(script.command_payloads)
        hand.recover(timeout=1)
        assert tuple(script.command_payloads) == commands_before_recovery
        assert hand.get_snapshot().currents.raw == script.readings[0x02]
        assert not hand.get_snapshot().current_limits.meta.valid
        before = len(script.sent_payloads)
        with pytest.raises(UnsupportedOperationError):
            hand.get_current_limits(timeout=1)
        assert len(script.sent_payloads) == before


@pytest.mark.parametrize("failure", ["missing", "malformed"])
def test_o6_invalid_limit_response_preserves_current_and_last_valid_limit(failure):
    script = make_script("O6", HandSide.RIGHT)
    hand = HandDevice(
        model="O6", side="right", transport=ScriptedClassicCANTransport(script)
    )
    with closing(hand.open()):
        assert hand.set_current_limits((60,) * 6).wait(1) is CommandStage.ACKNOWLEDGED
        previous = hand.get_snapshot().currents
        previous_limits = hand.get_current_limits(timeout=1)
        if failure == "missing":
            script.dropped_command_responses.add(0x02)
        else:
            script.bad_command_echoes[0x02] = (100,) * 5
        handle = hand.set_current_limits((100,) * 6)
        assert handle.wait(timeout=1) is CommandStage.FAILED
        current = hand.get_snapshot().currents
        assert current.raw == previous.raw
        assert current.meta.sequence == previous.meta.sequence
        limits = hand.get_current_limits(timeout=1)
        assert limits.raw == previous_limits.raw
        assert limits.meta.sequence == previous_limits.meta.sequence
        assert hand.get_health().connected
        assert hand.get_currents(timeout=1).raw == previous.raw


@pytest.mark.parametrize("side", [HandSide.LEFT, HandSide.RIGHT])
def test_o6_current_polling_reads_only_the_current_opcode(side):
    script = make_script("O6", side)
    hand = HandDevice(
        model="O6", side=side, transport=ScriptedClassicCANTransport(script)
    )
    with closing(hand.open()):
        assert hand.set_current_limits((90,) * 6).wait(1) is CommandStage.ACKNOWLEDGED
        before = len(script.sent_payloads)
        script.readings[0x02] = (6, 5, 4, 3, 2, 1)
        reads = script.read_counts[0x02]
        hand.start_polling(PollingPlan(current_hz=20))
        wait_until(lambda: script.read_counts[0x02] >= reads + 2)
        wait_until(lambda: hand.get_snapshot().currents.raw == (6, 5, 4, 3, 2, 1))
        hand.stop_polling()
        assert set(script.sent_payloads[before:]) == {b"\x02"}
        snapshot = hand.get_snapshot()
        assert snapshot.currents.meta.origin is DataOrigin.DEVICE_RESPONSE
        assert snapshot.current_limits.raw == (90,) * 6
        channels = {channel.channel: channel for channel in hand.get_health().channels}
        assert "currents" in channels
        assert channels["current_limits"].completed_reads == 0
