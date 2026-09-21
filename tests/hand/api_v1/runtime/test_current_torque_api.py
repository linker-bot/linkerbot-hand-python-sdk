"""Public current and torque APIs preserve separate O30 objects and samples."""

from contextlib import closing

from linkerbot_hand import (
    CommandFamily,
    CommandStage,
    CurrentLimitSample,
    CurrentSample,
    DataOrigin,
    HandDevice,
    TorqueLimitSample,
    TorqueSample,
)

from .o30_canfd_fake import O30DeviceScript, ScriptedO30Transport


def test_o30_actual_and_limit_objects_never_overwrite_each_other():
    script = O30DeviceScript()
    hand = HandDevice(model="O30", side="right", transport=ScriptedO30Transport(script))
    with closing(hand.open()):
        before = len(script.command_messages)
        current_write = hand.set_current_limits((40,) * 20)
        torque_write = hand.set_torques_limits((60,) * 20)
        assert current_write.family is CommandFamily.CURRENT_LIMIT
        assert torque_write.family is CommandFamily.TORQUE_LIMIT
        assert current_write.wait(1) is CommandStage.ACKNOWLEDGED
        assert torque_write.wait(1) is CommandStage.ACKNOWLEDGED
        writes = script.command_messages[before:]
        assert len(writes) == 2
        assert {message.data[0] & 0x7F for message in writes} == {0x04, 0x06}

        before = len(script.sent_messages)
        currents = hand.get_currents(timeout=1)
        current_limits = hand.get_current_limits(timeout=1)
        torques = hand.get_torques(timeout=1)
        torque_limits = hand.get_torques_limits(timeout=1)
        assert [message.data for message in script.sent_messages[before:]] == [
            b"\x04\x00\x19",
            b"\x84\x00\x19",
            b"\x06\x00\x19",
            b"\x86\x00\x19",
        ]
        for sample, sample_type, expected, origin in (
            (
                currents,
                CurrentSample,
                tuple(range(80, 100)),
                DataOrigin.DEVICE_RESPONSE,
            ),
            (
                current_limits,
                CurrentLimitSample,
                (40,) * 20,
                DataOrigin.DEVICE_CONFIG_RESPONSE,
            ),
            (torques, TorqueSample, (20,) * 20, DataOrigin.DEVICE_RESPONSE),
            (
                torque_limits,
                TorqueLimitSample,
                (60,) * 20,
                DataOrigin.DEVICE_CONFIG_RESPONSE,
            ),
        ):
            assert isinstance(sample, sample_type)
            assert sample.raw == expected and sample.meta.origin is origin
        state = hand.get_snapshot()
        assert state.currents.raw == currents.raw
        assert state.current_limits.raw == current_limits.raw
        assert state.torques.raw == torques.raw
        assert state.torques_limits.raw == torque_limits.raw
        assert state.joints[0].current_raw == 80
        assert state.joints[0].current_limit_raw == 40
        assert state.joints[0].torque_raw == 20
        assert state.joints[0].torque_limit_raw == 60


def test_ambiguous_effort_names_are_not_public_aliases():
    assert not hasattr(HandDevice, "get_effort_limits")
    assert not hasattr(HandDevice, "set_effort_limits")
