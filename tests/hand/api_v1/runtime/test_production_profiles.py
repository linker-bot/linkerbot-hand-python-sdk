"""End-to-end runtime tests using production L6/O6 profiles and CAN frames."""

from __future__ import annotations

import time

import pytest

from linkerbot_hand.api import (
    CommandStage,
    DataOrigin,
    DeviceConfig,
    DeviceNotReadyError,
    HandDevice,
    HandSide,
    IdentityMismatchError,
    PollingPlan,
)

from .classic_can_fake import (
    ScriptedClassicCANTransport,
    make_script,
    wait_until,
)


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


def test_l6_configured_speed_is_dispatch_cache_but_limits_are_queried() -> None:
    hand, transport = _make_hand("L6", HandSide.LEFT)
    hand.open()
    try:
        speed = hand.set_speeds((40, 41, 42, 43, 44, 45))
        assert speed.wait(timeout=1) is CommandStage.DISPATCHED
        sends_before_cache_read = len(transport.script.sent_payloads)

        configured = hand.get_configured_speeds(timeout=0.1)

        assert configured.raw == (40, 41, 42, 43, 44, 45)
        assert configured.meta.origin is DataOrigin.COMMAND_CACHE
        assert len(transport.script.sent_payloads) == sends_before_cache_read

        limits = hand.set_current_limits((70,) * 6)
        assert limits.wait(timeout=1) is CommandStage.DISPATCHED
        queried = hand.get_current_limits(timeout=0.2)
        assert queried.raw == (70,) * 6
        assert queried.meta.origin is DataOrigin.DEVICE_CONFIG_RESPONSE
        assert transport.script.read_counts[0x02] >= 2
    finally:
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
            assert handle.wait(timeout=1) is CommandStage.DISPATCHED
            sample = getter(timeout=0.2)
            assert sample.raw == values
            assert sample.meta.origin is DataOrigin.DEVICE_CONFIG_RESPONSE
            assert transport.script.read_counts[opcode] >= 1
    finally:
        hand.close()


@pytest.mark.parametrize(
    ("model", "side", "force_hz", "rows", "columns", "selector"),
    (
        ("L6", HandSide.LEFT, 5, 12, 6, 0xC6),
        ("O6", HandSide.RIGHT, 20, 10, 4, 0xA4),
        ("L20Lite", HandSide.LEFT, 10, 12, 6, 0xC6),
        ("L25", HandSide.RIGHT, 10, 12, 6, 0xC6),
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
        angle_opcode = 0x41 if model == "L25" else 0x01
        assert transport.script.read_counts[angle_opcode] >= 2

        counts_after_stop = transport.script.read_counts.copy()
        time.sleep(0.05)
        assert transport.script.read_counts == counts_after_stop
    finally:
        hand.close()


@pytest.mark.parametrize(
    ("model", "side", "joint_count", "expected_angles"),
    (
        ("L20Lite", HandSide.LEFT, 10, tuple(range(10, 20))),
        ("L25", HandSide.RIGHT, 16, tuple(range(10, 26))),
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
