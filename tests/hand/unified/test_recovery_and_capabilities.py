from __future__ import annotations

import threading
import time

import pytest

from linkerbot_hand import (
    ClosedError,
    CommandKind,
    CommandStage,
    ConnectionState,
    DeviceIdentity,
    DeviceNotReadyError,
    ErrorCode,
    HandDevice,
    HandSide,
    JointEndpoint,
    JointMotionType,
    PercentageSemantics,
    PollingPlan,
    SensorType,
    SessionConfig,
    TransportError,
    TransportType,
    UnsupportedOperationError,
    get_model_capabilities,
)
from linkerbot_hand.testing import (
    FakeBackend,
    FakeBackendFactory,
    ManualClock,
    wait_until,
)

from ._support import (
    BlockingRecoveryFactory,
    InterruptibleRecoveryFactory,
    PendingRecoveryCloseBackend,
    fake_state,
    make_rig,
)

JOINT_NAMES = {
    "L6": ("thumb_flex", "thumb_abd", "index", "middle", "ring", "pinky"),
    "O6": ("thumb_flex", "thumb_abd", "index", "middle", "ring", "pinky"),
    "L20Lite": (
        "thumb_flex",
        "thumb_abd",
        "index_flex",
        "middle_flex",
        "ring_flex",
        "pinky_flex",
        "index_abd",
        "ring_abd",
        "pinky_abd",
        "thumb_yaw",
    ),
    "L25": (
        "thumb_abd",
        "thumb_yaw",
        "thumb_root1",
        "thumb_tip",
        "index_abd",
        "index_root1",
        "index_tip",
        "middle_abd",
        "middle_root1",
        "middle_tip",
        "ring_abd",
        "ring_root1",
        "ring_tip",
        "pinky_abd",
        "pinky_root1",
        "pinky_tip",
    ),
    "L30": tuple(f"j{index}" for index in range(1, 18)),
    "O20": (
        "thumb_mcp",
        "thumb_ip",
        "thumb_abd",
        "thumb_cmc",
        "index_abd",
        "index_mcp",
        "index_pip",
        "middle_abd",
        "middle_mcp",
        "middle_pip",
        "ring_abd",
        "ring_mcp",
        "ring_pip",
        "pinky_abd",
        "pinky_mcp",
        "pinky_dip",
    ),
    "O30i": (
        "roll_0",
        "yaw_0",
        "yaw_1",
        "yaw_2",
        "yaw_3",
        "yaw_4",
        "root1_0",
        "root1_1",
        "root1_2",
        "root1_3",
        "root1_4",
        "root2_1",
        "root2_2",
        "root2_3",
        "root2_4",
        "tip_0",
        "tip_1",
        "tip_2",
        "tip_3",
        "tip_4",
    ),
}


class _OptionalForceTimeoutBackend(FakeBackend):
    def poll(self, sensor):
        if sensor is SensorType.FORCE:
            raise TimeoutError("optional force sensor is offline")
        return super().poll(sensor)


class _CloseFailureBackend(FakeBackend):
    def __init__(self, state, *, failures: int) -> None:
        super().__init__(state)
        self._remaining_close_failures = failures
        self._failure_lock = threading.Lock()
        self.close_calls = 0

    def close(self) -> None:
        with self._failure_lock:
            self.close_calls += 1
            should_fail = self._remaining_close_failures > 0
            if should_fail:
                self._remaining_close_failures -= 1
        if should_fail:
            raise TransportError("injected backend close failure")
        super().close()


class _PollAndCloseFailureBackend(_CloseFailureBackend):
    def poll(self, sensor):
        if sensor is SensorType.ANGLES:
            raise TransportError("injected replacement validation failure")
        return super().poll(sensor)


class _LateRecoveryCandidate(_CloseFailureBackend):
    def __init__(self, state) -> None:
        super().__init__(state, failures=1)
        self.open_calls = 0

    def open(self):
        self.open_calls += 1
        return super().open()


class _PausedRecoveryFactory:
    def __init__(self, state, candidate: _LateRecoveryCandidate) -> None:
        self._state = state
        self._candidate = candidate
        self._lock = threading.Lock()
        self.entered = threading.Event()
        self.release = threading.Event()
        self.call_count = 0

    def __call__(self):
        with self._lock:
            self.call_count += 1
            call_count = self.call_count
        if call_count == 1:
            return FakeBackend(self._state)
        self.entered.set()
        if not self.release.wait(1.0):
            raise TimeoutError("test did not release recovery factory")
        return self._candidate


def _seed_complete_angles(state, joint_count: int) -> None:
    state.set_reading(SensorType.ANGLES, (255,) * joint_count)


def _recent_error_message(device: HandDevice) -> str | None:
    error = device.get_health().recent_error
    return None if error is None else error.message


DIRECTIONAL_AXES = {
    "L6": {"thumb_abd"},
    "O6": {"thumb_abd"},
    "L20Lite": {"thumb_abd", "index_abd", "ring_abd", "pinky_abd", "thumb_yaw"},
    "L25": {
        "thumb_abd",
        "thumb_yaw",
        "index_abd",
        "middle_abd",
        "ring_abd",
        "pinky_abd",
    },
    "L30": {"j3", "j4", "j5", "j12", "j13", "j14", "j17"},
    "O20": {
        "thumb_abd",
        "thumb_cmc",
        "index_abd",
        "middle_abd",
        "ring_abd",
        "pinky_abd",
    },
    "O30i": {"roll_0", "yaw_0", "yaw_1", "yaw_2", "yaw_3", "yaw_4"},
}

RAW_ENDPOINTS = {
    "L6": ((0, 255),) * 6,
    "O6": ((0, 255),) * 6,
    "L20Lite": ((0, 255),) * 10,
    "L25": ((0, 255),) * 16,
    "O20": (
        (120, 0),
        (150, 0),
        (0, 180),
        (0, 130),
        (-30, 30),
        (180, 0),
        (180, 0),
        (-30, 30),
        (180, 0),
        (180, 0),
        (-20, 20),
        (180, 0),
        (180, 0),
        (-20, 20),
        (180, 0),
        (180, 0),
    ),
    "O30i": ((0, 255),) * 6 + ((255, 0),) * 14,
    "L30": (
        (900, 0),
        (1200, 0),
        (0, 900),
        (0, 800),
        (-200, 200),
        (1500, 0),
        (1600, 0),
        (1600, 0),
        (1500, 0),
        (1600, 0),
        (1500, 0),
        (-200, 200),
        (-200, 200),
        (-200, 200),
        (1600, 0),
        (1500, 0),
        (-1000, 1000),
    ),
}


def test_recovery_discards_pre_disconnect_motion_and_never_replays_it() -> None:
    rig = make_rig()
    joint_count = rig.device.get_capabilities().joint_count
    _seed_complete_angles(rig.state, joint_count)
    rig.device.open()
    try:
        sent = rig.device.submit_angles((10,) * joint_count)
        assert sent.wait(1.0) is CommandStage.DISPATCHED

        stale = rig.device.submit_angles((20,) * joint_count)
        assert stale.stage is CommandStage.ACCEPTED
        rig.state.disconnect()
        rig.device.start_polling(PollingPlan(angles_hz=60))
        wait_until(
            lambda: rig.device.get_health().connection_state is ConnectionState.ERROR
        )
        assert stale.wait(1.0) is CommandStage.FAILED

        dispatch_count = len(rig.state.dispatched_commands)
        rig.state.reconnect()
        rig.device.recover(timeout=1.0)
        rig.device.stop_polling()
        rig.clock.advance(1.0)

        assert len(rig.state.dispatched_commands) == dispatch_count
        assert rig.device.get_health().connection_generation == 2
        assert rig.device.get_health().reconnect_count == 1

        fresh = rig.device.submit_angles((30,) * joint_count)
        assert fresh.wait(1.0) is CommandStage.DISPATCHED
        assert rig.state.dispatched_commands[-1].values == (30,) * joint_count
    finally:
        rig.device.close()


def test_concurrent_recover_callers_share_one_successful_generation() -> None:
    capabilities = get_model_capabilities("L6", side=HandSide.RIGHT)
    state = fake_state("L6")
    _seed_complete_angles(state, capabilities.joint_count)
    open_entered = threading.Event()
    open_release = threading.Event()
    factory = BlockingRecoveryFactory(state, open_entered, open_release)
    device = HandDevice(capabilities, factory)
    outcomes: list[str] = []
    errors: list[BaseException] = []
    result_lock = threading.Lock()

    def recover(label: str) -> None:
        try:
            device.recover(timeout=1.0)
            with result_lock:
                outcomes.append(label)
        except BaseException as error:
            with result_lock:
                errors.append(error)

    device.open()
    first = threading.Thread(target=recover, args=("first",))
    second = threading.Thread(target=recover, args=("second",))
    try:
        first.start()
        assert open_entered.wait(1.0)
        second.start()
        time.sleep(0.02)
        open_release.set()
        first.join(1.0)
        second.join(1.0)

        assert not first.is_alive()
        assert not second.is_alive()
        assert errors == []
        assert sorted(outcomes) == ["first", "second"]
        assert factory.call_count == 2
        assert device.get_health().connection_generation == 2
        assert device.get_health().reconnect_count == 1
    finally:
        open_release.set()
        first.join(1.0)
        second.join(1.0)
        device.close()


def test_close_prevents_late_recovery_candidate_open_and_retries_cleanup() -> None:
    capabilities = get_model_capabilities("L6", side=HandSide.RIGHT)
    state = fake_state("L6")
    _seed_complete_angles(state, capabilities.joint_count)
    candidate = _LateRecoveryCandidate(state)
    factory = _PausedRecoveryFactory(state, candidate)
    device = HandDevice(
        capabilities,
        factory,
        config=SessionConfig(join_timeout_s=0.2),
    )
    recovery_errors: list[BaseException] = []
    close_errors: list[BaseException] = []

    def recover() -> None:
        try:
            device.recover(timeout=1.0)
        except BaseException as error:
            recovery_errors.append(error)

    def close() -> None:
        try:
            device.close()
        except BaseException as error:
            close_errors.append(error)

    device.open()
    recovery_thread = threading.Thread(target=recover)
    close_thread = threading.Thread(target=close)
    recovery_thread.start()
    assert factory.entered.wait(1.0)
    close_thread.start()
    wait_until(lambda: device.get_health().connection_state is ConnectionState.CLOSED)
    time.sleep(0.02)
    factory.release.set()
    recovery_thread.join(1.0)
    close_thread.join(1.0)

    assert not recovery_thread.is_alive()
    assert not close_thread.is_alive()
    assert len(recovery_errors) == 1
    assert isinstance(recovery_errors[0], TransportError)
    assert "replacement backend could not be closed" in str(recovery_errors[0])
    assert close_errors == []
    assert factory.call_count == 2
    assert candidate.open_calls == 0
    assert candidate.close_calls == 2
    assert state.active_connections == 0

    device.close()
    assert candidate.close_calls == 2


def test_recovery_rejects_empty_angles_without_advancing_generation() -> None:
    rig = make_rig()
    rig.device.open()
    try:
        with pytest.raises(TransportError, match="complete, non-empty angle reading"):
            rig.device.recover(timeout=1.0)

        snapshot = rig.device.get_snapshot()
        health = rig.device.get_health()

        assert snapshot.connection_generation == 1
        assert snapshot.samples
        assert all(sample.values == () for sample in snapshot.samples)
        assert all(not sample.valid and sample.stale for sample in snapshot.samples)
        assert health.connection_state is ConnectionState.ERROR
        assert health.connection_generation == 1
        assert health.reconnect_count == 0
    finally:
        rig.device.close()


def test_polling_caches_incomplete_joint_readings_as_invalid() -> None:
    rig = make_rig()
    values = (64,) * 5
    rig.state.set_reading(SensorType.ANGLES, values)
    rig.device.open()
    try:

        def angles_published() -> bool:
            sample = rig.device.get_snapshot().angles
            return sample is not None and sample.sequence == 1

        rig.device.start_polling(PollingPlan(angles_hz=60))
        wait_until(angles_published)
        rig.device.stop_polling()

        sample = rig.device.get_snapshot().angles
        health = rig.device.get_health()
        assert sample is not None
        assert sample.values == values
        assert not sample.valid and sample.stale
        assert health.connection_state is ConnectionState.CONNECTED
        assert health.last_successful_send_at is not None
        assert health.last_successful_receive_at is None
    finally:
        rig.device.close()


def test_recovery_requires_angles_but_tolerates_optional_sensor_timeout() -> None:
    capabilities = get_model_capabilities("L6", side=HandSide.RIGHT)
    state = fake_state("L6")
    state.set_reading(SensorType.ANGLES, (255,) * capabilities.joint_count)
    device = HandDevice(
        capabilities,
        lambda: _OptionalForceTimeoutBackend(state),
    )
    device.open()
    try:
        device.recover(timeout=1.0)

        snapshot = device.get_snapshot()
        health = device.get_health()
        assert health.connection_state is ConnectionState.CONNECTED
        assert health.connection_generation == 2
        assert health.last_successful_send_at is not None
        assert health.last_successful_receive_at is not None
        assert health.recent_error is not None
        assert health.recent_error.code is ErrorCode.TIMEOUT
        assert snapshot.angles is not None and snapshot.angles.valid
        assert snapshot.force is not None and not snapshot.force.valid
    finally:
        device.close()


def test_manual_recovery_does_not_replace_backend_until_old_close_succeeds() -> None:
    capabilities = get_model_capabilities("L6", side=HandSide.RIGHT)
    state = fake_state("L6")
    backend = _CloseFailureBackend(state, failures=2)
    factory_calls = 0

    def backend_factory():
        nonlocal factory_calls
        factory_calls += 1
        if factory_calls == 1:
            return backend
        return FakeBackend(state)

    device = HandDevice(capabilities, backend_factory)
    device.open()
    try:
        with pytest.raises(TransportError, match="injected backend close failure"):
            device.recover(timeout=1.0)

        health = device.get_health()
        assert backend.close_calls == 2
        assert factory_calls == 1
        assert state.active_connections == 1
        assert health.connection_state is ConnectionState.ERROR
        assert health.connection_generation == 1
        assert health.reconnect_count == 0
    finally:
        device.close()


def test_auto_recovery_does_not_replace_backend_when_old_close_fails() -> None:
    capabilities = get_model_capabilities("L6", side=HandSide.RIGHT)
    state = fake_state("L6")
    backend = _CloseFailureBackend(state, failures=1)
    clock = ManualClock(wall_time=1_000.0)
    factory_calls = 0

    def backend_factory():
        nonlocal factory_calls
        factory_calls += 1
        if factory_calls == 1:
            return backend
        return FakeBackend(state)

    device = HandDevice(
        capabilities,
        backend_factory,
        config=SessionConfig(
            auto_reconnect=True,
            reconnect_initial_delay_s=0.1,
            reconnect_max_delay_s=0.2,
        ),
        clock=clock,
    )
    device.open()
    try:
        state.disconnect()
        device.start_polling(PollingPlan(angles_hz=60))
        wait_until(
            lambda: device.get_health().connection_state is ConnectionState.ERROR
        )

        state.reconnect()
        clock.advance(0.1)
        wait_until(
            lambda: (
                backend.close_calls == 1
                and _recent_error_message(device) == "injected backend close failure"
            )
        )
        device.stop_polling()

        health = device.get_health()
        assert factory_calls == 1
        assert state.active_connections == 1
        assert health.connection_state is ConnectionState.ERROR
        assert health.connection_generation == 1
        assert health.reconnect_count == 0
    finally:
        device.close()


def test_failed_replacement_cleanup_blocks_the_next_auto_recovery() -> None:
    capabilities = get_model_capabilities("L6", side=HandSide.RIGHT)
    state = fake_state("L6")
    candidate = _PollAndCloseFailureBackend(state, failures=2)
    clock = ManualClock(wall_time=1_000.0)
    factory_calls = 0

    def backend_factory():
        nonlocal factory_calls
        factory_calls += 1
        if factory_calls == 1:
            return FakeBackend(state)
        if factory_calls == 2:
            return candidate
        return FakeBackend(state)

    device = HandDevice(
        capabilities,
        backend_factory,
        config=SessionConfig(
            auto_reconnect=True,
            reconnect_initial_delay_s=0.1,
            reconnect_max_delay_s=0.2,
        ),
        clock=clock,
    )
    device.open()
    try:
        with pytest.raises(TransportError, match="could not be closed"):
            device.recover(timeout=1.0)

        health = device.get_health()
        assert candidate.close_calls == 1
        assert factory_calls == 2
        assert state.active_connections == 1
        assert health.connection_state is ConnectionState.ERROR
        assert health.connection_generation == 1

        clock.advance(0.1)
        wait_until(
            lambda: (
                candidate.close_calls == 2
                and _recent_error_message(device) == "injected backend close failure"
            )
        )

        assert factory_calls == 2
        assert state.active_connections == 1
        assert device.get_health().connection_generation == 1
    finally:
        device.close()


def test_auto_reconnect_replaces_generation_without_replaying_targets() -> None:
    rig = make_rig(
        config=SessionConfig(
            auto_reconnect=True,
            reconnect_initial_delay_s=0.1,
            reconnect_max_delay_s=0.2,
        )
    )
    joint_count = rig.device.get_capabilities().joint_count
    _seed_complete_angles(rig.state, joint_count)
    rig.device.open()
    try:
        sent = rig.device.submit_angles((10,) * joint_count)
        assert sent.wait(1.0) is CommandStage.DISPATCHED

        rig.state.disconnect()
        rig.device.start_polling(PollingPlan(angles_hz=60))
        wait_until(
            lambda: rig.device.get_health().connection_state is ConnectionState.ERROR
        )
        sent_count = len(rig.state.dispatched_commands)

        rig.state.reconnect()
        rig.clock.advance(0.1)
        wait_until(
            lambda: (
                rig.device.get_health().connection_generation == 2
                and rig.device.get_health().connection_state
                is ConnectionState.CONNECTED
            )
        )
        rig.device.stop_polling()

        assert len(rig.state.dispatched_commands) == sent_count
        assert rig.device.get_health().reconnect_count == 1
    finally:
        rig.device.close()


def test_auto_reconnect_handles_runtime_closed_poll_error() -> None:
    rig = make_rig(
        config=SessionConfig(
            auto_reconnect=True,
            reconnect_initial_delay_s=0.1,
            reconnect_max_delay_s=0.2,
        )
    )
    _seed_complete_angles(rig.state, rig.device.get_capabilities().joint_count)
    rig.device.open()
    try:
        rig.state.poll_errors.append(ClosedError("legacy client closed"))
        rig.device.start_polling(PollingPlan(angles_hz=60))

        wait_until(
            lambda: rig.device.get_health().connection_state is ConnectionState.ERROR
        )
        recent_error = rig.device.get_health().recent_error
        assert recent_error is not None
        assert recent_error.code is ErrorCode.CLOSED

        rig.clock.advance(0.1)
        wait_until(
            lambda: (
                rig.device.get_health().connection_generation == 2
                and rig.device.get_health().connection_state
                is ConnectionState.CONNECTED
            )
        )
        assert rig.device.get_health().reconnect_count == 1
    finally:
        rig.device.close()


def test_auto_reconnect_handles_runtime_closed_dispatch_error() -> None:
    rig = make_rig(
        config=SessionConfig(
            auto_reconnect=True,
            reconnect_initial_delay_s=0.1,
            reconnect_max_delay_s=0.2,
        )
    )
    joint_count = rig.device.get_capabilities().joint_count
    _seed_complete_angles(rig.state, joint_count)
    rig.device.open()
    try:
        rig.state.dispatch_errors.append(ClosedError("legacy client closed"))
        handle = rig.device.submit_angles((50,) * joint_count)

        assert handle.wait(1.0) is CommandStage.FAILED
        assert [transition.stage for transition in handle.history] == [
            CommandStage.ACCEPTED,
            CommandStage.FAILED,
        ]
        assert handle.error is not None
        assert handle.error.code is ErrorCode.CLOSED
        assert rig.device.get_health().connection_state is ConnectionState.ERROR
        assert rig.state.dispatched_commands == []

        rig.clock.advance(0.1)
        wait_until(
            lambda: (
                rig.device.get_health().connection_generation == 2
                and rig.device.get_health().connection_state
                is ConnectionState.CONNECTED
            )
        )
        assert rig.device.get_health().reconnect_count == 1
    finally:
        rig.device.close()


def test_runtime_device_not_ready_poll_error_does_not_reconnect() -> None:
    rig = make_rig(
        config=SessionConfig(
            auto_reconnect=True,
            reconnect_initial_delay_s=0.1,
            reconnect_max_delay_s=0.2,
        )
    )
    rig.device.open()
    try:
        rig.state.poll_errors.append(DeviceNotReadyError("device is not enabled"))
        rig.device.start_polling(PollingPlan(angles_hz=60))

        wait_until(lambda: rig.device.get_health().recent_error is not None)
        health = rig.device.get_health()
        assert health.connection_state is ConnectionState.CONNECTED
        assert health.connection_generation == 1
        assert health.reconnect_count == 0
        assert health.recent_error is not None
        assert health.recent_error.code is ErrorCode.DEVICE_NOT_READY
    finally:
        rig.device.close()


def test_runtime_device_not_ready_dispatch_error_does_not_reconnect() -> None:
    rig = make_rig(
        config=SessionConfig(
            auto_reconnect=True,
            reconnect_initial_delay_s=0.1,
            reconnect_max_delay_s=0.2,
        )
    )
    joint_count = rig.device.get_capabilities().joint_count
    rig.device.open()
    try:
        rig.state.dispatch_errors.append(DeviceNotReadyError("device is not enabled"))
        handle = rig.device.submit_angles((50,) * joint_count)

        assert handle.wait(1.0) is CommandStage.FAILED
        assert handle.error is not None
        assert handle.error.code is ErrorCode.DEVICE_NOT_READY
        health = rig.device.get_health()
        assert health.connection_state is ConnectionState.CONNECTED
        assert health.connection_generation == 1
        assert health.reconnect_count == 0
        assert rig.state.dispatched_commands == []
    finally:
        rig.device.close()


def test_recover_closes_blocked_dispatch_and_never_replays_old_targets() -> None:
    capabilities = get_model_capabilities("L6", side=HandSide.RIGHT)
    state = fake_state("L6")
    _seed_complete_angles(state, capabilities.joint_count)
    dispatch_entered = threading.Event()
    factory = InterruptibleRecoveryFactory(state, dispatch_entered)
    device = HandDevice(capabilities, factory)
    device.open()
    try:
        inflight = device.submit_angles((10,) * capabilities.joint_count)
        assert dispatch_entered.wait(1.0)
        queued = device.submit_angles((20,) * capabilities.joint_count)

        device.recover(timeout=1.0)

        assert inflight.wait(1.0) is CommandStage.SUPERSEDED
        assert queued.wait(1.0) is CommandStage.FAILED
        assert state.dispatched_commands == []
        assert factory.call_count == 2
        assert device.get_health().connection_generation == 2

        time.sleep(0.02)
        assert state.dispatched_commands == []

        fresh = device.submit_angles((30,) * capabilities.joint_count)
        assert fresh.wait(1.0) is CommandStage.DISPATCHED
        assert [command.values for command in state.dispatched_commands] == [
            (30,) * capabilities.joint_count
        ]
    finally:
        device.close()


def test_close_wakes_recover_waiter_while_recovery_token_is_pending() -> None:
    capabilities = get_model_capabilities("L6", side=HandSide.RIGHT)
    state = fake_state("L6")
    dispatch_entered = threading.Event()
    first_close_entered = threading.Event()
    second_close_entered = threading.Event()
    release_first_close = threading.Event()
    backend = PendingRecoveryCloseBackend(
        state,
        dispatch_entered=dispatch_entered,
        first_close_entered=first_close_entered,
        second_close_entered=second_close_entered,
        release_first_close=release_first_close,
    )
    factory_calls = 0

    def backend_factory():
        nonlocal factory_calls
        factory_calls += 1
        return backend

    device = HandDevice(capabilities, backend_factory)
    recover_errors: list[BaseException] = []
    close_errors: list[BaseException] = []

    def recover() -> None:
        try:
            device.recover(timeout=1.0)
        except BaseException as error:
            recover_errors.append(error)

    def close() -> None:
        try:
            device.close()
        except BaseException as error:
            close_errors.append(error)

    device.open()
    command = device.submit_angles((10,) * capabilities.joint_count)
    assert dispatch_entered.wait(1.0)
    recover_thread = threading.Thread(target=recover)
    close_thread = threading.Thread(target=close)
    try:
        recover_thread.start()
        assert first_close_entered.wait(1.0)

        close_thread.start()
        assert second_close_entered.wait(1.0)
        close_thread.join(1.0)
        assert not close_thread.is_alive()

        release_first_close.set()
        recover_thread.join(1.0)

        assert not recover_thread.is_alive()
        assert close_errors == []
        assert len(recover_errors) == 1
        assert isinstance(recover_errors[0], ClosedError)
        assert "before recovery started" in str(recover_errors[0])
        assert command.wait(1.0) is CommandStage.FAILED
        assert state.dispatched_commands == []
        assert state.active_connections == 0
        assert factory_calls == 1
        assert device.get_health().connection_generation == 1
    finally:
        release_first_close.set()
        if close_thread.ident is not None:
            close_thread.join(1.0)
        if recover_thread.ident is not None:
            recover_thread.join(1.0)
        device.close()


MODEL_CONTRACTS = {
    "L6": {
        "joint_count": 6,
        "transport": TransportType.CAN,
        "sensors": {
            SensorType.ANGLES: 60,
            SensorType.TORQUES: 30,
            SensorType.FORCE: 30,
            SensorType.TEMPERATURE: 1,
            SensorType.CURRENT: 10,
            SensorType.FAULTS: 5,
        },
        "features": (False, True, False, False),
        "physical_angles": False,
    },
    "O6": {
        "joint_count": 6,
        "transport": TransportType.CAN,
        "sensors": {
            SensorType.ANGLES: 60,
            SensorType.SPEEDS: 30,
            SensorType.ACCELERATION: 10,
            SensorType.TORQUES: 30,
            SensorType.FORCE: 30,
            SensorType.TEMPERATURE: 1,
            SensorType.FAULTS: 5,
        },
        "features": (False, False, False, False),
        "physical_angles": False,
    },
    "L20Lite": {
        "joint_count": 10,
        "transport": TransportType.CAN,
        "sensors": {
            SensorType.ANGLES: 60,
            SensorType.SPEEDS: 30,
            SensorType.TORQUES: 30,
            SensorType.FORCE: 30,
            SensorType.TEMPERATURE: 1,
            SensorType.FAULTS: 5,
        },
        "features": (False, False, False, False),
        "physical_angles": False,
    },
    "L25": {
        "joint_count": 16,
        "transport": TransportType.CAN,
        "sensors": {
            SensorType.ANGLES: 60,
            SensorType.SPEEDS: 30,
            SensorType.TORQUES: 30,
            SensorType.FORCE: 30,
            SensorType.TEMPERATURE: 1,
            SensorType.FAULTS: 5,
        },
        "features": (False, True, False, False),
        "physical_angles": False,
    },
    "L30": {
        "joint_count": 17,
        "transport": TransportType.CAN_FD,
        "sensors": {
            SensorType.ANGLES: 60,
            SensorType.SPEEDS: 30,
            SensorType.FORCE: 30,
            SensorType.TEMPERATURE: 1,
            SensorType.CURRENT: 10,
            SensorType.FAULTS: 5,
        },
        "features": (True, False, True, True),
        "acknowledged_commands": (
            CommandKind.ENABLE,
            CommandKind.DISABLE,
            CommandKind.CALIBRATE,
        ),
        "physical_angles": False,
    },
    "O20": {
        "joint_count": 16,
        "transport": TransportType.CAN_FD,
        "sensors": {
            SensorType.ANGLES: 30,
            SensorType.SPEEDS: 30,
            SensorType.TORQUES: 10,
            SensorType.FORCE: 30,
            SensorType.TEMPERATURE: 1,
            SensorType.CURRENT: 10,
            SensorType.FAULTS: 5,
        },
        "features": (False, True, False, False),
        "physical_angles": False,
    },
    "O30i": {
        "joint_count": 20,
        "transport": TransportType.CAN_FD,
        "sensors": {
            SensorType.ANGLES: 30,
            SensorType.SPEEDS: 30,
            SensorType.ACCELERATION: 10,
            SensorType.TORQUES: 30,
            SensorType.TEMPERATURE: 1,
            SensorType.CURRENT: 10,
            SensorType.FAULTS: 5,
            SensorType.VOLTAGE: 1,
            SensorType.MOTION_TIME: 10,
        },
        "features": (True, False, False, False),
        "acknowledged_commands": (
            CommandKind.ANGLES,
            CommandKind.SPEEDS,
            CommandKind.TORQUES,
        ),
        "physical_angles": False,
    },
}


@pytest.mark.parametrize(("model", "contract"), MODEL_CONTRACTS.items())
def test_all_seven_models_publish_complete_capability_contract(
    model: str, contract: dict[str, object]
) -> None:
    capabilities = get_model_capabilities(model, side=HandSide.LEFT)
    factory = FakeBackendFactory(capabilities)
    device = HandDevice(capabilities, factory)

    assert device.get_capabilities() == capabilities
    assert capabilities.model == model
    assert capabilities.firmware is None
    assert capabilities.side is HandSide.LEFT
    assert capabilities.joint_count == contract["joint_count"]
    assert capabilities.joint_names == JOINT_NAMES[model]
    assert tuple(joint.index for joint in capabilities.joints) == tuple(
        range(capabilities.joint_count)
    )
    assert len(set(capabilities.joint_names)) == capabilities.joint_count
    assert capabilities.transport is contract["transport"]
    assert capabilities.percentage_convention == "100=open, 0=closed"
    assert {
        sensor.sensor: sensor.recommended_hz for sensor in capabilities.sensors
    } == contract["sensors"]

    features = capabilities.features
    assert (
        features.device_ack,
        features.clear_faults,
        features.enable,
        features.calibration,
    ) == contract["features"]
    assert features.acknowledged_commands == contract.get("acknowledged_commands", ())

    position_capabilities = []
    for joint in capabilities.joints:
        raw_range = joint.raw_angle_range
        mapping = joint.percentage_mapping
        assert raw_range is not None
        assert mapping is not None
        position_capabilities.append((raw_range, mapping))
        assert raw_range.minimum <= raw_range.maximum
        assert raw_range.unit != "radian"
        assert joint.speed_range is not None
        assert joint.torque_range is not None
        assert raw_range.minimum <= mapping.raw_open <= raw_range.maximum
        assert raw_range.minimum <= mapping.raw_closed <= raw_range.maximum
        assert mapping.physical_angle_supported is contract["physical_angles"]
        assert mapping.percentage_readback_supported
        assert mapping.raw_at_zero == mapping.raw_closed
        assert mapping.raw_at_hundred == mapping.raw_open
        if mapping.physical_angle is not None:
            assert mapping.physical_angle.unit == "degree"

    open_percentages = (100,) * capabilities.joint_count
    closed_percentages = (0,) * capabilities.joint_count
    raw_open = tuple(mapping.raw_open for _, mapping in position_capabilities)
    raw_closed = tuple(mapping.raw_closed for _, mapping in position_capabilities)
    assert device.percent_to_raw(open_percentages) == raw_open
    assert device.percent_to_raw(closed_percentages) == raw_closed
    assert device.raw_to_percent(raw_open) == tuple(
        100.0 for _ in position_capabilities
    )
    assert device.raw_to_percent(raw_closed) == tuple(
        0.0 for _ in position_capabilities
    )

    device.open()
    try:
        live_capabilities = device.get_capabilities()
        assert live_capabilities.model == model
        assert live_capabilities.firmware == "fake-1.0"
        assert live_capabilities.side is HandSide.LEFT
    finally:
        device.close()


def test_all_axes_publish_reversible_measured_percentage_mappings() -> None:
    for model in MODEL_CONTRACTS:
        capabilities = get_model_capabilities(model, side=HandSide.LEFT)
        device = HandDevice(capabilities, FakeBackendFactory(capabilities))
        assert len(RAW_ENDPOINTS[model]) == capabilities.joint_count

        for joint, (raw_at_zero, raw_at_hundred) in zip(
            capabilities.joints, RAW_ENDPOINTS[model], strict=True
        ):
            mapping = joint.percentage_mapping
            assert mapping is not None
            assert mapping.raw_at_zero == raw_at_zero
            assert mapping.raw_at_hundred == raw_at_hundred
            assert mapping.raw_at_zero != mapping.raw_at_hundred
            if joint.name in DIRECTIONAL_AXES[model]:
                assert mapping.semantics is PercentageSemantics.POSITION
                assert joint.motion_type is not JointMotionType.FLEXION
                assert mapping.zero_endpoint is not JointEndpoint.FLEXED
                assert mapping.hundred_endpoint is not JointEndpoint.EXTENDED
            else:
                assert mapping.semantics is PercentageSemantics.OPENNESS
                assert joint.motion_type is JointMotionType.FLEXION
                assert mapping.zero_endpoint is JointEndpoint.FLEXED
                assert mapping.hundred_endpoint is JointEndpoint.EXTENDED

        raw_at_zero = tuple(endpoints[0] for endpoints in RAW_ENDPOINTS[model])
        raw_at_hundred = tuple(endpoints[1] for endpoints in RAW_ENDPOINTS[model])
        assert device.percent_to_raw((0,) * capabilities.joint_count) == raw_at_zero
        assert device.percent_to_raw((100,) * capabilities.joint_count) == (
            raw_at_hundred
        )
        assert device.raw_to_percent(raw_at_zero) == (0.0,) * capabilities.joint_count
        assert (
            device.raw_to_percent(raw_at_hundred) == (100.0,) * capabilities.joint_count
        )


@pytest.mark.parametrize("model", MODEL_CONTRACTS)
def test_percent_to_raw_applies_each_joint_mapping_and_round_trips(
    model: str,
) -> None:
    capabilities = get_model_capabilities(model, side=HandSide.LEFT)
    device = HandDevice(capabilities, FakeBackendFactory(capabilities))
    percentages = tuple(
        (index * 17 + 11) % 101 for index in range(capabilities.joint_count)
    )
    mappings = []
    for joint in capabilities.joints:
        mapping = joint.percentage_mapping
        assert mapping is not None
        mappings.append(mapping)
    expected = tuple(
        round(
            mapping.raw_closed
            + percentage / 100.0 * (mapping.raw_open - mapping.raw_closed)
        )
        for mapping, percentage in zip(mappings, percentages, strict=True)
    )

    assert device.percent_to_raw(percentages) == expected
    round_trip = device.raw_to_percent(expected)
    for actual, percentage, mapping in zip(
        round_trip, percentages, mappings, strict=True
    ):
        raw_span = abs(mapping.raw_at_hundred - mapping.raw_at_zero)
        assert actual == pytest.approx(percentage, abs=50.0 / raw_span)


def test_o20_thumb_axes_publish_physical_motion_and_coupling() -> None:
    joints = get_model_capabilities("O20").joints

    assert joints[2].name == "thumb_abd"
    assert joints[2].motion_type is JointMotionType.ROLL
    assert joints[2].coupled_with == ("thumb_cmc",)
    assert joints[3].name == "thumb_cmc"
    assert joints[3].motion_type is JointMotionType.ROTATION
    assert joints[3].coupled_with == ("thumb_abd",)


@pytest.mark.parametrize("model", ("O20", "L30"))
def test_unverified_physical_angle_conversions_are_unsupported(model: str) -> None:
    capabilities = get_model_capabilities(model, side=HandSide.LEFT)
    device = HandDevice(capabilities, FakeBackendFactory(capabilities))
    raw_ranges = []
    for joint in capabilities.joints:
        raw_range = joint.raw_angle_range
        mapping = joint.percentage_mapping
        assert raw_range is not None
        assert mapping is not None
        assert mapping.physical_angle is None
        raw_ranges.append(raw_range)
    raw_values = tuple(int(raw_range.minimum) for raw_range in raw_ranges)

    with pytest.raises(
        UnsupportedOperationError, match="no calibrated physical angle mapping"
    ):
        device.raw_to_physical_angles(raw_values)
    with pytest.raises(
        UnsupportedOperationError, match="no calibrated physical angle mapping"
    ):
        device.physical_angles_to_raw((0,) * capabilities.joint_count)


@pytest.mark.parametrize("side", tuple(HandSide))
def test_l30_position_capabilities_use_one_mapping_for_all_sides(
    side: HandSide,
) -> None:
    capabilities = get_model_capabilities("L30", side=side)
    shared = get_model_capabilities("L30")

    assert capabilities.side is side
    assert capabilities.joints == shared.joints
    assert all(
        joint.raw_angle_range is not None
        and joint.percentage_mapping is not None
        and joint.raw_angle_control_supported
        and joint.percentage_angle_control_supported
        for joint in capabilities.joints
    )


@pytest.mark.parametrize("identity_side", (HandSide.LEFT, HandSide.RIGHT))
def test_l30_identity_preserves_shared_position_capabilities(
    identity_side: HandSide,
) -> None:
    capabilities = get_model_capabilities("L30")
    factory = FakeBackendFactory(capabilities)
    factory.state.identity = DeviceIdentity(
        model="L30",
        firmware="fake-1.0",
        side=identity_side,
        serial_number=f"FAKE-L30-{identity_side.value.upper()}",
    )
    device = HandDevice(capabilities, factory)

    device.open()
    try:
        live = device.get_capabilities()
        assert live.side is identity_side
        assert live.joints == capabilities.joints
        assert all(joint.raw_angle_range is not None for joint in live.joints)
        assert all(joint.percentage_mapping is not None for joint in live.joints)
    finally:
        device.close()


def test_l30_right_position_commands_use_shared_mapping() -> None:
    capabilities = get_model_capabilities("L30", side=HandSide.RIGHT)
    factory = FakeBackendFactory(capabilities)
    device = HandDevice(capabilities, factory)
    percentages = (50,) * capabilities.joint_count
    raw_values = device.percent_to_raw(percentages)

    assert len(raw_values) == capabilities.joint_count
    for joint, percentage in zip(
        capabilities.joints, device.raw_to_percent(raw_values), strict=True
    ):
        assert joint.percentage_mapping is not None
        assert percentage == pytest.approx(50.0)

    device.open()
    try:
        assert (
            device.submit_percentage_angles(percentages).wait(1.0)
            is CommandStage.DISPATCHED
        )
        assert device.submit_angles(raw_values).wait(1.0) is CommandStage.DISPATCHED
        assert [command.kind for command in factory.state.dispatched_commands] == [
            CommandKind.ANGLES,
            CommandKind.ANGLES,
        ]
    finally:
        device.close()


@pytest.mark.parametrize("model", ("L6", "O6", "L20Lite", "L25"))
def test_percentage_speed_conversion_is_explicitly_supported_for_classic_models(
    model: str,
) -> None:
    capabilities = get_model_capabilities(model)
    device = HandDevice(capabilities, FakeBackendFactory(capabilities))
    percentages = tuple(
        (0, 50, 100)[index % 3] for index in range(capabilities.joint_count)
    )

    protocol_values = device.percentage_speeds_to_raw(percentages)

    assert all(joint.percentage_speed_supported for joint in capabilities.joints)
    assert protocol_values == tuple(
        (0, 128, 255)[index % 3] for index in range(capabilities.joint_count)
    )
    assert device.raw_speeds_to_percentage(protocol_values) == pytest.approx(
        tuple((0, 128 * 100 / 255, 100)[index % 3] for index in range(len(percentages)))
    )


@pytest.mark.parametrize("model", ("O20", "O30i", "L30"))
def test_percentage_speed_conversion_rejects_models_without_declared_mapping(
    model: str,
) -> None:
    capabilities = get_model_capabilities(model)
    device = HandDevice(capabilities, FakeBackendFactory(capabilities))

    assert not any(joint.percentage_speed_supported for joint in capabilities.joints)
    with pytest.raises(
        UnsupportedOperationError, match="no verified percentage speed mapping"
    ):
        device.percentage_speeds_to_raw((50,) * capabilities.joint_count)
