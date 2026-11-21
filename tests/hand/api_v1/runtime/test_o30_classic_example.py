"""Exercise preserves target registers and records independent physical feedback."""

from argparse import Namespace
from dataclasses import replace

import can
import pytest

from examples.o30_classic_can import exercise, read_targets
from linkerbot_hand.protocols.o30.codec import decode_v25, encode_v25

from .o30_canfd_fake import O30DeviceScript
from .test_o30_classic_can import make_hand


def test_polling_with_new_samples_does_not_hide_a_query_timeout(monkeypatch):
    class DropOnce(O30DeviceScript):
        polling = False
        angle_ranges = 0

        def _read(self, main_index, sub_index, length, *, rts):
            if self.polling and main_index == 1 and sub_index == 10:
                self.angle_ranges += 1
                if self.angle_ranges == 2:
                    return ()
            return super()._read(main_index, sub_index, length, rts=rts)

    script = DropOnce()
    hand, _ = make_hand(script)
    report = {}
    start_polling = hand.start_polling

    def start(plan):
        script.polling = True
        return start_polling(plan)

    monkeypatch.setattr(hand, "start_polling", start)
    try:
        hand.open()
        with pytest.raises(
            RuntimeError, match="polling reported a communication error"
        ):
            exercise(hand, options(duration=0.3), report)
        assert report["polling"]["observed_unique_angle_samples"] > 0
        assert report["health"]["connected"]
        assert report["health"]["recent_error"]["code"] == "timeout"
    finally:
        hand.close()


def options(**overrides):
    return Namespace(
        **(
            {
                "write_back": False,
                "motion_joint": None,
                "delta": 2,
                "angles_hz": 100,
                "duration": 0.04,
                "motion_timeout": 0.04,
            }
            | overrides
        )
    )


class NoMotion(O30DeviceScript):
    def _write(self, main_index, sub_index, length, payload, *, rts=False):
        before = self.actual[1]
        replies = super()._write(main_index, sub_index, length, payload, rts=rts)
        if main_index == 1:
            self.actual[1] = before
        return replies


@pytest.mark.parametrize("write_back", [False, True])
def test_read_and_configuration_exercise_never_writes_angles(write_back):
    script = O30DeviceScript()
    script.targets[1] = encode_v25((100,) * 20)
    hand, transport = make_hand(script)
    report = {}
    try:
        hand.open()
        exercise(hand, options(write_back=write_back), report)
        assert report["force"]["status"] == "SKIP"
        assert report["polling"]["observed_unique_angle_samples"] > 0
        assert decode_v25(script.targets[1]) == (100,) * 20
        assert not any(
            m.data[0] & 0x7F == 1 and len(m.data) > 3 for m in transport.requests
        )
        if write_back:
            assert report["write_back"]["configured_speeds"]["readback"] == list(
                range(30, 50)
            )
    finally:
        hand.close()


def test_successful_motion_restores_original_targets_and_actual_angles():
    hand, transport = make_hand()
    original = decode_v25(transport.script.targets[1])
    report = {}
    try:
        hand.open()
        exercise(
            hand,
            options(motion_joint=6),
            report,
            target_reader=lambda: decode_v25(transport.script.targets[1]),
        )
        motion = report["motion"]
        assert motion["status"] == "TARGETS_VERIFIED"
        assert motion["actual_motion_observed"]
        assert motion["signed_motion_raw"] == 2
        assert motion["restore_error_raw"] == 0
        assert motion["restored_targets"] == original
        assert motion["other_joint_max_change_raw"] == 0
    finally:
        hand.close()


@pytest.mark.parametrize("delta", [2, -2, 1])
def test_no_actual_movement_is_recorded_without_failing_protocol_validation(delta):
    hand, transport = make_hand(NoMotion())
    report = {}
    try:
        hand.open()
        exercise(
            hand,
            options(motion_joint=6, delta=delta),
            report,
            target_reader=lambda: decode_v25(transport.script.targets[1]),
        )
        assert report["motion"]["command"]["stage"] == "acknowledged"
        assert report["motion"]["status"] == "TARGETS_VERIFIED"
        assert not report["motion"]["actual_motion_observed"]
        assert report["motion"]["signed_motion_raw"] == 0
        assert report["motion"]["target_error_raw"] == abs(delta)
        assert report["motion"]["restore"]["stage"] == "acknowledged"
    finally:
        hand.close()


def test_single_joint_exercise_preserves_other_unreached_targets():
    script = NoMotion()
    original = [100] * 20
    original[6] = 6
    script.targets[1] = encode_v25(tuple(original))
    hand, _ = make_hand(script)
    report = {}
    try:
        hand.open()
        exercise(
            hand,
            options(motion_joint=6),
            report,
            target_reader=lambda: decode_v25(script.targets[1]),
        )
        expected = original.copy()
        expected[6] += 2
        assert report["motion"]["target_readback"] == tuple(expected)
        assert decode_v25(script.targets[1]) == tuple(original)
    finally:
        hand.close()


def test_actual_target_offset_does_not_replace_original_targets_or_fail_validation():
    script = O30DeviceScript()
    script.targets[1] = encode_v25((100,) * 20)
    hand, _ = make_hand(script)
    report = {}
    try:
        hand.open()
        exercise(
            hand,
            options(motion_joint=6),
            report,
            target_reader=lambda: decode_v25(script.targets[1]),
        )
        motion = report["motion"]
        assert motion["status"] == "TARGETS_VERIFIED"
        assert motion["initial_actual"][6] == 6
        assert motion["target_readback"][6] == 102
        assert motion["restored_targets"] == (100,) * 20
        assert decode_v25(script.targets[1]) == (100,) * 20
    finally:
        hand.close()


def test_target_readback_failure_still_restores_original_targets():
    hand, transport = make_hand()
    original = decode_v25(transport.script.targets[1])
    report = {}
    calls = 0

    def targets():
        nonlocal calls
        calls += 1
        return (0,) * 20 if calls == 2 else decode_v25(transport.script.targets[1])

    try:
        hand.open()
        with pytest.raises(RuntimeError, match="readback mismatch"):
            exercise(hand, options(motion_joint=6), report, target_reader=targets)
        assert report["motion"]["restored_targets"] == original
        assert decode_v25(transport.script.targets[1]) == original
    finally:
        hand.close()


def test_feedback_query_failure_still_restores_targets_and_fails_validation():
    hand, transport = make_hand()
    original = decode_v25(transport.script.targets[1])
    original_read = hand.get_angles
    calls = 0
    report = {}

    def angles(timeout):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise TimeoutError("feedback query failed")
        return original_read(timeout=timeout)

    try:
        hand.open()
        hand.get_angles = angles
        with pytest.raises(TimeoutError, match="feedback query failed"):
            exercise(
                hand,
                options(motion_joint=6),
                report,
                target_reader=lambda: decode_v25(transport.script.targets[1]),
            )
        assert report["motion"]["restored_targets"] == original
        assert "command_error" in report["motion"]
        assert "status" not in report["motion"]
        assert decode_v25(transport.script.targets[1]) == original
    finally:
        hand.close()


def test_restore_readback_mismatch_is_an_error_even_with_physical_offsets_allowed():
    hand, transport = make_hand()
    report = {}
    calls = 0

    def targets():
        nonlocal calls
        calls += 1
        return (0,) * 20 if calls == 3 else decode_v25(transport.script.targets[1])

    try:
        hand.open()
        with pytest.raises(RuntimeError, match="target register restoration failed"):
            exercise(hand, options(motion_joint=6), report, target_reader=targets)
        assert "restore_error" in report["motion"]
        assert "status" not in report["motion"]
    finally:
        hand.close()


def test_read_targets_assembles_only_rts_target_fragments(monkeypatch):
    wire = encode_v25(tuple(range(20)))
    messages = []

    class Bus:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def set_filters(self, filters):
            pass

        def send(self, message, timeout):
            start = message.data[1]
            assert bytes(message.data) == bytes((0x81, start, 5))
            assert not message.is_fd
            messages.extend(
                [
                    can.Message(
                        arbitration_id=0x401,
                        data=b"\x01\x00\x01\xff",
                        is_extended_id=False,
                    ),
                    can.Message(
                        arbitration_id=0x401,
                        data=bytes((0x81, start, 5)) + wire[start : start + 5],
                        is_extended_id=False,
                    ),
                ]
            )

        def recv(self, timeout):
            return messages.pop(0) if messages else None

    monkeypatch.setattr("examples.o30_classic_can.can.Bus", lambda **kwargs: Bus())
    assert read_targets("unused", 1) == tuple(range(20))


def test_other_joint_feedback_change_is_recorded_without_changing_targets():
    hand, transport = make_hand()
    report = {}
    original_read = hand.get_angles
    calls = 0

    def angles(timeout):
        nonlocal calls
        calls += 1
        sample = original_read(timeout=timeout)
        if calls == 3:
            values = list(sample.raw)
            values[5] += 10
            return replace(sample, raw=tuple(values))
        return sample

    try:
        hand.open()
        hand.get_angles = angles
        exercise(
            hand,
            options(motion_joint=6),
            report,
            target_reader=lambda: decode_v25(transport.script.targets[1]),
        )
        assert report["motion"]["status"] == "TARGETS_VERIFIED"
        assert report["motion"]["other_joint_max_change_raw"] == 10
    finally:
        hand.close()


def test_transient_feedback_is_recorded_but_not_claimed_as_arrival():
    hand, transport = make_hand(NoMotion())
    report = {}
    original_read = hand.get_angles
    calls = 0

    def angles(timeout):
        nonlocal calls
        calls += 1
        sample = original_read(timeout=timeout)
        if calls == 3:
            values = list(sample.raw)
            values[6] += 2
            return replace(sample, raw=tuple(values))
        return sample

    try:
        hand.open()
        hand.get_angles = angles
        exercise(
            hand,
            options(motion_joint=6),
            report,
            target_reader=lambda: decode_v25(transport.script.targets[1]),
        )
        assert report["motion"]["status"] == "TARGETS_VERIFIED"
        assert report["motion"]["actual_motion_observed"]
        assert report["motion"]["observed"][6] == 6
        assert report["motion"]["target_error_raw"] == 2
        assert "arrival_confirmed" not in report["motion"]
    finally:
        hand.close()
