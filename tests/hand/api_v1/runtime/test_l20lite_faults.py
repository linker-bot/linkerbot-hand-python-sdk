"""LHT10 fault bytes retain their own bit semantics through public reads."""

from contextlib import closing

import pytest

from linkerbot_hand import (
    HandDevice,
    HandSide,
    L20LiteFaultStatus,
    TelemetrySemantic,
    UnsupportedOperationError,
    ValidationError,
)

from .classic_can_fake import ScriptedClassicCANTransport, make_script


@pytest.mark.parametrize("raw", range(256))
def test_all_bit_combinations_have_exact_meaning(raw):
    status = L20LiteFaultStatus(raw)
    assert status.raw == raw
    assert status.load_overload is bool(raw & 32)
    assert status.over_current is bool(raw & 8)
    assert status.over_temperature is bool(raw & 4)
    assert status.magnetic_encoder_abnormal is bool(raw & 2)
    assert status.voltage_abnormal is bool(raw & 1)
    assert status.reserved_bits == raw & 0xD0
    assert len(status.descriptions) == (raw & 0x2F).bit_count() + bool(raw & 0xD0)
    assert bool(status.descriptions) is bool(raw)
    if raw & 1:
        assert "Overvoltage or undervoltage" in status.descriptions


@pytest.mark.parametrize("raw", [-1, 256, True, 1.0, "1"])
def test_invalid_fault_byte_rejected(raw):
    with pytest.raises(ValidationError):
        L20LiteFaultStatus(raw)


@pytest.mark.parametrize("side", [HandSide.LEFT, HandSide.RIGHT])
def test_fault_queries_and_snapshot_explain_each_joint_without_clear_support(side):
    script = make_script("L20Lite", side)
    script.readings[0x35] = (0, 1, 2, 4, 8)
    script.readings[0x36] = (32, 16, 64, 128, 255)
    hand = HandDevice(
        model="L20Lite", side=side, transport=ScriptedClassicCANTransport(script)
    )
    with closing(hand.open()):
        caps = hand.get_capabilities()
        assert caps.telemetry_channel(TelemetrySemantic.FAULTS).decoded_statuses
        baseline = hand.get_snapshot().faults
        sample = hand.get_faults(timeout=1)
        assert sample.raw == (0, 1, 2, 4, 8, 32, 16, 64, 128, 255)
        assert (
            sample.statuses == baseline.statuses == hand.get_snapshot().faults.statuses
        )
        assert all(isinstance(status, L20LiteFaultStatus) for status in sample.statuses)
        count = len(script.sent_payloads)
        assert not caps.operations.clear_faults
        with pytest.raises(UnsupportedOperationError):
            hand.clear_faults()
        assert len(script.sent_payloads) == count
