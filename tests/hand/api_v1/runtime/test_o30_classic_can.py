"""O30 classic HOP framing, command failures, sensors and range-read regressions."""

import can
import pytest

from linkerbot_hand import (
    CommandStage,
    DeviceConfig,
    HandDevice,
    HandSide,
    TransportKind,
    UnsupportedFirmwareError,
    ValidationError,
    get_declared_capabilities,
)
from linkerbot_hand.api.errors import ProtocolError
from linkerbot_hand.capabilities.types import CommandSemantic, TelemetrySemantic
from linkerbot_hand.protocols.hop.framing import (
    build_read_message,
    build_write_message,
    matching_response_frame,
)
from linkerbot_hand.protocols.hop.response import HOPRangeAssembler
from linkerbot_hand.protocols.o30 import O30ProtocolBackend
from linkerbot_hand.protocols.o30.codec import encode_v25

from .o30_canfd_fake import O30DeviceScript
from .o30_classic_fake import ClassicO30Transport


def make_hand(script=None, **transport_options):
    transport = ClassicO30Transport(script or O30DeviceScript(), **transport_options)
    hand = HandDevice(
        model="O30",
        side="right",
        transport=transport,
        config=DeviceConfig(hop_transport=TransportKind.CAN),
    )
    return hand, transport


@pytest.mark.parametrize("value", ["can", None, True, 1])
def test_mode_requires_transport_enum(value):
    with pytest.raises(ValidationError):
        DeviceConfig(hop_transport=value)


def test_default_fd_and_classic_validation():
    assert DeviceConfig().hop_transport is TransportKind.CAN_FD
    with pytest.raises(ValidationError, match="BRS"):
        DeviceConfig(hop_transport=TransportKind.CAN, hop_brs=True)
    for model in ("L6", "O6", "L20", "O20", "L30"):
        with pytest.raises(ValidationError, match="requires O6 or O30"):
            HandDevice(
                model=model,
                side=None if model == "L30" else "right",
                transport=object(),
                config=DeviceConfig(hop_transport=TransportKind.CAN),
            )
    for model in ("L6",):
        with pytest.raises(ValidationError):
            get_declared_capabilities(
                model, side="right", protocol="hop", transport_kind="can"
            )


def test_classic_frame_limits_and_response_filtering():
    read = build_read_message(1, sub_index=0, length=25, request_id=1, can_fd=False)
    assert isinstance(read, can.Message) and bytes(read.data) == b"\x01\x00\x19"
    write = build_write_message(
        1, sub_index=5, payload=bytes(5), request_id=1, can_fd=False
    )
    assert len(write.data) == 8 and not write.is_fd
    with pytest.raises(ValidationError, match="5 bytes"):
        build_write_message(
            1, sub_index=0, payload=bytes(6), request_id=1, can_fd=False
        )
    for flags in (
        {"is_error_frame": True},
        {"is_remote_frame": True},
        {"is_fd": True},
        {"is_extended_id": True},
    ):
        msg = can.Message(arbitration_id=0x401, data=b"\x01\x00\x01\x01", **flags)
        assert matching_response_frame(msg, 0x401) is None


def test_unordered_duplicate_and_conflicting_classic_fragments():
    assembler = HOPRangeAssembler(
        1, sub_index=0, length=25, rts=False, response_id=0x401
    )
    wire = encode_v25(tuple(range(20)))

    def frame(si, payload):
        return can.Message(
            arbitration_id=0x401,
            data=bytes((1, si, len(payload))) + payload,
            is_extended_id=False,
        )

    for si in (20, 5, 5, 15, 0):
        assert assembler.consume(frame(si, wire[si : si + 5]))[1] is None
    _, result = assembler.consume(frame(10, wire[10:15]))
    assert result == wire
    with pytest.raises(ProtocolError, match="conflicts"):
        assembler.consume(frame(0, b"\xff" + wire[1:5]))


@pytest.mark.parametrize(
    "semantic",
    [CommandSemantic.ANGLES, CommandSemantic.SPEEDS, CommandSemantic.CURRENT_LIMITS],
)
def test_write_chain_publishes_only_complete_feedback(semantic):
    caps = get_declared_capabilities("O30", side="right", transport_kind="can")
    t = O30ProtocolBackend(HandSide.RIGHT, can_fd=False).command_transaction(
        semantic, (17,) * 20, caps
    )
    for si in range(0, 25, 5):
        m = t.request_messages[0]
        assert m.data[1] == si and len(m.data) == 8
        r = t.consume(
            can.Message(arbitration_id=0x401, data=m.data, is_extended_id=False)
        )
        assert r.complete and r.acknowledged
        assert (r.result is not None) == (
            si == 20 and semantic is CommandSemantic.ANGLES
        )
        t = t.build_next_transaction()
    assert t is None


@pytest.mark.parametrize("failure", ["drop_write_si", "error_write_si"])
def test_middle_stage_failure_stops_sends_and_does_not_publish_partial_vector(failure):
    hand, transport = make_hand(**{failure: 10})
    try:
        hand.open()
        initial = hand.get_snapshot().angles
        handle = hand.set_angles([17] * 20)
        assert handle.wait(timeout=2) is CommandStage.FAILED
        writes = [
            m for m in transport.requests if len(m.data) > 3 and m.data[0] & 0x7F == 1
        ]
        assert [m.data[1] for m in writes] == [0, 5, 10]
        assert hand.get_snapshot().angles.raw == initial.raw
        assert handle.error is not None
    finally:
        hand.close()


@pytest.mark.parametrize("sensor", ["NO_SENSOR", "TASHAN_GATHER", "TSSP_JZG"])
def test_classic_lifecycle_whole_hand_write_and_sensor_fragments(sensor):
    hand, _transport = make_hand(O30DeviceScript(sensor_variant=sensor))
    try:
        hand.open()
        assert hand.get_angles(timeout=1).raw == tuple(range(20))
        assert hand.set_angles([17] * 20).wait(timeout=2) is CommandStage.ACKNOWLEDGED
        assert hand.get_angles(timeout=1).raw == (17,) * 20
        assert hand.set_speeds([40] * 20).wait(timeout=2) is CommandStage.ACKNOWLEDGED
        assert hand.get_configured_speeds(timeout=1).raw == (40,) * 20
        if sensor == "NO_SENSOR":
            with pytest.raises(UnsupportedFirmwareError):
                hand.get_force(timeout=2)
        else:
            force = hand.get_force("fn", timeout=3)
            assert force.fn is not None
            assert len(force.fn.raw) == 5
            assert len(force.fn.raw[1]) == (1 if sensor == "TASHAN_GATHER" else 70)
        hand.close()
        hand.open()
        assert hand.get_angles(timeout=1).raw == (17,) * 20
    finally:
        hand.close()


def test_classic_absent_first_and_last_fingers_do_not_hide_installed_sensors():
    from .test_o30_tactile_availability import MixedSensors

    script = MixedSensors(sensor_variant="TSSP_JZG")
    script.absent = {1, 5}
    script.selected_sensor = 5
    hand, _ = make_hand(script)
    try:
        hand.open()
        force = hand.get_force("fn", timeout=3)
        assert force.fn.raw[0] is None and force.fn.raw[4] is None
        assert len(force.fn.raw[1]) == 70
    finally:
        hand.close()


def test_unrelated_fault_is_not_proof_that_tactile_is_unsupported():
    class NoSelector(O30DeviceScript):
        def _write(self, main_index, sub_index, length, payload, *, rts=False):
            if main_index == 0x31 and sub_index == 0x6E:
                return (self._response(0x0C, 0, bytes(5)),)
            return super()._write(main_index, sub_index, length, payload, rts=rts)

    hand, _ = make_hand(NoSelector(application_version="1.0.0"))
    try:
        hand.open()
        from linkerbot_hand import TimeoutError

        with pytest.raises(TimeoutError):
            hand.get_force(timeout=2)
        assert hand.get_capabilities().telemetry_supported(TelemetrySemantic.FORCE)
        assert hand.get_health().connected
        assert hand.get_angles(timeout=1).raw == tuple(range(20))
    finally:
        hand.close()


def test_classic_no_sensor_repeated_type_reply_is_unsupported():
    class RepeatType(O30DeviceScript):
        def _write(self, main_index, sub_index, length, payload, *, rts=False):
            if main_index == 0x31 and sub_index == 0x6E:
                return (self._response(0x31, 0, self._sensor_object(1)[:32]),)
            return super()._write(main_index, sub_index, length, payload, rts=rts)

    hand, _ = make_hand(RepeatType(application_version="1.0.0"))
    try:
        hand.open()
        with pytest.raises(UnsupportedFirmwareError):
            hand.get_force(timeout=2)
        assert hand.get_health().connected
    finally:
        hand.close()


@pytest.mark.parametrize("firmware", ["1.0.0", "1.2.0"])
def test_late_fault_does_not_hide_four_installed_fingertips(firmware):
    from .test_o30_tactile_availability import MixedSensors

    class LateFault(MixedSensors):
        absent = frozenset({1})

        def _write(self, main_index, sub_index, length, payload, *, rts=False):
            replies = super()._write(main_index, sub_index, length, payload, rts=rts)
            if main_index == 0x31 and sub_index == 0x6E and payload == b"\x01":
                return (self._response(0x0C, 20, bytes(5)),) + replies
            return replies

    hand, _ = make_hand(
        LateFault(sensor_variant="TSSP_JZG", application_version=firmware)
    )
    try:
        hand.open()
        force = hand.get_force("fn", timeout=2)
        assert force.fn.raw[0] is None
        assert all(len(values) == 70 for values in force.fn.raw[1:])
        assert hand.get_capabilities().telemetry_supported(TelemetrySemantic.FORCE)
    finally:
        hand.close()


def test_serialized_reads_support_firmware_without_burst_reply_capacity():
    class NoBurstReplies(O30DeviceScript):
        def _read(self, main_index, sub_index, length, *, rts):
            if length > 5:
                return ()
            return super()._read(main_index, sub_index, length, rts=rts)

    hand, transport = make_hand(NoBurstReplies(sensor_variant="TASHAN_GATHER"))
    try:
        hand.open()
        assert hand.get_angles(timeout=1).raw == tuple(range(20))
        assert hand.get_force("fn", timeout=3).fn is not None
        requests = [m for m in transport.requests if len(m.data) == 3]
        assert requests and all(1 <= m.data[2] <= 5 for m in requests)
    finally:
        hand.close()


def test_serialized_read_ignores_future_ranges_and_publishes_only_full_vector():
    caps = get_declared_capabilities("O30", side="right", transport_kind="can")
    tx = O30ProtocolBackend(HandSide.RIGHT, can_fd=False).read_transaction(
        TelemetrySemantic.ANGLES, caps
    )
    wire = encode_v25(tuple(range(20)))

    def frame(start, payload):
        return can.Message(
            arbitration_id=0x401,
            data=bytes((1, start, len(payload))) + payload,
            is_extended_id=False,
        )

    assert not tx.consume(frame(20, wire[20:25])).matched
    for start in range(0, 25, 5):
        assert bytes(tx.request_messages[0].data) == bytes((1, start, 5))
        # One requested range can itself arrive in reordered small fragments.
        partial = tx.consume(frame(start + 2, wire[start + 2 : start + 5]))
        assert partial.matched and not partial.complete and partial.result is None
        assert not tx.consume(frame(start, wire[start : start + 1])).complete
        progress = tx.consume(frame(start + 1, wire[start + 1 : start + 2]))
        assert progress.complete
        if start == 20:
            assert progress.result.raw == tuple(range(20))
        else:
            assert progress.result is None
        tx = tx.build_next_transaction()
    assert tx is None


def test_missing_middle_read_reply_stops_later_ranges_and_keeps_snapshot():
    class MissingRange(O30DeviceScript):
        drop = False

        def _read(self, main_index, sub_index, length, *, rts):
            if self.drop and main_index == 1 and sub_index == 10:
                return ()
            return super()._read(main_index, sub_index, length, rts=rts)

    script = MissingRange()
    hand, transport = make_hand(script)
    try:
        hand.open()
        initial = hand.get_snapshot().angles
        transport.requests.clear()
        script.drop = True
        from linkerbot_hand import TimeoutError

        with pytest.raises(TimeoutError):
            hand.get_angles(timeout=1)
        assert [m.data[1] for m in transport.requests] == [0, 5, 10]
        assert hand.get_snapshot().angles.meta.sequence == initial.meta.sequence
        assert hand.get_snapshot().angles.raw == initial.raw
    finally:
        hand.close()


def test_fd_read_retains_single_request():
    caps = get_declared_capabilities("O30", side="right")
    tx = O30ProtocolBackend(HandSide.RIGHT).read_transaction(
        TelemetrySemantic.ANGLES, caps
    )
    assert bytes(tx.request_messages[0].data) == bytes((1, 0, 25))
    assert tx.build_next_transaction() is None
