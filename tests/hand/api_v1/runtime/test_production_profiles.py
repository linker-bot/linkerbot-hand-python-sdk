"""End-to-end runtime tests using production profiles and CAN messages."""

from __future__ import annotations

import time

import can
import pytest

from linkerbot_hand import TelemetrySemantic
from linkerbot_hand.api import (
    AngleFeedbackPolicy,
    CommandStage,
    DataOrigin,
    DeviceConfig,
    DeviceNotReadyError,
    ErrorCode,
    HandDevice,
    HandSide,
    IdentityMismatchError,
    NormalForceResultantSample,
    PollingPlan,
    ProtocolError,
    TimeoutError,
    TransportError,
    UnsupportedFirmwareError,
    ValidationError,
)
from linkerbot_hand.protocols.o30.framing import addressing_for_side

from .classic_can_fake import (
    ScriptedClassicCANTransport,
    make_script,
    wait_until,
)
from .l30_canfd_fake import L30DeviceScript, ScriptedL30Transport
from .o20_canfd_fake import O20DeviceScript, ScriptedO20Transport
from .o30_canfd_fake import O30DeviceScript, ScriptedO30Transport


def _make_hand(
    model: str,
    side: HandSide,
    *,
    config: DeviceConfig | None = None,
) -> tuple[HandDevice, ScriptedClassicCANTransport]:
    script = make_script(model, side)
    transport = ScriptedClassicCANTransport(script)
    hand = HandDevice(
        model=model,
        side=side.value,
        transport=transport,
        config=config,
    )
    return hand, transport


def _make_o20_hand(
    side: HandSide,
) -> tuple[HandDevice, ScriptedO20Transport]:
    script = O20DeviceScript(side)
    transport = ScriptedO20Transport(script)
    hand = HandDevice(model="O20", side=side.value, transport=transport)
    return hand, transport


def _make_l30_hand() -> tuple[HandDevice, ScriptedL30Transport]:
    script = L30DeviceScript()
    transport = ScriptedL30Transport(script)
    hand = HandDevice(model="L30", transport=transport)
    return hand, transport


def _make_o30_hand(
    side: HandSide = HandSide.RIGHT,
) -> tuple[HandDevice, ScriptedO30Transport]:
    addressing = addressing_for_side(side)
    script = O30DeviceScript(
        reported_side=side,
        request_id=addressing.request_id,
        response_id=addressing.response_id,
    )
    transport = ScriptedO30Transport(script)
    hand = HandDevice(model="O30", side=side.value, transport=transport)
    return hand, transport


def test_default_l6_profile_opens_and_controls_raw_and_percent_angles() -> None:
    hand, transport = _make_hand("L6", HandSide.LEFT)
    assert transport.dispatchers == []

    hand.open()
    try:
        capabilities = hand.get_capabilities()
        snapshot = hand.get_snapshot()
        assert capabilities.identity.verified
        assert capabilities.identity.firmware == "2.4.0"
        assert snapshot.device_info is not None
        assert snapshot.device_info.product_code == "LHL6-04-077-L-B-1-A"
        assert snapshot.angles is not None
        assert snapshot.angles.raw == (10, 20, 30, 40, 50, 60)
        assert snapshot.faults is not None and snapshot.faults.meta.valid
        assert snapshot.current_limits is not None
        assert snapshot.current_limits.raw == (80,) * 6

        raw = hand.set_angles((1, 2, 3, 4, 5, 6))
        assert raw.wait(timeout=1) is CommandStage.DISPATCHED
        percent = hand.set_percent_angles((0, 50, 100, 25, 75, 100))
        assert percent.wait(timeout=1) is CommandStage.DISPATCHED

        assert transport.script.command_payloads[-2:] == [
            b"\x01\x01\x02\x03\x04\x05\x06",
            b"\x01\x00\x80\xff\x40\xbf\xff",
        ]
    finally:
        hand.close()


def test_l6_configured_speed_and_limits_use_device_confirmed_values() -> None:
    hand, transport = _make_hand("L6", HandSide.LEFT)
    hand.open()
    try:
        speed = hand.set_speeds((40, 41, 42, 43, 44, 45))
        assert speed.wait(timeout=1) is CommandStage.ACKNOWLEDGED

        configured = hand.get_configured_speeds(timeout=0.1)

        assert configured.raw == (40, 41, 42, 43, 44, 45)
        assert configured.meta.origin is DataOrigin.DEVICE_CONFIG_RESPONSE
        assert transport.script.read_counts[0x05] == 0

        limits = hand.set_current_limits((70,) * 6)
        assert limits.wait(timeout=1) is CommandStage.ACKNOWLEDGED
        queried = hand.get_current_limits(timeout=0.2)
        assert queried.raw == (70,) * 6
        assert queried.meta.origin is DataOrigin.DEVICE_CONFIG_RESPONSE
        assert transport.script.read_counts[0x02] >= 2
    finally:
        hand.close()


def test_l6_latest_management_queries_and_raw_writes_use_verified_protocol() -> None:
    hand, transport = _make_hand("L6", HandSide.LEFT)
    hand.open()
    try:
        assert hand.get_stall_detection_times(timeout=0.2).raw == (5,) * 6
        assert hand.get_stall_thresholds(timeout=0.2).raw == (91,) * 6
        assert hand.get_stall_hold_currents(timeout=0.2).raw == (106,) * 6
        assert hand.get_limit_compensations(timeout=0.2).raw == (
            0,
            0,
            227,
            223,
            208,
            207,
        )

        cases = (
            (hand.set_stall_detection_times, 0xC5, (6,) * 6),
            (hand.set_stall_thresholds, 0xC6, (92,) * 6),
            (hand.set_stall_hold_currents, 0xC7, (107,) * 6),
        )
        for setter, opcode, values in cases:
            handle = setter(values)
            assert handle.wait(timeout=1) is CommandStage.ACKNOWLEDGED
            assert transport.script.command_payloads[-1] == bytes((opcode, *values))

        compensation = hand.set_limit_compensations((1, 2, 3, 4, 5, 6))
        assert compensation.wait(timeout=1) is CommandStage.ACKNOWLEDGED
        assert transport.script.command_payloads[-1] == (
            b"\x38\x01\x02\x03\x04\x05\x06"
        )
        cached = hand.get_snapshot().limit_compensations
        assert cached is not None and cached.raw == (1, 2, 3, 4, 5, 6)

        save = hand.save_parameters()
        assert save.wait(timeout=1) is CommandStage.ACKNOWLEDGED
        assert transport.script.command_payloads[-1] == bytes((0xCF,)) * 8
    finally:
        hand.close()


def test_l6_zero_stall_detection_time_is_rejected_before_transport() -> None:
    hand, transport = _make_hand("L6", HandSide.LEFT)
    hand.open()
    try:
        sent_before = tuple(transport.script.sent_payloads)

        with pytest.raises(ValidationError, match="outside the declared range"):
            hand.set_stall_detection_times((0,) * 6)

        assert tuple(transport.script.sent_payloads) == sent_before
    finally:
        hand.close()


def test_l6_bad_stall_echo_fails_command_without_disconnect() -> None:
    hand, transport = _make_hand("L6", HandSide.LEFT)
    transport.script.bad_command_echoes[0xC5] = (5,) * 6
    hand.open()
    try:
        handle = hand.set_stall_detection_times((6,) * 6)

        assert handle.wait(timeout=1) is CommandStage.FAILED
        assert handle.error is not None
        assert "did not echo" in handle.error.message
        assert hand.get_health().connected
    finally:
        hand.close()


def test_l6_limit_compensation_bad_echo_fails_without_disconnect() -> None:
    hand, transport = _make_hand("L6", HandSide.LEFT)
    transport.script.bad_command_echoes[0x38] = (0,) * 6
    hand.open()
    try:
        handle = hand.set_limit_compensations((1, 2, 3, 4, 5, 6))

        assert handle.wait(timeout=1) is CommandStage.FAILED
        assert handle.error is not None
        assert "did not echo" in handle.error.message
        assert transport.script.command_payloads[-1] == (
            b"\x38\x01\x02\x03\x04\x05\x06"
        )
        assert hand.get_health().connected
    finally:
        hand.close()


def test_l6_optional_current_query_degrades_without_disconnecting() -> None:
    hand, transport = _make_hand("L6", HandSide.LEFT)
    transport.script.firmware = (2, 3, 3)
    transport.script.unsupported_queries.add(0x36)
    hand.open()
    try:
        hand.start_polling(PollingPlan(angles_hz=50, current_hz=50))

        wait_until(
            lambda: bool(hand.get_capabilities().firmware_unsupported_telemetry),
            timeout_s=1,
        )
        current_count = transport.script.read_counts[0x36]
        angle_count = transport.script.read_counts[0x01]
        time.sleep(0.08)

        capabilities = hand.get_capabilities()
        health = hand.get_health()
        assert capabilities.identity.firmware == "2.3.3"
        assert capabilities.firmware_unsupported_telemetry == (
            TelemetrySemantic.CURRENTS,
        )
        assert health.connected
        assert health.recent_error is not None
        assert health.recent_error.error_type == "UnsupportedFirmwareError"
        current_health = next(
            channel for channel in health.channels if channel.channel == "currents"
        )
        assert current_health.requested_hz == 0
        assert current_health.timeouts == 3
        assert transport.script.read_counts[0x36] == current_count == 3
        assert transport.script.read_counts[0x01] > angle_count

        with pytest.raises(UnsupportedFirmwareError, match="currents"):
            hand.get_currents(timeout=0.2)
        assert transport.script.read_counts[0x36] == current_count
    finally:
        hand.stop_polling()
        hand.close()


@pytest.mark.parametrize("response", (b"\x36", b"\x36\x01"))
def test_l6_malformed_optional_current_reply_degrades_after_three_failures(
    response: bytes,
    caplog: pytest.LogCaptureFixture,
) -> None:
    hand, transport = _make_hand("L6", HandSide.LEFT)
    transport.script.malformed_query_responses[0x36] = response
    hand.open()
    try:
        with caplog.at_level("WARNING", logger="linkerbot_hand.runtime._device_actor"):
            with pytest.raises(ProtocolError):
                hand.get_currents(timeout=0.2)
            with pytest.raises(ProtocolError):
                hand.get_currents(timeout=0.2)
            with pytest.raises(UnsupportedFirmwareError, match="currents"):
                hand.get_currents(timeout=0.2)
            with pytest.raises(UnsupportedFirmwareError, match="currents"):
                hand.get_currents(timeout=0.2)

        assert transport.script.read_counts[0x36] == 3
        warnings = [
            record
            for record in caplog.records
            if "disabling this query" in record.getMessage()
        ]
        assert len(warnings) == 1
        assert hand.get_health().connected
    finally:
        hand.close()


def test_successful_optional_read_confirms_support_for_connection_generation() -> None:
    hand, transport = _make_hand("L6", HandSide.LEFT)
    hand.open()
    try:
        transport.script.unsupported_queries.add(0x36)
        for _ in range(2):
            with pytest.raises(TimeoutError):
                hand.get_currents(timeout=0.2)

        transport.script.unsupported_queries.clear()
        assert hand.get_currents(timeout=0.2).raw == (1, 2, 3, 4, 5, 6)

        transport.script.unsupported_queries.add(0x36)
        for _ in range(3):
            with pytest.raises(TimeoutError):
                hand.get_currents(timeout=0.2)
        assert not hand.get_capabilities().firmware_unsupported_telemetry

        transport.script.unsupported_queries.clear()
        assert hand.get_currents(timeout=0.2).raw == (1, 2, 3, 4, 5, 6)
        assert transport.script.read_counts[0x36] == 7
    finally:
        hand.close()


def test_verified_optional_read_is_not_later_downgraded_to_unsupported() -> None:
    hand, transport = _make_hand("O6", HandSide.RIGHT)
    hand.open()
    try:
        assert len(hand.get_force(timeout=0.3).tactile) == 5

        transport.script.unsupported_queries.add(0xA4)
        for _ in range(3):
            with pytest.raises(TimeoutError):
                hand.get_force(timeout=0.3)

        assert not hand.get_capabilities().firmware_unsupported_telemetry

        transport.script.unsupported_queries.clear()
        assert len(hand.get_force(timeout=0.3).tactile) == 5
        assert transport.script.read_counts[0xA4] == 13
    finally:
        hand.close()


def test_recovery_rechecks_previously_verified_optional_read() -> None:
    hand, transport = _make_hand("L6", HandSide.LEFT)
    hand.open()
    try:
        assert hand.get_currents(timeout=0.2).raw == (1, 2, 3, 4, 5, 6)
        generation = hand.get_health().connection_generation

        transport.script.unsupported_queries.add(0x36)
        hand.recover(timeout=1)

        assert hand.get_health().connection_generation == generation + 1
        with pytest.raises(TimeoutError):
            hand.get_currents(timeout=0.2)
        with pytest.raises(TimeoutError):
            hand.get_currents(timeout=0.2)
        with pytest.raises(UnsupportedFirmwareError, match="currents"):
            hand.get_currents(timeout=0.2)
        assert hand.get_capabilities().firmware_unsupported_telemetry == (
            TelemetrySemantic.CURRENTS,
        )
    finally:
        hand.close()


def test_l6_optional_query_transport_failure_disconnects_instead_of_degrading() -> None:
    hand, transport = _make_hand(
        "L6",
        HandSide.LEFT,
        config=DeviceConfig(auto_reconnect=False),
    )
    hand.open()
    try:
        transport.current_dispatcher.send_error = OSError("simulated bus write failure")

        with pytest.raises(TransportError, match="batch did not dispatch"):
            hand.get_currents(timeout=0.2)

        wait_until(lambda: not hand.get_health().connected)
        assert not hand.get_capabilities().firmware_unsupported_telemetry
        assert transport.script.read_counts[0x36] == 0
    finally:
        hand.close()


def test_l6_recovery_reprobes_runtime_firmware_capabilities() -> None:
    hand, transport = _make_hand("L6", HandSide.LEFT)
    transport.script.unsupported_queries.add(0x36)
    hand.open()
    try:
        hand.start_polling(PollingPlan(current_hz=50))
        wait_until(
            lambda: (
                TelemetrySemantic.CURRENTS
                in hand.get_capabilities().firmware_unsupported_telemetry
            ),
            timeout_s=1,
        )

        transport.script.unsupported_queries.clear()
        reads_before_recovery = transport.script.read_counts[0x36]
        hand.recover(timeout=1)

        def recovered_current_sample_is_valid() -> bool:
            currents = hand.get_snapshot().currents
            return (
                transport.script.read_counts[0x36] > reads_before_recovery
                and currents is not None
                and currents.meta.valid
            )

        wait_until(
            recovered_current_sample_is_valid,
            timeout_s=1,
        )

        assert not hand.get_capabilities().firmware_unsupported_telemetry
        currents = hand.get_snapshot().currents
        assert currents is not None
        assert currents.meta.valid
    finally:
        hand.stop_polling()
        hand.close()


def test_o6_configuration_reads_are_device_responses_not_measurements() -> None:
    hand, transport = _make_hand("O6", HandSide.RIGHT)
    hand.open()
    try:
        assert hand.get_capabilities().identity.sensor_variant == "B"
        cases = (
            (
                hand.set_current_limits,
                hand.get_current_limits,
                (61,) * 6,
                0x02,
            ),
            (
                hand.set_speeds,
                hand.get_configured_speeds,
                (62,) * 6,
                0x05,
            ),
            (
                hand.set_accelerations,
                hand.get_configured_accelerations,
                (63,) * 6,
                0x87,
            ),
        )
        for setter, getter, values, opcode in cases:
            handle = setter(values)
            assert handle.wait(timeout=1) is CommandStage.ACKNOWLEDGED
            sample = getter(timeout=0.2)
            assert sample.raw == values
            assert sample.meta.origin is DataOrigin.DEVICE_CONFIG_RESPONSE
            assert transport.script.read_counts[opcode] >= 1
    finally:
        hand.close()


def test_o6_stall_management_reads_writes_and_saves_parameters() -> None:
    hand, transport = _make_hand("O6", HandSide.RIGHT)
    hand.open()
    try:
        assert hand.get_stall_thresholds(timeout=0.2).raw == (128,) * 6
        assert hand.get_stall_detection_times(timeout=0.2).raw == (5,) * 6
        assert hand.get_stall_hold_currents(timeout=0.2).raw == (118,) * 6

        cases = (
            (hand.set_stall_thresholds, 0xC5, (129,) * 6),
            (hand.set_stall_detection_times, 0xC6, (6,) * 6),
            (hand.set_stall_hold_currents, 0xC7, (119,) * 6),
        )
        for setter, opcode, values in cases:
            handle = setter(values)
            assert handle.wait(timeout=1) is CommandStage.ACKNOWLEDGED
            assert transport.script.command_payloads[-1] == bytes((opcode, *values))

        save = hand.save_parameters()
        assert save.wait(timeout=1) is CommandStage.ACKNOWLEDGED
        assert transport.script.command_payloads[-1] == bytes((0xCF,)) * 8
    finally:
        hand.close()


def test_o6_bad_configuration_echo_fails_without_disconnecting() -> None:
    hand, transport = _make_hand("O6", HandSide.RIGHT)
    transport.script.bad_command_echoes[0xC5] = (128,) * 6
    hand.open()
    try:
        handle = hand.set_stall_thresholds((129,) * 6)

        assert handle.wait(timeout=1) is CommandStage.FAILED
        assert handle.error is not None
        assert "did not echo" in handle.error.message
        assert hand.get_health().connected
    finally:
        hand.close()


def test_o6_configuration_timeout_fails_without_firmware_read_degradation() -> None:
    hand, transport = _make_hand("O6", HandSide.RIGHT)
    transport.script.dropped_command_responses.add(0xC5)
    hand.open()
    try:
        handle = hand.set_stall_thresholds((129,) * 6)

        assert handle.wait(timeout=1) is CommandStage.FAILED
        assert handle.error is not None
        assert handle.error.code is ErrorCode.TIMEOUT
        assert hand.get_health().connected
        assert not hand.get_capabilities().firmware_unsupported_telemetry
    finally:
        hand.close()


def test_o6_optional_stall_query_degrades_without_disconnecting() -> None:
    hand, transport = _make_hand("O6", HandSide.RIGHT)
    transport.script.unsupported_queries.add(0xC5)
    hand.open()
    try:
        with pytest.raises(TimeoutError):
            hand.get_stall_thresholds(timeout=0.2)
        with pytest.raises(TimeoutError):
            hand.get_stall_thresholds(timeout=0.2)
        with pytest.raises(UnsupportedFirmwareError, match="stall_thresholds"):
            hand.get_stall_thresholds(timeout=0.2)

        assert hand.get_health().connected
        assert hand.get_capabilities().firmware_unsupported_telemetry == (
            TelemetrySemantic.STALL_THRESHOLDS,
        )
        with pytest.raises(UnsupportedFirmwareError, match="stall_thresholds"):
            hand.get_stall_thresholds(timeout=0.2)
        assert transport.script.read_counts[0xC5] == 3
    finally:
        hand.close()


def test_o6_optional_tactile_query_degrades_without_disconnecting() -> None:
    hand, transport = _make_hand("O6", HandSide.RIGHT)
    transport.script.unsupported_queries.add(0xA4)
    hand.open()
    try:
        with pytest.raises(TimeoutError):
            hand.get_force(timeout=0.3)
        with pytest.raises(TimeoutError):
            hand.get_force(timeout=0.3)
        with pytest.raises(UnsupportedFirmwareError, match="force"):
            hand.get_force(timeout=0.3)

        assert hand.get_health().connected
        assert hand.get_capabilities().firmware_unsupported_telemetry == (
            TelemetrySemantic.FORCE,
        )
        with pytest.raises(UnsupportedFirmwareError, match="force"):
            hand.get_force(timeout=0.3)
        assert transport.script.read_counts[0xA4] == 3
    finally:
        hand.close()


def test_o6_report_updates_cache_without_incrementing_completed_reads() -> None:
    hand, transport = _make_hand("O6", HandSide.RIGHT)
    hand.open()
    try:
        hand.start_polling(PollingPlan(angles_hz=0.1))

        def first_read_completed() -> bool:
            angles = next(
                item for item in hand.get_health().channels if item.channel == "angles"
            )
            return angles.completed_reads == 1

        wait_until(first_read_completed)
        before = next(
            item for item in hand.get_health().channels if item.channel == "angles"
        )
        transport.emit(
            can.Message(
                arbitration_id=transport.script.arbitration_id,
                data=b"\x01\x0b\x15\x1f\x29\x33\x3d",
                is_extended_id=False,
            )
        )

        def report_published() -> bool:
            angles = hand.get_snapshot().angles
            return (
                angles is not None and angles.meta.sequence > before.data_age.sequence
            )

        wait_until(report_published)

        after = next(
            item for item in hand.get_health().channels if item.channel == "angles"
        )
        assert after.completed_reads == 1
        assert after.data_age.sequence == before.data_age.sequence + 1
        assert after.data_age.origin is DataOrigin.DEVICE_REPORT
    finally:
        hand.stop_polling()
        hand.close()


def test_o6_complete_tactile_transaction_counts_as_one_read() -> None:
    hand, transport = _make_hand("O6", HandSide.RIGHT)
    hand.open()
    try:
        hand.start_polling(PollingPlan(force_hz=0.1))

        def first_read_completed() -> bool:
            force = next(
                item for item in hand.get_health().channels if item.channel == "force"
            )
            return force.completed_reads == 1

        wait_until(first_read_completed)
        force = next(
            item for item in hand.get_health().channels if item.channel == "force"
        )

        assert force.completed_reads == 1
        assert force.data_age.sequence == 1
        assert force.data_age.origin is DataOrigin.DEVICE_RESPONSE
        assert transport.script.read_counts[0xA4] == 5
    finally:
        hand.stop_polling()
        hand.close()


@pytest.mark.parametrize(
    ("model", "side", "force_hz", "rows", "columns", "selector"),
    (
        ("L6", HandSide.LEFT, 5, 12, 6, 0xC6),
        ("O6", HandSide.RIGHT, 20, 10, 4, 0xA4),
        ("L20Lite", HandSide.LEFT, 10, 12, 6, 0xC6),
        ("L20", HandSide.RIGHT, 10, 12, 6, 0xC6),
    ),
)
def test_angle_and_full_tactile_polling_publish_complete_samples(
    model: str,
    side: HandSide,
    force_hz: float,
    rows: int,
    columns: int,
    selector: int,
) -> None:
    hand, transport = _make_hand(model, side)
    hand.open()
    try:
        initial_angles = hand.get_snapshot().angles
        assert initial_angles is not None
        initial_angle_sequence = initial_angles.meta.sequence
        hand.start_polling(PollingPlan(angles_hz=50, force_hz=force_hz))

        def polling_ready() -> bool:
            snapshot = hand.get_snapshot()
            angles = snapshot.angles
            force = snapshot.force
            return (
                angles is not None
                and angles.meta.sequence > initial_angle_sequence
                and force is not None
                and force.meta.valid
            )

        wait_until(polling_ready, timeout_s=1)
        hand.stop_polling()

        snapshot = hand.get_snapshot()
        assert snapshot.force is not None
        assert len(snapshot.force.tactile) == 5
        assert all(
            (matrix.rows, matrix.columns) == (rows, columns)
            for matrix in snapshot.force.tactile
        )
        assert snapshot.force.tactile[0].raw[0] == tuple(range(columns))
        assert transport.script.read_counts[selector] >= 5
        angle_opcode = 0x41 if model == "L20" else 0x01
        assert transport.script.read_counts[angle_opcode] >= 2

        counts_after_stop = transport.script.read_counts.copy()
        time.sleep(0.05)
        assert transport.script.read_counts == counts_after_stop
    finally:
        hand.close()


def test_l6_tactile_yields_between_complete_fingers_for_one_angle_target() -> None:
    hand, transport = _make_hand("L6", HandSide.LEFT)
    original_send = transport.send
    submitted = False
    angle_handle = None

    def send(message: can.Message):
        nonlocal submitted, angle_handle
        if bytes(message.data) == b"\xb1\xc6" and not submitted:
            submitted = True
            angle_handle = hand.set_angles((42,) * 6)
        return original_send(message)

    transport.send = send  # ty: ignore[invalid-assignment]
    hand.open()
    try:
        force = hand.get_force(timeout=1)

        assert angle_handle is not None
        assert angle_handle.wait(timeout=1) is CommandStage.DISPATCHED
        payloads = transport.script.sent_payloads
        first_finger = payloads.index(b"\xb1\xc6")
        second_finger = payloads.index(b"\xb2\xc6", first_finger + 1)
        angle_command = payloads.index(bytes((0x01, 42, 42, 42, 42, 42, 42)))
        assert first_finger < angle_command < second_finger
        assert len(force.tactile) == 5
    finally:
        transport.send = original_send  # ty: ignore[invalid-assignment]
        hand.close()


def test_o6_tactile_yields_for_due_angle_command_and_poll_between_fingers() -> None:
    hand, transport = _make_hand("O6", HandSide.RIGHT)
    original_send = transport.send
    polling_started = False
    angle_handle = None

    def send(message: can.Message):
        nonlocal polling_started, angle_handle
        if bytes(message.data) == b"\xb1\xa4" and not polling_started:
            polling_started = True
            hand.start_polling(
                PollingPlan(
                    angles_hz=100,
                    angle_feedback=AngleFeedbackPolicy.QUERY_ONLY,
                )
            )
            angle_handle = hand.set_angles((43,) * 6)
        return original_send(message)

    transport.send = send  # ty: ignore[invalid-assignment]
    hand.open()
    try:
        force = hand.get_force(timeout=1)

        payloads = transport.script.sent_payloads
        first_finger = payloads.index(b"\xb1\xa4")
        second_finger = payloads.index(b"\xb2\xa4", first_finger + 1)
        angle_query = payloads.index(b"\x01", first_finger + 1)
        angle_command = payloads.index(bytes((0x01, 43, 43, 43, 43, 43, 43)))
        assert angle_handle is not None
        assert angle_handle.wait(timeout=1) is CommandStage.DISPATCHED
        assert first_finger < angle_command < second_finger
        assert first_finger < angle_query < second_finger
        assert len(force.tactile) == 5
    finally:
        if polling_started:
            hand.stop_polling()
        transport.send = original_send  # ty: ignore[invalid-assignment]
        hand.close()


def test_o6_tactile_waits_at_boundary_for_imminent_rate_limited_target() -> None:
    hand, transport = _make_hand(
        "O6",
        HandSide.RIGHT,
        config=DeviceConfig(command_rate_hz=500),
    )
    first = None
    second = None
    original_send = transport.send

    def send(message: can.Message):
        nonlocal second
        if bytes(message.data) == b"\xb1\xa4" and second is None:
            second = hand.set_angles((45,) * 6)
        return original_send(message)

    transport.send = send  # ty: ignore[invalid-assignment]
    hand.open()
    try:
        first = hand.set_angles((44,) * 6)
        assert first.wait(timeout=1) is CommandStage.DISPATCHED

        force = hand.get_force(timeout=1)

        assert second is not None
        assert second.wait(timeout=1) is CommandStage.DISPATCHED
        payloads = transport.script.sent_payloads
        first_finger = payloads.index(b"\xb1\xa4")
        second_finger = payloads.index(b"\xb2\xa4", first_finger + 1)
        second_angle = payloads.index(bytes((0x01, 45, 45, 45, 45, 45, 45)))
        assert first_finger < second_angle < second_finger
        assert len(force.tactile) == 5
    finally:
        transport.send = original_send  # ty: ignore[invalid-assignment]
        hand.close()


@pytest.mark.parametrize(
    ("model", "side", "joint_count", "expected_angles"),
    (
        ("L20Lite", HandSide.LEFT, 10, tuple(range(10, 20))),
        ("L20", HandSide.RIGHT, 16, tuple(range(10, 26))),
    ),
)
def test_extended_classic_profiles_open_and_control_multi_frame_objects(
    model: str,
    side: HandSide,
    joint_count: int,
    expected_angles: tuple[int, ...],
) -> None:
    hand, transport = _make_hand(model, side)
    hand.open()
    try:
        capabilities = hand.get_capabilities()
        snapshot = hand.get_snapshot()
        assert capabilities.identity.verified
        assert len(capabilities.joints) == joint_count
        assert snapshot.angles is not None
        assert snapshot.angles.raw == expected_angles
        assert snapshot.faults is not None and snapshot.faults.meta.valid
        assert snapshot.effort_limits is not None
        assert snapshot.effort_limits.raw == (80,) * joint_count

        values = tuple(range(joint_count))
        angles = hand.set_angles(values)
        assert angles.wait(timeout=1) is CommandStage.DISPATCHED
        speeds = hand.set_speeds((70,) * joint_count)
        assert speeds.wait(timeout=1) is CommandStage.DISPATCHED
        configured = hand.get_configured_speeds(timeout=0.2)

        assert configured.raw == (70,) * joint_count
        assert configured.meta.origin is DataOrigin.DEVICE_CONFIG_RESPONSE
        assert sum(
            1
            for payload in transport.script.command_payloads
            if payload[0] in ({0x01, 0x04} if model == "L20Lite" else range(0x41, 0x46))
        ) == (2 if model == "L20Lite" else 5)
    finally:
        hand.close()


def test_o20_profile_exposes_complete_raw_control_and_telemetry() -> None:
    hand, transport = _make_o20_hand(HandSide.RIGHT)
    assert transport.dispatchers == []

    hand.open()
    try:
        capabilities = hand.get_capabilities()
        snapshot = hand.get_snapshot()
        assert capabilities.identity.verified
        assert capabilities.identity.hardware_revision == "H1.3"
        assert snapshot.device_info is not None
        assert snapshot.device_info.serial_number == "SN-O20-0001"
        assert snapshot.angles is not None
        assert snapshot.angles.raw == tuple(range(10, 26))
        assert snapshot.faults is not None and snapshot.faults.meta.valid
        assert snapshot.effort_limits is not None
        assert snapshot.effort_limits.raw == (400,) * 16

        angles = hand.set_angles((0,) * 16)
        assert angles.wait(timeout=1) is CommandStage.DISPATCHED
        speeds = hand.set_speeds((70,) * 16)
        assert speeds.wait(timeout=1) is CommandStage.DISPATCHED
        effort = hand.set_effort_limits((500,) * 16)
        assert effort.wait(timeout=1) is CommandStage.DISPATCHED

        assert hand.get_speeds(timeout=0.2).raw == tuple(range(-8, 8))
        configured = hand.get_configured_speeds(timeout=0.2)
        assert configured.raw == (70,) * 16
        assert configured.meta.origin is DataOrigin.DEVICE_CONFIG_RESPONSE
        assert hand.get_effort_limits(timeout=0.2).raw == (500,) * 16
        assert hand.get_currents(timeout=0.2).raw == tuple(range(100, 116))
        assert hand.get_temperatures(timeout=0.2).raw == tuple(range(30, 46))

        force = hand.get_force(timeout=0.2)
        assert len(force.tactile) == 5
        assert all(matrix.online for matrix in force.tactile)
        assert force.tactile[0].raw[0] == tuple(range(6))

        clear = hand.clear_faults()
        assert clear.wait(timeout=1) is CommandStage.DISPATCHED
        assert transport.script.read_counts[0x09] == 1
        assert transport.script.read_counts[0x12] == 1
        assert all(
            message.data[-2:] == b"\x00\x00"
            for message in transport.script.command_messages
            if len(message.data) == 34
        )
    finally:
        hand.close()


def test_o20_polling_is_atomic_and_stops_without_trailing_queries() -> None:
    hand, transport = _make_o20_hand(HandSide.LEFT)
    hand.open()
    try:
        initial = hand.get_snapshot().angles
        assert initial is not None
        hand.start_polling(PollingPlan(angles_hz=50, force_hz=20))

        def polling_ready() -> bool:
            snapshot = hand.get_snapshot()
            angles = snapshot.angles
            force = snapshot.force
            return (
                angles is not None
                and angles.meta.sequence > initial.meta.sequence
                and force is not None
                and force.meta.valid
            )

        wait_until(polling_ready, timeout_s=1)
        hand.stop_polling()
        counts_after_stop = transport.script.read_counts.copy()
        time.sleep(0.05)
        assert transport.script.read_counts == counts_after_stop
    finally:
        hand.close()


def test_o20_recovery_rejects_a_replacement_serial_number() -> None:
    hand, transport = _make_o20_hand(HandSide.RIGHT)
    hand.open()
    try:
        transport.current_dispatcher.disconnect()
        wait_until(lambda: not hand.get_health().connected)
        transport.script.serial_number = "SN-O20-REPLACED"

        with pytest.raises(IdentityMismatchError, match="fingerprint changed"):
            hand.recover(timeout=1)

        assert not hand.get_health().connected
    finally:
        hand.close()


def test_l30_profile_exposes_side_neutral_raw_control_and_telemetry() -> None:
    hand, transport = _make_l30_hand()
    assert transport.dispatchers == []

    hand.open()
    try:
        capabilities = hand.get_capabilities()
        snapshot = hand.get_snapshot()
        assert capabilities.identity.verified
        assert capabilities.side is HandSide.NOT_APPLICABLE
        assert capabilities.identity.firmware == "V1.0.6"
        assert snapshot.device_info is not None
        assert snapshot.device_info.product_code == "LHT30-06-001-L-B-3-A"
        assert snapshot.angles is not None
        assert snapshot.angles.raw == tuple(range(100, 117))
        assert snapshot.faults is not None and snapshot.faults.meta.valid
        assert snapshot.configured_speeds is not None
        assert not snapshot.configured_speeds.meta.valid
        assert snapshot.current_limits is not None
        assert not snapshot.current_limits.meta.valid

        angles = hand.set_angles((0,) * 17)
        assert angles.wait(timeout=1) is CommandStage.DISPATCHED
        percent = hand.set_percent_angles((100,) * 17)
        assert percent.wait(timeout=1) is CommandStage.DISPATCHED
        assert hand.get_angles(timeout=0.2).percent == (100.0,) * 17

        speed_reads = transport.script.read_counts.copy()
        speeds = hand.set_speeds((60,) * 17)
        assert speeds.wait(timeout=1) is CommandStage.DISPATCHED
        configured = hand.get_configured_speeds(timeout=0.2)
        assert configured.raw == (60,) * 17
        assert configured.meta.origin is DataOrigin.COMMAND_CACHE
        assert transport.script.read_counts == speed_reads

        limits = hand.set_current_limits((200,) * 17)
        assert limits.wait(timeout=1) is CommandStage.DISPATCHED
        current_limits = hand.get_current_limits(timeout=0.2)
        assert current_limits.raw == (200,) * 17
        assert current_limits.meta.origin is DataOrigin.COMMAND_CACHE

        assert hand.get_speeds(timeout=0.2).raw == tuple(range(30, 47))
        assert hand.get_currents(timeout=0.2).raw == tuple(range(200, 217))
        assert hand.get_temperatures(timeout=0.2).raw == tuple(range(35, 52))

        force = hand.get_force(timeout=0.5)
        assert len(force.tactile) == 5
        assert all(matrix.online for matrix in force.tactile)
        assert force.tactile[0].raw[0] == tuple(range(6))
        assert all(
            transport.script.read_counts[(0x2, finger)] == 1 for finger in range(1, 6)
        )
    finally:
        hand.close()


def test_l30_acknowledged_operations_preserve_calibration_stage_order() -> None:
    hand, transport = _make_l30_hand()
    hand.open()
    try:
        enable = hand.enable()
        assert enable.wait(timeout=1) is CommandStage.ACKNOWLEDGED
        disable = hand.disable()
        assert disable.wait(timeout=1) is CommandStage.ACKNOWLEDGED
        calibrate = hand.calibrate()
        assert calibrate.wait(timeout=1) is CommandStage.ACKNOWLEDGED

        routes = [
            (
                (message.arbitration_id >> 21) & 0xF,
                (message.arbitration_id >> 13) & 0xFF,
            )
            for message in transport.script.command_messages
        ]
        assert routes[-3:] == [(0x1, 0x08), (0x3, 0x01), (0x3, 0x05)]
    finally:
        hand.close()


def test_l30_polling_is_atomic_and_stops_without_trailing_queries() -> None:
    hand, transport = _make_l30_hand()
    hand.open()
    try:
        initial = hand.get_snapshot().angles
        assert initial is not None
        hand.start_polling(PollingPlan(angles_hz=50, force_hz=20))

        def polling_ready() -> bool:
            snapshot = hand.get_snapshot()
            angles = snapshot.angles
            force = snapshot.force
            return (
                angles is not None
                and angles.meta.sequence > initial.meta.sequence
                and force is not None
                and force.meta.valid
            )

        wait_until(polling_ready, timeout_s=1)
        hand.stop_polling()
        assert all(
            transport.script.read_counts[(0x2, finger)] >= 1 for finger in range(1, 6)
        )
        counts_after_stop = transport.script.read_counts.copy()
        time.sleep(0.05)
        assert transport.script.read_counts == counts_after_stop
    finally:
        hand.close()


def test_l30_recovery_rejects_a_replacement_device_identity() -> None:
    hand, transport = _make_l30_hand()
    hand.open()
    try:
        transport.current_dispatcher.disconnect()
        wait_until(lambda: not hand.get_health().connected)
        transport.script.serial_number = 2

        with pytest.raises(IdentityMismatchError, match="fingerprint changed"):
            hand.recover(timeout=1)

        assert not hand.get_health().connected
    finally:
        hand.close()


def test_o30_profile_exposes_acknowledged_raw_control_and_telemetry() -> None:
    hand, transport = _make_o30_hand()
    assert transport.dispatchers == []

    hand.open()
    try:
        capabilities = hand.get_capabilities()
        snapshot = hand.get_snapshot()
        assert capabilities.identity.verified
        assert capabilities.identity.firmware == "1.2.0"
        assert snapshot.device_info is not None
        assert capabilities.model == "O30"
        assert capabilities.identity.product_code == "O30"
        assert snapshot.device_info.serial_number == "DEVICE-UID-O30"
        assert snapshot.device_info.product_code == "O30"
        assert snapshot.angles is not None
        assert snapshot.angles.raw == tuple(range(20))
        assert snapshot.faults is not None and snapshot.faults.raw == (0,) * 20
        assert snapshot.stall_detection_times is not None
        assert snapshot.stall_detection_times.raw == (11,) * 20
        assert snapshot.stall_thresholds is not None
        assert snapshot.stall_thresholds.raw == (12,) * 20
        assert snapshot.stall_hold_currents is not None
        assert snapshot.stall_hold_currents.raw == (13,) * 20

        command_count = len(transport.script.command_messages)
        raw_angles = hand.set_angles((25,) * 20)
        assert raw_angles.wait(timeout=1) is CommandStage.ACKNOWLEDGED
        assert len(transport.script.command_messages) == command_count + 1
        assert transport.script.command_messages[-1].data == (
            b"\x01\x00\x19" + bytes((25, 0, 0, 0, 0, *([25] * 10), 0, *([25] * 9)))
        )

        percent_angles = hand.set_percent_angles((100,) * 20)
        assert percent_angles.wait(timeout=1) is CommandStage.ACKNOWLEDGED
        angles = hand.get_angles(timeout=0.2)
        assert angles.raw == (255,) * 6 + (0,) * 14
        assert angles.percent == (100.0,) * 20

        commands = (
            (hand.set_speeds, (60,) * 20),
            (hand.set_accelerations, (61,) * 20),
            (hand.set_effort_limits, (62,) * 20),
            (hand.set_motion_times, (63,) * 20),
            (hand.set_stall_detection_times, (64,) * 20),
            (hand.set_stall_thresholds, (65,) * 20),
            (hand.set_stall_hold_currents, (66,) * 20),
        )
        for setter, values in commands:
            handle = setter(values)
            assert handle.wait(timeout=1) is CommandStage.ACKNOWLEDGED

        assert hand.get_speeds(timeout=0.2).raw == tuple(range(30, 50))
        configured = hand.get_configured_speeds(timeout=0.2)
        assert configured.raw == (60,) * 20
        assert configured.meta.origin is DataOrigin.DEVICE_CONFIG_RESPONSE
        assert hand.get_configured_accelerations(timeout=0.2).raw == (61,) * 20
        assert hand.get_effort_limits(timeout=0.2).raw == (62,) * 20
        assert hand.get_motion_times(timeout=0.2).raw == (63,) * 20
        assert hand.get_stall_detection_times(timeout=0.2).raw == (64,) * 20
        assert hand.get_stall_thresholds(timeout=0.2).raw == (65,) * 20
        assert hand.get_stall_hold_currents(timeout=0.2).raw == (66,) * 20
        assert hand.get_currents(timeout=0.2).raw == tuple(range(80, 100))
        assert hand.get_voltages(timeout=0.2).raw == (12,) * 20
        assert hand.get_temperatures(timeout=0.2).raw == tuple(range(25, 45))
        assert hand.get_faults(timeout=0.2).raw == (0,) * 20
        assert all(
            len(message.data) == 28 and message.data[2] == 25
            for message in transport.script.command_messages
        )
    finally:
        hand.close()


def test_o30_current_limits_write_uses_rts1_and_torque_reads_actual() -> None:
    hand, transport = _make_o30_hand()
    hand.open()
    try:
        # Torque measurement reads actual (RTS0); current limit reads setpoint.
        assert hand.get_torques(timeout=0.2).raw == (20,) * 20
        assert hand.get_current_limits(timeout=0.2).raw == tuple(range(80, 100))

        handle = hand.set_current_limits((50,) * 20)
        assert handle.wait(timeout=1) is CommandStage.ACKNOWLEDGED
        # Writing a limit/setpoint must carry RTS=1 (CTRL 0x04 | 0x80 = 0x84).
        assert transport.script.command_messages[-1].data[0] == 0x84
        assert hand.get_current_limits(timeout=0.2).raw == (50,) * 20
    finally:
        hand.close()


def _make_o30_sensor_hand(
    sensor_variant: str,
) -> tuple[HandDevice, ScriptedO30Transport]:
    script = O30DeviceScript(reported_side=HandSide.RIGHT, sensor_variant=sensor_variant)
    transport = ScriptedO30Transport(script)
    hand = HandDevice(model="O30", side="right", transport=transport)
    return hand, transport


def test_o30_get_force_reads_all_five_descriptor_tactile_sensors() -> None:
    hand, _transport = _make_o30_sensor_hand("TSSP_JZG")
    hand.open()
    try:
        force = hand.get_force(timeout=1)

        assert force.tactile == ()
        assert len(force.sensors) == 5
        assert tuple(sensor.metadata.sensor_name for sensor in force.sensors) == (
            "thumb",
            "index",
            "middle",
            "ring",
            "pinky",
        )
        for finger, sensor in enumerate(force.sensors, start=1):
            metadata = sensor.metadata
            assert metadata.selected_sensor == finger
            assert metadata.sensor_type == "TSSP_JZG"
            assert metadata.data_unit == "g"
            assert metadata.descriptor_text == "FnS_U16_1;Fn_U8_70"
            assert metadata.total_data_length == 72
            assert len(sensor.raw_data) == 72
            resultant, normal_force = sensor.fields
            assert resultant.semantic == "FnS"
            assert resultant.values == (1000 + finger,)
            assert normal_force.semantic == "Fn"
            assert normal_force.values == tuple(
                (finger * 10 + index) & 0xFF for index in range(70)
            )
    finally:
        hand.close()


def test_o30_get_normal_force_resultant_extracts_scalar_fns_per_finger() -> None:
    hand, _transport = _make_o30_sensor_hand("TSSP_JZG")
    hand.open()
    try:
        resultant = hand.get_normal_force_resultant(timeout=1)

        assert isinstance(resultant, NormalForceResultantSample)
        assert resultant.sensor_names == ("thumb", "index", "middle", "ring", "pinky")
        assert resultant.raw == (1001, 1002, 1003, 1004, 1005)
        assert resultant.units == ("g", "g", "g", "g", "g")
        assert resultant.meta.valid
    finally:
        hand.close()


def test_o30_get_force_without_a_sensor_is_reported_unavailable() -> None:
    hand, _transport = _make_o30_sensor_hand("NO_SENSOR")
    hand.open()
    try:
        with pytest.raises((ProtocolError, UnsupportedFirmwareError)):
            hand.get_force(timeout=0.3)
    finally:
        hand.close()


def test_o30_left_hand_connects_and_reads_over_0x002_0x402() -> None:
    hand, transport = _make_o30_hand(HandSide.LEFT)
    assert (transport.script.request_id, transport.script.response_id) == (0x002, 0x402)
    hand.open()
    try:
        capabilities = hand.get_capabilities()
        assert capabilities.identity.side is HandSide.LEFT
        assert capabilities.model == "O30"
        info = hand.get_device_info(1.0)
        assert info.side is HandSide.LEFT
        assert hand.get_angles(timeout=0.2).raw == tuple(range(20))
    finally:
        hand.close()


@pytest.mark.parametrize("model", ("O30", "o30", "O30i", "o30i"))
def test_o30_model_aliases_create_the_canonical_profile(model: str) -> None:
    script = O30DeviceScript(product_model="O30i-20-001-R-B")
    transport = ScriptedO30Transport(script)
    hand = HandDevice(model=model, side="right", transport=transport)
    hand.open()
    try:
        assert hand.get_capabilities().model == "O30"
    finally:
        hand.close()


def test_o30_open_rejects_device_reported_side_mismatch() -> None:
    # Device speaks the right-hand ID pair but reports LEFT; a right-declared
    # open must complete identity then reject the reported-side mismatch.
    script = O30DeviceScript(reported_side=HandSide.LEFT)
    hand = HandDevice(
        model="O30",
        side="right",
        transport=ScriptedO30Transport(script),
    )

    with pytest.raises(IdentityMismatchError, match="side does not match"):
        hand.open()


def test_o30_polling_stops_without_trailing_queries() -> None:
    hand, transport = _make_o30_hand(HandSide.LEFT)
    hand.open()
    try:
        initial = hand.get_snapshot().angles
        assert initial is not None
        hand.start_polling(
            PollingPlan(angles_hz=50, speeds_hz=20, current_hz=10, faults_hz=5)
        )

        def polling_ready() -> bool:
            snapshot = hand.get_snapshot()
            return (
                snapshot.angles is not None
                and snapshot.angles.meta.sequence > initial.meta.sequence
                and snapshot.measured_speeds is not None
                and snapshot.measured_speeds.meta.valid
                and snapshot.currents is not None
                and snapshot.currents.meta.valid
            )

        wait_until(polling_ready, timeout_s=1)
        hand.stop_polling()
        counts_after_stop = transport.script.read_counts.copy()
        time.sleep(0.05)
        assert transport.script.read_counts == counts_after_stop
    finally:
        hand.close()


def test_o30_recovery_rejects_a_replacement_device_uid() -> None:
    hand, transport = _make_o30_hand()
    hand.open()
    try:
        transport.current_dispatcher.disconnect()
        wait_until(lambda: not hand.get_health().connected)
        transport.script.device_uid = "DEVICE-UID-REPLACED"

        with pytest.raises(IdentityMismatchError, match="fingerprint changed"):
            hand.recover(timeout=1)

        assert not hand.get_health().connected
    finally:
        hand.close()


def test_production_recovery_revalidates_identity_and_never_replays_target() -> None:
    hand, transport = _make_hand(
        "L6",
        HandSide.LEFT,
        config=DeviceConfig(command_rate_hz=1, auto_reconnect=False),
    )
    hand.open()
    try:
        first = hand.set_angles((1,) * 6)
        assert first.wait(timeout=1) is CommandStage.DISPATCHED
        stale = hand.set_angles((2,) * 6)

        transport.current_dispatcher.disconnect()
        wait_until(lambda: not hand.get_health().connected)
        assert stale.wait(timeout=1) is CommandStage.FAILED
        with pytest.raises(DeviceNotReadyError):
            hand.set_angles((3,) * 6)

        generation = hand.get_health().connection_generation
        identity_queries = transport.script.identity_queries
        hand.recover(timeout=1)

        assert hand.get_health().connection_generation == generation + 1
        assert transport.script.identity_queries == identity_queries + 1
        time.sleep(1.05)
        assert b"\x01" + bytes((2,) * 6) not in transport.script.command_payloads
        assert b"\x01" + bytes((3,) * 6) not in transport.script.command_payloads
    finally:
        hand.close()


def test_production_recovery_rejects_a_replacement_product_code() -> None:
    hand, transport = _make_hand("L6", HandSide.LEFT)
    hand.open()
    try:
        transport.current_dispatcher.disconnect()
        wait_until(lambda: not hand.get_health().connected)
        transport.script.product_code = "LHL6-04-999-L-B-1-A"

        with pytest.raises(IdentityMismatchError, match="fingerprint changed"):
            hand.recover(timeout=1)

        assert not hand.get_health().connected
    finally:
        hand.close()
