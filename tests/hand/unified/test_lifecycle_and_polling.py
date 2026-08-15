from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from linkerbot_hand import (
    ClosedError,
    DeviceIdentity,
    DeviceNotReadyError,
    HandDevice,
    PollingPlan,
    SensorType,
    SessionConfig,
    TimeoutError,
    TransportError,
    get_model_capabilities,
)
from linkerbot_hand.testing import FakeBackend, wait_until

from ._support import CountingFactory, fake_state, make_rig


class _InterruptibleOpenBackend(FakeBackend):
    def __init__(self, state, entered: threading.Event) -> None:
        super().__init__(state)
        self._entered = entered
        self._close_requested = threading.Event()
        self.close_calls = 0

    def open(self):
        self._entered.set()
        if not self._close_requested.wait(1.0):
            raise TimeoutError("test open was not interrupted")
        raise ClosedError("candidate backend closed during open")

    def close(self) -> None:
        self.close_calls += 1
        self._close_requested.set()
        super().close()


class _UninterruptibleOpenBackend(FakeBackend):
    def __init__(
        self,
        state,
        entered: threading.Event,
        release: threading.Event,
    ) -> None:
        super().__init__(state)
        self._entered = entered
        self._release = release
        self.close_calls = 0

    def open(self):
        self._entered.set()
        if not self._release.wait(1.0):
            raise TimeoutError("test did not release blocked open")
        return super().open()

    def close(self) -> None:
        self.close_calls += 1
        super().close()


class _RetryableCloseBackend(FakeBackend):
    def __init__(self, state) -> None:
        super().__init__(state)
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        if self.close_calls == 1:
            raise TransportError("injected close failure")
        super().close()


class _WrongIdentityRetryableCloseBackend(_RetryableCloseBackend):
    def open(self) -> DeviceIdentity:
        identity = super().open()
        return DeviceIdentity(
            model="O6",
            firmware=identity.firmware,
            side=identity.side,
            serial_number=identity.serial_number,
        )


def _poll_count(rig, sensor) -> int:
    with rig.state.lock:
        return rig.state.poll_log.count(sensor)


def test_construction_has_zero_factory_calls_and_zero_device_io() -> None:
    capabilities = get_model_capabilities("L6")
    state = fake_state("L6")
    factory = CountingFactory(state)

    device = HandDevice(capabilities, factory)

    assert factory.call_count == 0
    assert state.open_count == 0
    assert state.close_count == 0
    assert state.io_count == 0
    assert device.get_health().connected is False


def test_open_and_close_are_idempotent() -> None:
    rig = make_rig()

    first = rig.device.open()
    second = rig.device.open()

    assert first is second
    assert rig.state.open_count == 1
    assert rig.state.active_connections == 1

    rig.device.close()
    rig.device.close()

    assert rig.state.close_count == 1
    assert rig.state.active_connections == 0


def test_failed_backend_close_is_retained_for_retry() -> None:
    state = fake_state("L6")
    backend = _RetryableCloseBackend(state)
    factory_calls = 0

    def factory() -> _RetryableCloseBackend:
        nonlocal factory_calls
        factory_calls += 1
        return backend

    device = HandDevice(
        get_model_capabilities("L6", side=state.identity.side),
        factory,
    )
    device.open()

    with pytest.raises(TransportError, match="injected close failure"):
        device.close()

    assert backend.close_calls == 1
    assert state.active_connections == 1
    with pytest.raises(DeviceNotReadyError, match="call close.*again"):
        device.open()
    assert factory_calls == 1

    device.close()
    device.close()

    assert backend.close_calls == 2
    assert state.active_connections == 0


def test_failed_open_cleanup_is_retained_for_close_retry() -> None:
    state = fake_state("L6")
    backend = _WrongIdentityRetryableCloseBackend(state)
    factory_calls = 0

    def factory() -> _WrongIdentityRetryableCloseBackend:
        nonlocal factory_calls
        factory_calls += 1
        return backend

    device = HandDevice(
        get_model_capabilities("L6", side=state.identity.side),
        factory,
    )

    with pytest.raises(TransportError, match="backend could not be closed"):
        device.open()

    assert backend.close_calls == 1
    assert state.active_connections == 1
    with pytest.raises(DeviceNotReadyError, match="call close.*again"):
        device.open()
    assert factory_calls == 1

    device.close()

    assert backend.close_calls == 2
    assert state.active_connections == 0


def test_close_interrupts_candidate_backend_during_open() -> None:
    state = fake_state("L6")
    entered = threading.Event()
    backend = _InterruptibleOpenBackend(state, entered)
    device = HandDevice(
        get_model_capabilities("L6", side=state.identity.side),
        lambda: backend,
        config=SessionConfig(join_timeout_s=0.2),
    )
    open_errors: list[BaseException] = []

    def open_device() -> None:
        try:
            device.open()
        except BaseException as exc:
            open_errors.append(exc)

    open_thread = threading.Thread(target=open_device)
    open_thread.start()
    assert entered.wait(0.2)

    started = time.monotonic()
    device.close()
    elapsed = time.monotonic() - started
    open_thread.join(timeout=0.2)

    assert elapsed < 0.2
    assert not open_thread.is_alive()
    assert backend.close_calls >= 1
    assert len(open_errors) == 1
    assert isinstance(open_errors[0], ClosedError)
    assert device.get_health().connected is False


def test_close_times_out_if_candidate_open_ignores_interruption() -> None:
    state = fake_state("L6")
    entered = threading.Event()
    release = threading.Event()
    backend = _UninterruptibleOpenBackend(state, entered, release)
    device = HandDevice(
        get_model_capabilities("L6", side=state.identity.side),
        lambda: backend,
        config=SessionConfig(join_timeout_s=0.05),
    )
    open_errors: list[BaseException] = []

    def open_device() -> None:
        try:
            device.open()
        except BaseException as exc:
            open_errors.append(exc)

    open_thread = threading.Thread(target=open_device)
    open_thread.start()
    assert entered.wait(0.2)

    started = time.monotonic()
    with pytest.raises(TimeoutError, match=r"open\(\) cancellation"):
        device.close()
    elapsed = time.monotonic() - started

    release.set()
    open_thread.join(timeout=0.2)
    device.open()
    assert state.active_connections == 1
    device.close()

    assert elapsed < 0.2
    assert not open_thread.is_alive()
    assert backend.close_calls >= 2
    assert len(open_errors) == 1
    assert isinstance(open_errors[0], ClosedError)
    assert state.active_connections == 0


def test_close_timeout_prevents_second_open_while_factory_is_still_running() -> None:
    state = fake_state("L6")
    factory_entered = threading.Event()
    factory_release = threading.Event()
    factory_calls = 0

    def blocking_factory() -> FakeBackend:
        nonlocal factory_calls
        factory_calls += 1
        if factory_calls == 1:
            factory_entered.set()
            assert factory_release.wait(1.0)
        return FakeBackend(state)

    device = HandDevice(
        get_model_capabilities("L6", side=state.identity.side),
        blocking_factory,
        config=SessionConfig(join_timeout_s=0.03),
    )
    open_errors: list[BaseException] = []

    def open_device() -> None:
        try:
            device.open()
        except BaseException as exc:
            open_errors.append(exc)

    first_open = threading.Thread(target=open_device)
    first_open.start()
    assert factory_entered.wait(0.2)

    with pytest.raises(TimeoutError, match=r"open\(\) cancellation"):
        device.close()
    with pytest.raises(DeviceNotReadyError, match="open attempt is still exiting"):
        device.open()

    assert factory_calls == 1
    assert state.active_connections == 0

    factory_release.set()
    first_open.join(timeout=0.2)

    assert not first_open.is_alive()
    assert len(open_errors) == 1
    assert isinstance(open_errors[0], ClosedError)
    assert state.active_connections == 0

    device.open()
    try:
        assert factory_calls == 2
        assert state.active_connections == 1
        assert state.max_active_connections == 1
    finally:
        device.close()


def test_disabled_polling_never_queries_sensors() -> None:
    rig = make_rig()
    rig.device.open()
    try:
        rig.clock.advance(60.0)
        rig.device.start_polling(PollingPlan())
        rig.clock.advance(60.0)
        rig.device.stop_polling()

        assert rig.state.poll_log == []
        assert rig.state.io_count == 0
    finally:
        rig.device.close()


def test_stop_polling_prevents_all_future_sensor_queries() -> None:
    rig = make_rig()
    rig.device.open()
    try:
        rig.device.start_polling(PollingPlan(angles_hz=60))
        wait_until(lambda: _poll_count(rig, SensorType.ANGLES) == 1)

        rig.device.stop_polling()
        count_after_stop = len(rig.state.poll_log)
        rig.clock.advance(10.0)
        time.sleep(0.01)

        assert len(rig.state.poll_log) == count_after_stop
    finally:
        rig.device.close()


def test_polling_plan_honors_60_30_and_1_hz_with_manual_clock() -> None:
    rig = make_rig()
    rig.device.open()
    try:
        rig.device.start_polling(
            PollingPlan(angles_hz=60, force_hz=30, temperature_hz=1)
        )
        wait_until(lambda: len(rig.state.poll_log) == 3)

        for tick in range(1, 61):
            rig.clock.advance(1 / 60)
            expected_angles = 1 + tick
            expected_force = 1 + tick // 2
            wait_until(lambda: _poll_count(rig, SensorType.ANGLES) >= expected_angles)
            if tick % 2 == 0:
                wait_until(lambda: _poll_count(rig, SensorType.FORCE) >= expected_force)

        wait_until(lambda: _poll_count(rig, SensorType.TEMPERATURE) == 2)
        rig.device.stop_polling()

        assert _poll_count(rig, SensorType.ANGLES) == 61
        assert _poll_count(rig, SensorType.FORCE) == 31
        assert _poll_count(rig, SensorType.TEMPERATURE) == 2
    finally:
        rig.device.close()


def test_one_hundred_lifecycle_cycles_leak_no_connections_or_workers() -> None:
    rig = make_rig(config=SessionConfig(join_timeout_s=1.0))
    worker_name = "linkerbot-hand-L6"
    baseline_workers = sum(
        thread.name == worker_name and thread.is_alive()
        for thread in threading.enumerate()
    )
    fd_path = Path("/proc/self/fd")
    baseline_fds = len(tuple(fd_path.iterdir())) if fd_path.exists() else None

    for _ in range(100):
        rig.device.open()
        assert rig.state.active_connections == 1
        rig.device.close()
        assert rig.state.active_connections == 0

    remaining_workers = sum(
        thread.name == worker_name and thread.is_alive()
        for thread in threading.enumerate()
    )
    assert rig.state.open_count == 100
    assert rig.state.close_count == 100
    assert rig.state.max_active_connections == 1
    assert remaining_workers == baseline_workers
    if baseline_fds is not None:
        assert len(tuple(fd_path.iterdir())) == baseline_fds
