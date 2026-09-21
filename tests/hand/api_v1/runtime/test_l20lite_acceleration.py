"""LHT10 acceleration writes confirm two groups without inventing queries."""

from contextlib import closing

import can
import pytest

from linkerbot_hand import (
    AckSupport,
    CommandFamily,
    CommandSemantic,
    CommandStage,
    Confidence,
    ConfiguredAccelerationSample,
    DataOrigin,
    HandDevice,
    HandSide,
    ProtocolError,
    ReadMode,
    TelemetrySemantic,
    UnsupportedOperationError,
    ValidationError,
    get_declared_capabilities,
)
from linkerbot_hand.protocols.l20lite.transactions import L20LiteProtocolBackend

from .classic_can_fake import ClassicCANDeviceScript, ScriptedClassicCANTransport

VALUES = (0, 1, 2, 100, 254, 254, 80, 3, 4, 5)


class AccelerationScript(ClassicCANDeviceScript):
    def respond(self, request):
        data = bytes(request.data)
        if data and data[0] in (0x87, 0x88):
            self.sent_payloads.append(data)
            # Fail if a baseline, getter or polling path invents a query.
            assert len(data) == 6
            if data[0] in self.dropped_command_responses:
                return ()
            echoed = self.bad_command_echoes.get(data[0], tuple(data[1:]))
            return (self.frame(bytes((data[0], *echoed))),)
        return super().respond(request)


def make_hand(side=HandSide.LEFT):
    script = AccelerationScript(
        "L20Lite",
        side,
        f"LHT10-07-98-{'L' if side is HandSide.LEFT else 'R'}-A-1-C",
        (4, 6, 10),
    )
    hand = HandDevice(
        model="L20Lite", side=side, transport=ScriptedClassicCANTransport(script)
    )
    return hand, script


@pytest.mark.parametrize("side", [HandSide.LEFT, HandSide.RIGHT])
def test_accelerations_confirm_both_groups_in_fifo_and_getter_never_sends(side):
    hand, script = make_hand(side)
    with closing(hand.open()):
        caps = hand.get_capabilities()
        command = caps.command(CommandSemantic.ACCELERATIONS)
        channel = caps.telemetry_channel(TelemetrySemantic.CONFIGURED_ACCELERATIONS)
        assert command.ack_support is AckSupport.SUPPORTED
        assert command.channels == channel.channels == caps.joint_names
        assert command.raw_unit == channel.raw_unit == "count"
        assert channel.raw_encoding == "u8"
        assert channel.unit_confidence is Confidence.UNKNOWN
        assert channel.raw_range.minimum == 0 and channel.raw_range.maximum == 254
        assert channel.read_mode is ReadMode.COMMAND_RESPONSE
        assert not channel.device_queried and not channel.device_measured
        assert channel.recommended_hz == channel.estimated_transaction_s == 0
        before = len(script.sent_payloads)
        assert not hand.get_snapshot().configured_accelerations.meta.valid
        with pytest.raises(UnsupportedOperationError):
            hand.get_configured_accelerations(timeout=1)
        assert len(script.sent_payloads) == before
        handles = [
            hand.set_accelerations(VALUES),
            hand.set_accelerations(tuple(reversed(VALUES))),
        ]
        for handle in handles:
            assert handle.family is CommandFamily.ACCELERATION_SETTING
            assert handle.wait(1) is CommandStage.ACKNOWLEDGED
            assert handle.sent_frames == 2
        assert script.sent_payloads[before:] == [
            bytes((opcode, *values[offset : offset + 5]))
            for values in (VALUES, tuple(reversed(VALUES)))
            for opcode, offset in ((0x87, 0), (0x88, 5))
        ]
        before = len(script.sent_payloads)
        sample = hand.get_configured_accelerations(timeout=1)
        assert isinstance(sample, ConfiguredAccelerationSample)
        assert sample.raw == tuple(reversed(VALUES))
        assert sample.meta.origin is DataOrigin.DEVICE_CONFIG_RESPONSE
        assert hand.get_snapshot().configured_accelerations.raw == sample.raw
        assert len(script.sent_payloads) == before
        for invalid in (
            VALUES[:-1],
            VALUES + (0,),
            (-1,) * 10,
            (255,) * 10,
            (256,) * 10,
            (1.5,) * 10,
            (True,) * 10,
        ):
            with pytest.raises(ValidationError):
                hand.set_accelerations(invalid)
        assert len(script.sent_payloads) == before


def transaction():
    caps = get_declared_capabilities("L20Lite", side="left")
    return L20LiteProtocolBackend(HandSide.LEFT).command_transaction(
        CommandSemantic.ACCELERATIONS, VALUES, caps
    )


def frame(data, **flags):
    return can.Message(
        arbitration_id=flags.pop("arbitration_id", 0x28),
        is_extended_id=flags.pop("is_extended_id", False),
        data=data,
        **flags,
    )


def test_two_echoes_assemble_out_of_order_without_partial_publication():
    tx = transaction()
    assert tx.request_interval_s == 0.001
    first, second = (bytes(m.data) for m in tx.request_messages)
    assert first == bytes((0x87, *VALUES[:5]))
    assert second == bytes((0x88, *VALUES[5:]))
    for flags in (
        {"arbitration_id": 0x27},
        {"is_extended_id": True},
        {"is_fd": True},
        {"is_error_frame": True},
        {"is_remote_frame": True},
    ):
        assert not tx.consume(frame(first, **flags)).matched
    assert not tx.consume(frame(b"\x99")).matched
    for _ in range(2):
        partial = tx.consume(frame(second))
        assert partial.matched and not partial.complete
        assert partial.result is None
    complete = tx.consume(frame(first))
    assert complete.complete and complete.acknowledged
    assert complete.result.raw == VALUES


@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize("fault", ["short", "long", "wrong_value", "duplicate"])
def test_malformed_or_mismatched_echoes_cannot_confirm(index, fault):
    tx = transaction()
    data = bytes(tx.request_messages[index].data)
    if fault == "duplicate":
        assert not tx.consume(frame(data)).complete
    else:
        assert not tx.consume(tx.request_messages[1 - index]).complete
    bad = {
        "short": data[:-1],
        "long": data + b"\x00",
        "wrong_value": data[:-1] + bytes((data[-1] ^ 1,)),
        "duplicate": data[:-1] + bytes((data[-1] ^ 1,)),
    }[fault]
    with pytest.raises(ProtocolError):
        tx.consume(frame(bad))


@pytest.mark.parametrize("opcode", [0x87, 0x88])
@pytest.mark.parametrize("failure", ["missing", "mismatched"])
def test_failed_write_preserves_last_complete_echo_and_connection(opcode, failure):
    hand, script = make_hand()
    with closing(hand.open()):
        assert hand.set_accelerations(VALUES).wait(1) is CommandStage.ACKNOWLEDGED
        original = hand.get_configured_accelerations(timeout=1)
        if failure == "missing":
            script.dropped_command_responses.add(opcode)
        else:
            script.bad_command_echoes[opcode] = (1,) * 5
        failed = hand.set_accelerations((16,) * 10)
        assert failed.wait(1) is CommandStage.FAILED
        assert failed.error is not None
        cached = hand.get_configured_accelerations(timeout=1)
        assert cached.raw == original.raw
        assert cached.meta.sequence == original.meta.sequence
        assert hand.get_angles(timeout=1).meta.valid


def test_missing_first_write_echo_leaves_cache_unavailable():
    hand, script = make_hand()
    script.dropped_command_responses.add(0x88)
    with closing(hand.open()):
        assert hand.set_accelerations(VALUES).wait(1) is CommandStage.FAILED
        assert not hand.get_snapshot().configured_accelerations.meta.valid
        with pytest.raises(UnsupportedOperationError):
            hand.get_configured_accelerations(timeout=1)


def test_recovery_clears_echo_cache_without_replaying_accelerations():
    hand, script = make_hand()
    with closing(hand.open()):
        assert hand.set_accelerations(VALUES).wait(1) is CommandStage.ACKNOWLEDGED
        before = len(script.sent_payloads)
        hand.recover(timeout=1)
        assert not any(
            data[0] in (0x87, 0x88) for data in script.sent_payloads[before:]
        )
        assert not hand.get_snapshot().configured_accelerations.meta.valid
        with pytest.raises(UnsupportedOperationError):
            hand.get_configured_accelerations(timeout=1)


def test_backend_does_not_expose_an_acceleration_query():
    caps = get_declared_capabilities("L20Lite", side="left")
    backend = L20LiteProtocolBackend(HandSide.LEFT)
    assert TelemetrySemantic.CONFIGURED_ACCELERATIONS not in backend.baseline_semantics(
        caps
    )
    with pytest.raises(UnsupportedOperationError, match="no active protocol query"):
        backend.read_transaction(TelemetrySemantic.CONFIGURED_ACCELERATIONS, caps)
