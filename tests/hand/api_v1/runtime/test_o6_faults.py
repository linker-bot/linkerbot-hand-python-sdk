"""O6 fault decoding from the documented bit table through the public API."""

from contextlib import closing
from dataclasses import FrozenInstanceError

import can
import pytest

from linkerbot_hand import (
    DataOrigin,
    FaultSample,
    HandDevice,
    HandSide,
    L20LiteFaultStatus,
    O6FaultStatus,
    PollingPlan,
    ProtocolError,
    SampleMeta,
    TelemetrySemantic,
    UnsupportedOperationError,
    ValidationError,
    get_declared_capabilities,
)
from linkerbot_hand.protocols.o6 import O6ProtocolBackend

from .classic_can_fake import ScriptedClassicCANTransport, make_script, wait_until

_DEFINED_BITS = (
    (0x20, "load_overload", "Load overload"),
    (0x08, "over_current", "Overcurrent"),
    (0x04, "over_temperature", "Overtemperature"),
    (0x02, "magnetic_encoder_abnormal", "Magnetic encoder abnormal"),
    (0x01, "voltage_abnormal", "Overvoltage or undervoltage"),
)


@pytest.mark.parametrize("mask,field,description", _DEFINED_BITS)
def test_o6_single_fault_bits_have_only_the_documented_meaning(
    mask, field, description
):
    status = O6FaultStatus(mask)
    assert status.raw == mask
    assert status.descriptions == (description,)
    assert status.reserved_bits == 0
    assert {name for _, name, _ in _DEFINED_BITS if getattr(status, name)} == {field}
    assert not isinstance(status, L20LiteFaultStatus)


@pytest.mark.parametrize("reserved", (0, 0x10, 0x40, 0x80, 0xD0))
def test_undefined_bits_are_preserved_without_inventing_faults(reserved):
    status = O6FaultStatus(reserved)
    assert status.reserved_bits == reserved
    assert not any(getattr(status, name) for _, name, _ in _DEFINED_BITS)
    assert status.descriptions == (
        (f"Undefined fault bits: 0x{reserved:02X}",) if reserved else ()
    )


def test_combined_faults_keep_every_description_and_the_original_byte():
    status = O6FaultStatus(0xFF)
    assert status.descriptions == (
        "Load overload",
        "Overcurrent",
        "Overtemperature",
        "Magnetic encoder abnormal",
        "Overvoltage or undervoltage",
        "Undefined fault bits: 0xD0",
    )
    assert status.raw == 0xFF
    with pytest.raises(FrozenInstanceError):
        status.raw = 0
    sample = FaultSample(raw=(0xFF,), statuses=(status,), meta=SampleMeta.unavailable())
    assert sample.statuses == (status,)
    with pytest.raises(ValidationError, match="preserve raw"):
        FaultSample(raw=(0,), statuses=(status,), meta=SampleMeta.unavailable())


@pytest.mark.parametrize("raw", (-1, 256, True, 1.0, "1", None))
def test_o6_fault_byte_requires_an_unsigned_integer(raw):
    with pytest.raises(ValidationError, match="O6 fault raw"):
        O6FaultStatus(raw)


@pytest.mark.parametrize("side", (HandSide.LEFT, HandSide.RIGHT))
def test_baseline_query_polling_and_reports_keep_the_six_joint_statuses(side):
    script = make_script("O6", side)
    baseline_raw = (0x20, 0x08, 0x04, 0x02, 0x01, 0)
    script.readings[0x35] = baseline_raw
    transport = ScriptedClassicCANTransport(script)
    hand = HandDevice(model="O6", side=side, transport=transport)
    with closing(hand.open()):
        caps = hand.get_capabilities()
        channel = caps.telemetry_channel(TelemetrySemantic.FAULTS)
        assert channel.decoded_statuses and channel.raw_unit is None
        assert channel.channels == (
            "thumb_flex",
            "thumb_abd",
            "index",
            "middle",
            "ring",
            "pinky",
        )
        baseline = hand.get_snapshot().faults
        assert baseline.raw == baseline_raw
        assert tuple(status.raw for status in baseline.statuses) == baseline_raw
        assert all(type(status) is O6FaultStatus for status in baseline.statuses)

        # A malformed response cannot replace the last complete sample.
        script.malformed_query_responses[0x35] = b"\x35\xff"
        with pytest.raises(ProtocolError):
            hand.get_faults(timeout=1)
        assert hand.get_snapshot().faults.statuses == baseline.statuses
        del script.malformed_query_responses[0x35]

        queried_raw = (0xFF, 0xD0, 0x29, 0x05, 0, 2)
        script.readings[0x35] = queried_raw
        before = len(script.sent_payloads)
        sample = hand.get_faults(timeout=1)
        assert script.sent_payloads[before:] == [b"\x35"]
        assert sample.raw == queried_raw
        assert tuple(status.raw for status in sample.statuses) == queried_raw
        assert sample.meta.origin is DataOrigin.DEVICE_RESPONSE
        assert hand.get_snapshot().faults.statuses == sample.statuses
        assert baseline.raw == baseline_raw  # Prior samples remain immutable.

        polled_raw = (1, 4, 8, 32, 0x40, 0x80)
        script.readings[0x35] = polled_raw
        before = len(script.sent_payloads)
        hand.start_polling(PollingPlan(faults_hz=5))
        wait_until(lambda: hand.get_snapshot().faults.raw == polled_raw)
        hand.stop_polling()
        polled = hand.get_snapshot().faults
        assert all(type(status) is O6FaultStatus for status in polled.statuses)
        assert tuple(status.raw for status in polled.statuses) == polled_raw
        assert polled.meta.origin is DataOrigin.DEVICE_RESPONSE
        assert script.sent_payloads[before:]
        assert set(script.sent_payloads[before:]) == {b"\x35"}

        reported_raw = (0, 0xFF, 0xD0, 0x10, 0x40, 0x80)
        transport.emit(script.frame(bytes((0x35, *reported_raw))))
        wait_until(lambda: hand.get_snapshot().faults.raw == reported_raw)
        reported = hand.get_snapshot().faults
        assert reported.meta.origin is DataOrigin.DEVICE_REPORT
        assert tuple(status.raw for status in reported.statuses) == reported_raw
        assert all(type(status) is O6FaultStatus for status in reported.statuses)

        assert not caps.operations.clear_faults
        before = len(script.sent_payloads)
        with pytest.raises(UnsupportedOperationError):
            hand.clear_faults()
        assert len(script.sent_payloads) == before
        assert hand.get_health().connected


@pytest.mark.parametrize("side", (HandSide.LEFT, HandSide.RIGHT))
def test_fault_response_matching_and_report_boundaries(side):
    caps = get_declared_capabilities("O6", side=side)
    backend = O6ProtocolBackend(side)
    script = make_script("O6", side)
    tx = backend.read_transaction(TelemetrySemantic.FAULTS, caps)
    for data in (b"\x35", b"\x35\xff", b"\x35" + b"\xff" * 7):
        malformed = script.frame(data)
        with pytest.raises(ProtocolError):
            tx.consume(malformed)
        assert backend.decode_report(malformed, caps) == ()

    for message in (
        can.Message(
            arbitration_id=0x27 if side is HandSide.LEFT else 0x28,
            data=b"\x35" + b"\xff" * 6,
            is_extended_id=False,
        ),
        can.Message(
            arbitration_id=script.arbitration_id,
            data=b"\x35" + b"\xff" * 6,
            is_extended_id=True,
        ),
        script.frame(b"\x99" + b"\xff" * 6),
    ):
        assert not tx.consume(message).matched
        assert backend.decode_report(message, caps) == ()

    temperature = backend.read_transaction(TelemetrySemantic.TEMPERATURES, caps)
    payload = temperature.consume(script.frame(b"\x33" + b"\x20" * 6)).result
    assert payload.fault_statuses == ()
