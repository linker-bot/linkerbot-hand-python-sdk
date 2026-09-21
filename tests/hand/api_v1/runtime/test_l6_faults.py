"""L6 fault interpretation retains the original byte at every read path."""

from contextlib import closing

import pytest

from linkerbot_hand import (
    HandDevice,
    HandSide,
    L6FaultStatus,
    TelemetrySemantic,
    ValidationError,
)

from .classic_can_fake import ScriptedClassicCANTransport, make_script, wait_until


@pytest.mark.parametrize(
    "raw,field,text",
    [
        (0x01, "phase_b_over_current", "Phase B overcurrent"),
        (0x02, "phase_c_over_current", "Phase C overcurrent"),
        (0x04, "phase_a_over_current", "Phase A overcurrent"),
        (0x08, "overload_1", "Overload 1"),
        (0x10, "overload_2", "Overload 2"),
        (0x20, "motor_over_temperature", "Motor overtemperature"),
        (0x40, "mcu_over_temperature", "MCU overtemperature"),
    ],
)
def test_fault_bits_do_not_follow_o30_or_servo_layout(raw, field, text):
    status = L6FaultStatus(raw)
    assert getattr(status, field) is True
    assert status.descriptions == (text,)
    assert L6FaultStatus(0).descriptions == ()
    assert L6FaultStatus(0xFF).reserved_bits == 0x80
    assert len(L6FaultStatus(0xFF).descriptions) == 8


@pytest.mark.parametrize("raw", [True, -1, 256, 1.0])
def test_non_byte_fault_is_rejected(raw):
    with pytest.raises(ValidationError):
        L6FaultStatus(raw)


def test_baseline_query_and_unsolicited_faults_share_the_typed_contract():
    script = make_script("L6", HandSide.LEFT)
    script.readings[0x35] = (1, 2, 4, 0x28, 0x50, 0x80)
    transport = ScriptedClassicCANTransport(script)
    hand = HandDevice(model="L6", side="left", transport=transport)
    with closing(hand.open()):
        assert (
            hand.get_capabilities()
            .telemetry_channel(TelemetrySemantic.FAULTS)
            .decoded_statuses
        )
        baseline = hand.get_snapshot().faults
        queried = hand.get_faults(timeout=1)
        assert queried.statuses == baseline.statuses
        assert tuple(status.raw for status in queried.statuses) == queried.raw
        assert all(isinstance(status, L6FaultStatus) for status in queried.statuses)
        transport.emit(script.frame(bytes([0x35] + [0xFF] * 6)))
        wait_until(lambda: hand.get_snapshot().faults.raw == (0xFF,) * 6)
        assert all(
            len(status.descriptions) == 8
            for status in hand.get_snapshot().faults.statuses
        )
