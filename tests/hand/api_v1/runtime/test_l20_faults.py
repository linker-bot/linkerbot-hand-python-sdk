"""L20 fault meanings and effective-joint mapping through public reads."""

from contextlib import closing
from dataclasses import FrozenInstanceError

import can
import pytest

from linkerbot_hand import (
    CommandStage,
    DataOrigin,
    FaultSample,
    HandDevice,
    HandSide,
    L20FaultStatus,
    PollingPlan,
    ProtocolError,
    SampleMeta,
    TelemetrySemantic,
    TimeoutError,
    ValidationError,
    get_declared_capabilities,
)
from linkerbot_hand.protocols.l20 import L20ProtocolBackend

from .classic_can_fake import ScriptedClassicCANTransport, make_script, wait_until

BITS = (
    (0x80, "motor_communication_abnormal", "Motor communication abnormal"),
    (
        0x40,
        "soft_rotor_lock",
        "Soft rotor lock (may occur while grasping; clears automatically after release)",
    ),
    (0x20, "over_temperature", "Overtemperature"),
    (0x10, "motor_self_test_abnormal", "Motor self-test abnormal"),
    (0x08, "voltage_abnormal", "Motor voltage abnormal"),
    (0x04, "motor_stall", "Motor stall"),
    (0x02, "over_current", "Overcurrent"),
    (0x01, "motor_rotor_lock", "Motor rotor lock"),
)
# Distinct reserved-slot sentinels must never become public joint faults.
FRAGMENTS = (
    (0x59, (0x80, 0x40, 0x20, 0xAA, 0xBB, 0x10)),
    (0x5A, (0x08, 0xCC, 0x04, 0xDD, 0xEE, 0x02)),
    (0x5B, (0x01, 0xFF, 0, 0xFF, 0xFF, 0x81)),
    (0x5C, (0x18, 0xFF, 0x06, 0xFF, 0xFF, 0x40)),
    (0x5D, (0xFF, 0, 0x82, 0, 0, 0x05)),
)
EXPECTED = (0x80, 0x40, 0x20, 0x10, 8, 4, 2, 1, 0, 0x81, 0x18, 6, 0x40, 255, 0x82, 5)
QUERIES = [bytes((opcode,)) for opcode, _ in FRAGMENTS]


@pytest.mark.parametrize("mask,field,description", BITS)
def test_single_bit_has_only_its_documented_meaning(mask, field, description):
    status = L20FaultStatus(mask)
    assert status.raw == mask and status.reserved_bits == 0
    assert status.descriptions == (description,)
    assert {name for _, name, _ in BITS if getattr(status, name)} == {field}


def test_combined_and_zero_fault_bytes_remain_exact_and_immutable():
    assert L20FaultStatus(0).descriptions == ()
    assert L20FaultStatus(0x81).descriptions == (
        "Motor communication abnormal",
        "Motor rotor lock",
    )
    status = L20FaultStatus(255)
    assert status.descriptions == tuple(description for _, _, description in BITS)
    assert status.reserved_bits == 0
    with pytest.raises(FrozenInstanceError):
        status.raw = 0
    assert FaultSample(
        raw=(255,), statuses=(status,), meta=SampleMeta.unavailable()
    ).raw == (255,)
    with pytest.raises(ValidationError, match="preserve raw"):
        FaultSample(raw=(0,), statuses=(status,), meta=SampleMeta.unavailable())


@pytest.mark.parametrize("raw", (-1, 256, True, 1.0, "1", None))
def test_fault_byte_requires_an_unsigned_integer(raw):
    with pytest.raises(ValidationError, match="L20 fault raw"):
        L20FaultStatus(raw)


@pytest.mark.parametrize("side", (HandSide.LEFT, HandSide.RIGHT))
def test_baseline_query_polling_and_clear_keep_sixteen_decoded_statuses(side):
    script = make_script("L20", side)
    script.readings.update(FRAGMENTS)
    hand = HandDevice(
        model="L20", side=side, transport=ScriptedClassicCANTransport(script)
    )
    with closing(hand.open()):
        caps = hand.get_capabilities()
        channel = caps.telemetry_channel(TelemetrySemantic.FAULTS)
        assert channel.decoded_statuses and channel.raw_unit is None
        assert channel.channels == caps.joint_names
        baseline = hand.get_snapshot().faults
        assert baseline.raw == EXPECTED
        assert all(type(status) is L20FaultStatus for status in baseline.statuses)
        assert tuple(status.raw for status in baseline.statuses) == EXPECTED
        before = len(script.sent_payloads)
        sample = hand.get_faults(timeout=1)
        assert script.sent_payloads[before:] == QUERIES
        assert sample.statuses == baseline.statuses
        assert sample.meta.origin is DataOrigin.DEVICE_RESPONSE

        for opcode, _ in FRAGMENTS:
            script.readings[opcode] = (0x40,) * 6
        before = len(script.sent_payloads)
        hand.start_polling(PollingPlan(faults_hz=5))
        wait_until(lambda: hand.get_snapshot().faults.raw == (0x40,) * 16)
        hand.stop_polling()
        polled = hand.get_snapshot().faults
        assert all(type(status) is L20FaultStatus for status in polled.statuses)
        assert all(status.soft_rotor_lock for status in polled.statuses)
        assert all(not status.motor_rotor_lock for status in polled.statuses)
        sent = script.sent_payloads[before:]
        assert sent and len(sent) % 5 == 0
        assert sent == QUERIES * (len(sent) // 5)

        # Fault decoding must not add implicit clear requests or change explicit clear.
        before = len(script.sent_payloads)
        assert hand.clear_faults().wait(1) is CommandStage.DISPATCHED
        assert script.sent_payloads[before:] == [
            bytes((opcode, 1, 1, 1, 1, 1, 1)) for opcode, _ in FRAGMENTS
        ]
        cleared = hand.get_faults(timeout=1)
        assert cleared.raw == (0,) * 16
        assert all(status.descriptions == () for status in cleared.statuses)
        assert baseline.raw == EXPECTED


@pytest.mark.parametrize("failure", ("missing", "malformed"))
def test_incomplete_or_malformed_reads_preserve_the_last_complete_sample(failure):
    script = make_script("L20", HandSide.LEFT)
    script.readings.update(FRAGMENTS)
    hand = HandDevice(
        model="L20", side="left", transport=ScriptedClassicCANTransport(script)
    )
    with closing(hand.open()):
        baseline = hand.get_snapshot().faults
        script.readings[0x59] = (0,) * 6
        if failure == "missing":
            script.unsupported_queries.add(0x5B)
        else:
            script.malformed_query_responses[0x5B] = b"\x5b\x00"
        with pytest.raises(TimeoutError if failure == "missing" else ProtocolError):
            hand.get_faults(timeout=1)
        cached = hand.get_snapshot().faults
        assert cached.raw == EXPECTED and cached.statuses == baseline.statuses
        assert cached.meta.sequence == baseline.meta.sequence
        assert hand.get_angles(timeout=1).meta.valid


@pytest.mark.parametrize("side", (HandSide.LEFT, HandSide.RIGHT))
def test_fragment_order_duplicates_and_route_matching_do_not_corrupt_faults(side):
    caps = get_declared_capabilities("L20", side=side)
    backend = L20ProtocolBackend(side)
    script = make_script("L20", side)
    tx = backend.read_transaction(TelemetrySemantic.FAULTS, caps)
    replies = [script.frame(bytes((opcode, *values))) for opcode, values in FRAGMENTS]
    for frame in (
        can.Message(
            arbitration_id=0x27 if side is HandSide.LEFT else 0x28,
            is_extended_id=False,
            data=replies[0].data,
        ),
        can.Message(
            arbitration_id=script.arbitration_id,
            is_extended_id=True,
            data=replies[0].data,
        ),
        script.frame(b"\x99\x00\x00\x00\x00\x00\x00"),
    ):
        assert not tx.consume(frame).matched
    for frame in reversed(replies[1:]):
        assert tx.consume(frame).result is None
        assert tx.consume(frame).result is None
    complete = tx.consume(replies[0])
    assert complete.complete and complete.result.raw == EXPECTED
    assert tuple(status.raw for status in complete.result.fault_statuses) == EXPECTED
    # Multi-frame reports outside a transaction cannot publish partial joint state.
    assert all(backend.decode_report(frame, caps) == () for frame in replies)

    tx = backend.read_transaction(TelemetrySemantic.FAULTS, caps)
    assert not tx.consume(replies[0]).complete
    with pytest.raises(ProtocolError, match="duplicate"):
        tx.consume(script.frame(b"\x59\x00\x00\x00\x00\x00\x00"))
