from __future__ import annotations

import math
import threading
import time

import pytest

from linkerbot_hand import (
    AckSupport,
    CommandStage,
    ConnectionState,
    DispatchResult,
    ErrorCode,
    HandDevice,
    HandSide,
    PollingPlan,
    QueueFullError,
    SensorType,
    SessionConfig,
    TimeoutError,
    TransportError,
    ValidationError,
    get_model_capabilities,
)
from linkerbot_hand.testing import wait_until

from ._support import (
    BlockingDispatchBackend,
    MissingAckBackend,
    RejectingAckBackend,
    fake_state,
    make_rig,
)


class _LaterFrameFailureBackend(BlockingDispatchBackend):
    """Send one frame, then expose a failure in the rest of the command."""

    def dispatch(self, command, *, claim_dispatch, mark_dispatched):
        if not claim_dispatch():
            return DispatchResult()
        mark_dispatched()
        self._entered.set()
        if not self._release.wait(1.0):
            raise TimeoutError("test did not release the later transport frame")
        raise TransportError("injected later-frame send failure")


def _stages(handle) -> list[CommandStage]:
    return [transition.stage for transition in handle.history]


def test_snapshot_is_cached_complete_and_p95_below_one_millisecond() -> None:
    rig = make_rig()
    joint_count = rig.device.get_capabilities().joint_count
    angles = tuple(index + 10 for index in range(joint_count))
    readings = {
        SensorType.ANGLES: angles,
        SensorType.TORQUES: tuple(20 for _ in range(joint_count)),
        SensorType.FORCE: tuple(30 for _ in range(joint_count)),
        SensorType.TEMPERATURE: tuple(40 for _ in range(joint_count)),
        SensorType.CURRENT: tuple(50 for _ in range(joint_count)),
        SensorType.FAULTS: tuple(0 for _ in range(joint_count)),
    }
    for sensor, values in readings.items():
        rig.state.set_reading(sensor, values)
    rig.device.open()
    try:
        rig.device.start_polling(
            PollingPlan(
                angles_hz=60,
                torques_hz=30,
                force_hz=30,
                temperature_hz=1,
                current_hz=10,
                faults_hz=5,
            )
        )
        wait_until(
            lambda: (
                len(rig.state.poll_log) == len(readings)
                and all(sample.valid for sample in rig.device.get_snapshot().samples)
            )
        )
        rig.device.stop_polling()
        io_before = rig.state.io_count

        for _ in range(100):
            rig.device.get_snapshot()
        durations_ns = []
        for _ in range(2_000):
            started = time.perf_counter_ns()
            snapshot = rig.device.get_snapshot()
            durations_ns.append(time.perf_counter_ns() - started)

        percentile_index = math.ceil(0.95 * len(durations_ns)) - 1
        p95_ns = sorted(durations_ns)[percentile_index]
        sample = snapshot.angles

        assert rig.state.io_count == io_before
        assert p95_ns < 1_000_000
        assert snapshot.connection_generation == 1
        assert snapshot.snapshot_sequence >= 2
        assert sample is not None
        assert sample.collected_at == rig.clock.time()
        assert sample.sequence == 1
        assert sample.valid is True
        assert sample.stale is False
        assert sample.connection_generation == snapshot.connection_generation
        assert snapshot.force is not None and snapshot.force.valid
        assert snapshot.temperature is not None and snapshot.temperature.valid
        assert snapshot.current is not None and snapshot.current.valid
        assert snapshot.faults is not None and snapshot.faults.valid
        assert snapshot.force.value_kind.value == "tactile_matrix"
        assert snapshot.force.matrix_shape == (12, 6)
        assert snapshot.temperature.unit == "degree_celsius"
        assert snapshot.temperature.scale == 1
        assert snapshot.current.unit == "count"
        assert snapshot.current.physical_unit == "mA"
        assert snapshot.current.scale == 1400 / 255
        assert snapshot.current.offset == 0
        assert snapshot.faults.value_kind.value == "bitmask"
        assert sample.values == angles
        assert sample.unit == "count"
        assert sample.encoding == "u8"
        assert sample.physical_unit is None
        assert sample.scale is None
        assert sample.offset is None
        assert sample.value_kind.value == "numeric"
        assert sample.matrix_shape is None
        assert snapshot.joints[0].angle == angles[0]
        assert snapshot.joints[0].torque == 20
        assert snapshot.joints[0].fault == 0

        rig.clock.advance(4.0)
        stale_snapshot = rig.device.get_snapshot()
        assert all(sample.valid and sample.stale for sample in stale_snapshot.samples)
        assert rig.state.io_count == io_before
    finally:
        rig.device.close()


@pytest.mark.parametrize("model", ("L6", "O6", "L20Lite", "L25", "O20", "O30i", "L30"))
def test_all_models_preserve_protocol_raw_angles_in_commands_and_snapshots(
    model: str,
) -> None:
    rig = make_rig(model)
    capabilities = rig.device.get_capabilities()
    raw_values = tuple(
        int(
            raw_range.minimum
            if index % 3 == 0
            else (
                raw_range.maximum
                if index % 3 == 1
                else round((raw_range.minimum + raw_range.maximum) / 2)
            )
        )
        for index, joint in enumerate(capabilities.joints)
        if (raw_range := joint.raw_angle_range) is not None
    )
    rig.state.set_reading(SensorType.ANGLES, raw_values)

    rig.device.open()
    try:
        handle = rig.device.submit_angles(raw_values)
        assert handle.wait(1.0) in {
            CommandStage.DISPATCHED,
            CommandStage.ACKNOWLEDGED,
        }
        assert rig.state.dispatched_commands[-1].values == raw_values

        rig.device.start_polling(PollingPlan(angles_hz=60))
        wait_until(
            lambda: (
                (sample := rig.device.get_snapshot().angles) is not None
                and sample.valid
            )
        )
        rig.device.stop_polling()
        snapshot = rig.device.get_snapshot()

        assert snapshot.angles is not None
        assert snapshot.angles.values == raw_values
        assert tuple(joint.angle for joint in snapshot.joints) == raw_values
    finally:
        rig.device.close()


def test_latest_wins_at_1000_hz_with_bounded_pending_and_latest_sent() -> None:
    rig = make_rig(config=SessionConfig(command_rate_hz=100))
    joint_count = rig.device.get_capabilities().joint_count
    rig.device.open()
    handles = []
    try:
        max_pending = 0
        for index in range(1_000):
            value = index % 256
            handles.append(rig.device.submit_angles((value,) * joint_count))
            max_pending = max(max_pending, rig.device.get_health().pending_commands)
            rig.clock.advance(0.001)

        final_handle = handles[-1]
        deadline = time.monotonic() + 1.0
        while not final_handle.done and time.monotonic() < deadline:
            rig.clock.advance(0.01)
            time.sleep(0.001)
        wait_until(lambda: final_handle.done)

        superseded = sum(handle.stage is CommandStage.SUPERSEDED for handle in handles)
        health = rig.device.get_health()

        assert max_pending <= 2
        assert health.pending_commands == 0
        assert health.superseded_commands == superseded
        assert superseded >= 800
        assert len(rig.state.dispatched_commands) <= 200
        assert rig.state.dispatched_commands[-1].values == (231,) * joint_count
        assert final_handle.stage is CommandStage.DISPATCHED
    finally:
        rig.device.close()


def test_unacknowledged_model_stops_at_dispatched_after_transport_send() -> None:
    capabilities = get_model_capabilities("L6", side=HandSide.RIGHT)
    state = fake_state("L6")
    entered = threading.Event()
    release = threading.Event()
    device = HandDevice(
        capabilities,
        lambda: BlockingDispatchBackend(state, entered, release),
    )
    device.open()
    try:
        handle = device.submit_angles((50,) * capabilities.joint_count)
        assert entered.wait(1.0)
        assert handle.stage is CommandStage.ACCEPTED
        assert state.dispatched_commands == []

        release.set()
        assert handle.wait(1.0) is CommandStage.DISPATCHED
        assert handle.ack_support is AckSupport.UNSUPPORTED
        assert _stages(handle) == [CommandStage.ACCEPTED, CommandStage.DISPATCHED]
        assert len(state.dispatched_commands) == 1
    finally:
        release.set()
        device.close()


def test_unacknowledged_multiframe_failure_never_completes_as_dispatched() -> None:
    capabilities = get_model_capabilities("L6", side=HandSide.RIGHT)
    state = fake_state("L6")
    first_frame_sent = threading.Event()
    release_later_frame = threading.Event()
    device = HandDevice(
        capabilities,
        lambda: _LaterFrameFailureBackend(state, first_frame_sent, release_later_frame),
    )
    device.open()
    try:
        handle = device.submit_angles((50,) * capabilities.joint_count)
        assert first_frame_sent.wait(1.0)

        assert handle.stage is CommandStage.ACCEPTED
        assert not handle.done
        assert device.get_health().last_successful_send_at is not None

        release_later_frame.set()
        assert handle.wait(1.0) is CommandStage.FAILED
        assert _stages(handle) == [CommandStage.ACCEPTED, CommandStage.FAILED]
        assert handle.error is not None
        assert handle.error.code is ErrorCode.TRANSPORT
    finally:
        release_later_frame.set()
        device.close()


def test_worker_owned_motion_is_superseded_before_transport_claim() -> None:
    capabilities = get_model_capabilities("L6", side=HandSide.RIGHT)
    state = fake_state("L6")
    entered = threading.Event()
    release = threading.Event()
    device = HandDevice(
        capabilities,
        lambda: BlockingDispatchBackend(state, entered, release),
    )
    device.open()
    try:
        first = device.submit_angles((10,) * capabilities.joint_count)
        assert entered.wait(1.0)
        assert first.stage is CommandStage.ACCEPTED

        latest = device.submit_angles((90,) * capabilities.joint_count)

        assert first.wait(1.0) is CommandStage.SUPERSEDED
        assert state.dispatched_commands == []
        release.set()
        assert latest.wait(1.0) is CommandStage.DISPATCHED
        assert state.dispatched_commands == [latest.command]
        assert _stages(first) == [
            CommandStage.ACCEPTED,
            CommandStage.SUPERSEDED,
        ]
    finally:
        release.set()
        device.close()


def test_acknowledged_stage_requires_explicit_device_ack() -> None:
    rig = make_rig("O30i")
    joint_count = rig.device.get_capabilities().joint_count
    rig.device.open()
    try:
        handle = rig.device.submit_angles((50,) * joint_count)

        assert handle.wait(1.0) is CommandStage.ACKNOWLEDGED
        assert handle.ack_support is AckSupport.SUPPORTED
        assert _stages(handle) == [
            CommandStage.ACCEPTED,
            CommandStage.DISPATCHED,
            CommandStage.ACKNOWLEDGED,
        ]
    finally:
        rig.device.close()


def test_l30_ack_support_is_reported_per_command_kind() -> None:
    rig = make_rig("L30", side=HandSide.LEFT)
    joint_count = rig.device.get_capabilities().joint_count
    rig.device.open()
    try:
        motion = rig.device.submit_angles((50,) * joint_count)
        assert motion.wait(1.0) is CommandStage.DISPATCHED
        assert motion.ack_support is AckSupport.UNSUPPORTED

        discrete = rig.device.enable()
        rig.clock.advance(0.01)
        assert discrete.wait(1.0) is CommandStage.ACKNOWLEDGED
        assert discrete.ack_support is AckSupport.SUPPORTED
    finally:
        rig.device.close()


def test_explicit_device_rejection_fails_after_dispatch() -> None:
    capabilities = get_model_capabilities("O30i", side=HandSide.RIGHT)
    state = fake_state("O30i", acknowledge_commands=True)
    device = HandDevice(capabilities, lambda: RejectingAckBackend(state))
    device.open()
    try:
        handle = device.submit_angles((50,) * capabilities.joint_count)

        assert handle.wait(1.0) is CommandStage.FAILED
        assert _stages(handle) == [
            CommandStage.ACCEPTED,
            CommandStage.DISPATCHED,
            CommandStage.FAILED,
        ]
        assert handle.error is not None
        assert handle.error.code is ErrorCode.DEVICE_FAULT
    finally:
        device.close()


def test_missing_required_ack_fails_with_timeout_and_disconnects() -> None:
    capabilities = get_model_capabilities("O30i", side=HandSide.RIGHT)
    state = fake_state("O30i", acknowledge_commands=False)
    device = HandDevice(capabilities, lambda: MissingAckBackend(state))
    device.open()
    try:
        handle = device.submit_angles((50,) * capabilities.joint_count)

        assert handle.wait(1.0) is CommandStage.FAILED
        assert _stages(handle) == [
            CommandStage.ACCEPTED,
            CommandStage.DISPATCHED,
            CommandStage.FAILED,
        ]
        assert handle.error is not None
        assert handle.error.code is ErrorCode.TIMEOUT
        assert device.get_health().connection_state is ConnectionState.ERROR
    finally:
        device.close()


def test_send_failure_never_reports_dispatched() -> None:
    rig = make_rig()
    rig.state.dispatch_errors.append(TransportError("injected send failure"))
    joint_count = rig.device.get_capabilities().joint_count
    rig.device.open()
    try:
        handle = rig.device.submit_angles((50,) * joint_count)

        assert handle.wait(1.0) is CommandStage.FAILED
        assert _stages(handle) == [CommandStage.ACCEPTED, CommandStage.FAILED]
        assert handle.error is not None
        assert handle.error.code is ErrorCode.TRANSPORT
        assert rig.state.dispatched_commands == []
        assert rig.device.get_health().last_successful_send_at is None
    finally:
        rig.device.close()


def test_non_integer_angle_is_rejected_before_accepted() -> None:
    rig = make_rig()
    joint_count = rig.device.get_capabilities().joint_count
    rig.device.open()
    try:
        with pytest.raises(ValidationError, match=r"angle\[0\] must be int"):
            rig.device.submit_angles((1.0,) + (0,) * (joint_count - 1))

        assert rig.device.get_health().pending_commands == 0
        assert rig.state.dispatched_commands == []

        accepted = rig.device.submit_angles((0,) * joint_count)
        assert accepted.command_id == 1
        assert accepted.wait(1.0) is CommandStage.DISPATCHED
    finally:
        rig.device.close()


def test_discrete_fifo_is_bounded_and_reports_queue_full() -> None:
    capabilities = get_model_capabilities("L6", side=HandSide.RIGHT)
    state = fake_state("L6")
    entered = threading.Event()
    release = threading.Event()
    device = HandDevice(
        capabilities,
        lambda: BlockingDispatchBackend(state, entered, release),
        config=SessionConfig(discrete_queue_size=2),
    )
    device.open()
    try:
        first = device.clear_faults()
        assert entered.wait(1.0)
        second = device.clear_faults()
        third = device.clear_faults()

        with pytest.raises(QueueFullError):
            device.clear_faults()

        assert first.stage is CommandStage.ACCEPTED
        assert second.stage is CommandStage.ACCEPTED
        assert third.stage is CommandStage.ACCEPTED
        assert device.get_health().pending_commands == 3
    finally:
        release.set()
        device.close()
