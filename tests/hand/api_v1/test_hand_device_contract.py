from __future__ import annotations

import threading
from collections.abc import Iterable
from typing import Any, cast

import pytest

import linkerbot_hand.api as hand_api
from linkerbot_hand.api import (
    AckSupport,
    AngleSample,
    ClosedError,
    CommandFamily,
    CommandHandle,
    HandDevice,
    HandSide,
    HandSnapshot,
    HealthStatus,
    LifecycleError,
    PollingPlan,
    UnsupportedOperationError,
    ValidationError,
)
from linkerbot_hand.capabilities.types import (
    CommandSemantic,
    DeviceCapabilities,
    TelemetrySemantic,
)

_MODEL_CONFIGS = (
    ("l6", "left"),
    ("o6", "right"),
    ("l20lite", "left"),
    ("l25", "right"),
    ("o20", "left"),
    ("l30", None),
    ("o30i", "right"),
)

_PUBLIC_METHODS = (
    "open",
    "close",
    "start_polling",
    "stop_polling",
    "set_angles",
    "set_percent_angles",
    "get_angles",
    "set_speeds",
    "set_percent_speeds",
    "get_speeds",
    "get_configured_speeds",
    "set_accelerations",
    "get_configured_accelerations",
    "set_current_limits",
    "get_current_limits",
    "set_effort_limits",
    "get_effort_limits",
    "set_torques",
    "get_torques",
    "get_currents",
    "get_temperatures",
    "get_force",
    "get_faults",
    "get_voltages",
    "set_motion_times",
    "get_motion_times",
    "get_device_info",
    "set_stall_detection_times",
    "get_stall_detection_times",
    "set_stall_thresholds",
    "get_stall_thresholds",
    "set_stall_hold_currents",
    "get_stall_hold_currents",
    "set_limit_compensations",
    "get_limit_compensations",
    "clear_faults",
    "enable",
    "disable",
    "calibrate",
    "save_parameters",
    "get_snapshot",
    "wait_for_snapshot",
    "get_health",
    "get_capabilities",
    "recover",
)

_COMMAND_METHODS = {
    CommandSemantic.ANGLES: "set_angles",
    CommandSemantic.SPEEDS: "set_speeds",
    CommandSemantic.ACCELERATIONS: "set_accelerations",
    CommandSemantic.CURRENT_LIMITS: "set_current_limits",
    CommandSemantic.EFFORT_LIMITS: "set_effort_limits",
    CommandSemantic.TORQUES: "set_torques",
    CommandSemantic.MOTION_TIMES: "set_motion_times",
    CommandSemantic.STALL_DETECTION_TIMES: "set_stall_detection_times",
    CommandSemantic.STALL_THRESHOLDS: "set_stall_thresholds",
    CommandSemantic.STALL_HOLD_CURRENTS: "set_stall_hold_currents",
    CommandSemantic.LIMIT_COMPENSATIONS: "set_limit_compensations",
}

_TELEMETRY_METHODS = {
    TelemetrySemantic.ANGLES: "get_angles",
    TelemetrySemantic.MEASURED_SPEEDS: "get_speeds",
    TelemetrySemantic.CONFIGURED_SPEEDS: "get_configured_speeds",
    TelemetrySemantic.CONFIGURED_ACCELERATIONS: "get_configured_accelerations",
    TelemetrySemantic.CURRENTS: "get_currents",
    TelemetrySemantic.CURRENT_LIMITS: "get_current_limits",
    TelemetrySemantic.EFFORT_LIMITS: "get_effort_limits",
    TelemetrySemantic.TORQUES: "get_torques",
    TelemetrySemantic.TEMPERATURES: "get_temperatures",
    TelemetrySemantic.FORCE: "get_force",
    TelemetrySemantic.FAULTS: "get_faults",
    TelemetrySemantic.VOLTAGES: "get_voltages",
    TelemetrySemantic.MOTION_TIMES: "get_motion_times",
    TelemetrySemantic.STALL_DETECTION_TIMES: "get_stall_detection_times",
    TelemetrySemantic.STALL_THRESHOLDS: "get_stall_thresholds",
    TelemetrySemantic.STALL_HOLD_CURRENTS: "get_stall_hold_currents",
    TelemetrySemantic.LIMIT_COMPENSATIONS: "get_limit_compensations",
}

_SNAPSHOT_ATTRIBUTES = {
    TelemetrySemantic.ANGLES: "angles",
    TelemetrySemantic.MEASURED_SPEEDS: "measured_speeds",
    TelemetrySemantic.CONFIGURED_SPEEDS: "configured_speeds",
    TelemetrySemantic.CONFIGURED_ACCELERATIONS: "configured_accelerations",
    TelemetrySemantic.CURRENTS: "currents",
    TelemetrySemantic.CURRENT_LIMITS: "current_limits",
    TelemetrySemantic.EFFORT_LIMITS: "effort_limits",
    TelemetrySemantic.TORQUES: "torques",
    TelemetrySemantic.TEMPERATURES: "temperatures",
    TelemetrySemantic.FORCE: "force",
    TelemetrySemantic.FAULTS: "faults",
    TelemetrySemantic.VOLTAGES: "voltages",
    TelemetrySemantic.MOTION_TIMES: "motion_times",
    TelemetrySemantic.STALL_DETECTION_TIMES: "stall_detection_times",
    TelemetrySemantic.STALL_THRESHOLDS: "stall_thresholds",
    TelemetrySemantic.STALL_HOLD_CURRENTS: "stall_hold_currents",
    TelemetrySemantic.LIMIT_COMPENSATIONS: "limit_compensations",
}


class SpyDependency:
    """Records any accidental construction-time dependency access."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> Any:
        self.calls.append(name)
        raise AssertionError(f"constructor accessed dependency attribute {name}")


class FakeRuntime:
    def __init__(self, **options: object) -> None:
        self.capabilities = cast(DeviceCapabilities, options["capabilities"])
        self.snapshot = cast(HandSnapshot, options["initial_snapshot"])
        self.health = cast(HealthStatus, options["initial_health"])
        self.open_calls = 0
        self.close_calls = 0
        self.close_failures_remaining = 0
        self.close_entered = threading.Event()
        self.close_release: threading.Event | None = None
        self.stop_polling_calls = 0
        self.submissions: list[tuple[CommandSemantic, tuple[int | float, ...]]] = []
        self.polling_plans: list[PollingPlan] = []
        self.operations: list[str] = []
        self.snapshot_wait_options: list[bool] = []
        self._opened = False
        self._close_pending = False
        self._polling_plan: PollingPlan | None = None
        self._state_lock = threading.Lock()

    def open(self) -> None:
        with self._state_lock:
            if self._opened:
                return
            if self._close_pending:
                raise LifecycleError("retry close before open")
            self.open_calls += 1
            self._opened = True
            self._close_pending = True

    def close(self) -> None:
        with self._state_lock:
            if not self._close_pending:
                return
            self.close_calls += 1
            self._opened = False
        self.close_entered.set()
        if self.close_release is not None:
            assert self.close_release.wait(timeout=1)
        if self.close_failures_remaining:
            self.close_failures_remaining -= 1
            raise RuntimeError("simulated close failure")
        with self._state_lock:
            self._close_pending = False
            self._polling_plan = None

    def start_polling(self, plan: PollingPlan) -> None:
        with self._state_lock:
            self._require_open_locked()
            if plan == self._polling_plan:
                return
            self.polling_plans.append(plan)
            self._polling_plan = plan

    def stop_polling(self) -> None:
        with self._state_lock:
            if not self._opened or self._polling_plan is None:
                return
            self.stop_polling_calls += 1
            self._polling_plan = None

    def submit(
        self,
        semantic: CommandSemantic,
        values: tuple[int | float, ...],
    ) -> CommandHandle:
        with self._state_lock:
            self._require_open_locked()
            self.submissions.append((semantic, values))
            return CommandHandle(
                command_id=len(self.submissions),
                family=CommandFamily.ANGLE_TARGET,
                ack_support=AckSupport.UNSUPPORTED,
                accepted_at=1.0,
            )

    def read(self, semantic: TelemetrySemantic, timeout: float) -> object:
        assert timeout > 0
        with self._state_lock:
            self._require_open_locked()
        return getattr(self.snapshot, _SNAPSHOT_ATTRIBUTES[semantic])

    def operate(self, operation: str) -> CommandHandle:
        with self._state_lock:
            self._require_open_locked()
            self.operations.append(operation)
            return CommandHandle(
                command_id=len(self.operations),
                family=CommandFamily.DISCRETE_OPERATION,
                ack_support=AckSupport.UNSUPPORTED,
                accepted_at=1.0,
            )

    def recover(self, timeout: float) -> None:
        assert timeout > 0
        with self._state_lock:
            self._require_open_locked()

    def get_capabilities(self):
        return self.capabilities

    def get_snapshot(self):
        return self.snapshot

    def wait_for_snapshot(
        self,
        previous: HandSnapshot,
        timeout: float,
        *,
        wake_on_command_feedback: bool = True,
    ):
        assert isinstance(previous, HandSnapshot)
        assert timeout > 0
        assert isinstance(wake_on_command_feedback, bool)
        self.snapshot_wait_options.append(wake_on_command_feedback)
        return self.snapshot

    def get_health(self):
        return self.health

    def _require_open_locked(self) -> None:
        if not self._opened:
            raise ClosedError("fake runtime is closed")


class RuntimeFactory:
    def __init__(self) -> None:
        self.calls = 0
        self.runtime: FakeRuntime | None = None

    def __call__(self, **options: object) -> FakeRuntime:
        self.calls += 1
        self.runtime = FakeRuntime(**options)
        return self.runtime


def make_device(
    *,
    model: str = "l6",
    side: str | None = "left",
    transport: object | None = None,
    clock: object | None = None,
    protocol_backend: object | None = None,
    runtime_factory: object | None = None,
) -> HandDevice:
    return HandDevice(
        model=model,
        side=side,
        transport=SpyDependency() if transport is None else transport,
        clock=clock,
        protocol_backend=protocol_backend,
        runtime_factory=runtime_factory,  # ty: ignore[invalid-argument-type]
    )


def test_constructor_has_zero_io_threads_motion_and_dependency_calls() -> None:
    transport = SpyDependency()
    clock = SpyDependency()
    protocol = SpyDependency()
    factory = RuntimeFactory()
    threads_before = tuple(threading.enumerate())

    hand = make_device(
        transport=transport,
        clock=clock,
        protocol_backend=protocol,
        runtime_factory=factory,
    )

    assert tuple(threading.enumerate()) == threads_before
    assert transport.calls == []
    assert clock.calls == []
    assert protocol.calls == []
    assert factory.calls == 0
    assert hand.get_capabilities().identity.side is HandSide.LEFT
    assert hand.get_snapshot().connection_generation == 0
    assert not hand.get_health().connected
    assert transport.calls == []
    assert clock.calls == []


@pytest.mark.parametrize(("model", "side"), _MODEL_CONFIGS)
def test_initial_snapshot_and_health_are_shaped_from_capabilities(
    model: str,
    side: str | None,
) -> None:
    hand = make_device(model=model, side=side)
    capabilities = hand.get_capabilities()
    snapshot = hand.get_snapshot()
    health = hand.get_health()

    assert tuple(joint.name for joint in snapshot.joints) == capabilities.joint_names
    assert snapshot.angles is not None
    assert not snapshot.angles.meta.valid
    assert snapshot.angles.meta.stale
    declared_telemetry = {item.semantic for item in capabilities.telemetry}
    for semantic, attribute in _SNAPSHOT_ATTRIBUTES.items():
        sample = getattr(snapshot, attribute)
        assert (sample is not None) == (semantic in declared_telemetry)
        if sample is not None:
            assert not sample.meta.valid
            assert sample.meta.stale
    assert {item.channel for item in health.channels} == {
        semantic.value for semantic in declared_telemetry
    }
    assert all(item.pending == 0 for item in health.command_families)


def test_l30_omits_side_and_rejects_any_side_argument() -> None:
    hand = make_device(model="l30", side=None)

    assert hand.get_capabilities().side is HandSide.NOT_APPLICABLE
    with pytest.raises(ValidationError, match="must not specify side"):
        make_device(model="l30", side="left")


def test_only_one_device_facade_and_no_submit_aliases_are_public() -> None:
    assert "HandDevice" in hand_api.__all__
    assert "HandSession" not in hand_api.__all__
    assert not hasattr(HandDevice, "submit_command")
    assert not hasattr(HandDevice, "submit_angles")
    assert hasattr(HandDevice, "set_angles")
    assert hasattr(HandDevice, "set_percent_angles")
    assert hasattr(HandDevice, "get_angles")
    assert hasattr(HandDevice, "get_snapshot")
    assert hasattr(HandDevice, "wait_for_snapshot")


def test_wait_for_snapshot_is_one_explicit_cache_only_blocking_route() -> None:
    factory = RuntimeFactory()
    hand = make_device(runtime_factory=factory).open()
    previous = hand.get_snapshot()

    current = hand.wait_for_snapshot(previous, timeout=0.1)

    assert current is previous
    hand.wait_for_snapshot(
        previous,
        timeout=0.1,
        wake_on_command_feedback=False,
    )
    assert factory.runtime is not None
    assert factory.runtime.snapshot_wait_options == [True, False]
    with pytest.raises(ValidationError, match="previous"):
        hand.wait_for_snapshot(object(), timeout=0.1)  # ty: ignore[invalid-argument-type]
    with pytest.raises(ValidationError, match="wake_on_command_feedback"):
        hand.wait_for_snapshot(
            previous,
            timeout=0.1,
            wake_on_command_feedback=1,  # ty: ignore[invalid-argument-type]
        )


def test_value_validation_happens_before_closed_state_rejection() -> None:
    hand = make_device()

    with pytest.raises(ValidationError, match="requires 6 values"):
        hand.set_angles((0,))
    with pytest.raises(ValidationError, match="0 through 100"):
        hand.set_percent_angles((-1, 0, 0, 0, 0, 0))
    with pytest.raises(ClosedError):
        hand.set_angles((0, 0, 0, 0, 0, 0))


def test_explicit_open_creates_runtime_and_setters_remain_non_blocking() -> None:
    factory = RuntimeFactory()
    hand = make_device(runtime_factory=factory)

    assert hand.open() is hand
    handle = hand.set_percent_angles((0, 50, 100, 100, 100, 100))

    assert factory.calls == 1
    assert factory.runtime is not None
    assert factory.runtime.open_calls == 1
    assert factory.runtime.submissions == [
        (CommandSemantic.ANGLES, (0, 128, 255, 255, 255, 255))
    ]
    assert handle.stage.value == "accepted"
    hand.close()
    assert factory.runtime.close_calls == 1


def test_lifecycle_and_polling_methods_are_idempotent() -> None:
    factory = RuntimeFactory()
    hand = make_device(runtime_factory=factory)
    plan = PollingPlan(angles_hz=60)

    hand.open()
    hand.open()
    hand.start_polling(plan)
    hand.start_polling(plan)
    hand.stop_polling()
    hand.stop_polling()
    hand.close()
    hand.close()

    assert factory.runtime is not None
    assert factory.runtime.open_calls == 1
    assert factory.runtime.polling_plans == [plan]
    assert factory.runtime.stop_polling_calls == 1
    assert factory.runtime.close_calls == 1


def test_failed_close_can_be_retried_before_reopen() -> None:
    factory = RuntimeFactory()
    hand = make_device(runtime_factory=factory).open()
    assert factory.runtime is not None
    factory.runtime.close_failures_remaining = 1

    with pytest.raises(RuntimeError, match="simulated close failure"):
        hand.close()
    with pytest.raises(LifecycleError, match="retry close"):
        hand.open()

    hand.close()
    hand.close()
    hand.open()

    assert factory.runtime.close_calls == 2
    assert factory.runtime.open_calls == 2


def test_cached_views_do_not_wait_for_concurrent_close() -> None:
    factory = RuntimeFactory()
    hand = make_device(runtime_factory=factory).open()
    assert factory.runtime is not None
    release = threading.Event()
    factory.runtime.close_release = release
    close_thread = threading.Thread(target=hand.close)
    close_thread.start()
    assert factory.runtime.close_entered.wait(timeout=1)

    reads_finished = threading.Event()
    command_rejected = threading.Event()

    def read_cached_views() -> None:
        hand.get_capabilities()
        hand.get_snapshot()
        hand.get_health()
        try:
            hand.set_angles((0,) * 6)
        except ClosedError:
            command_rejected.set()
        reads_finished.set()

    read_thread = threading.Thread(target=read_cached_views)
    read_thread.start()
    completed_before_close = reads_finished.wait(timeout=1)
    release.set()
    close_thread.join(timeout=1)
    read_thread.join(timeout=1)

    assert completed_before_close
    assert command_rejected.is_set()
    assert not close_thread.is_alive()
    assert not read_thread.is_alive()


def test_active_get_angles_uses_fresh_runtime_transaction_contract() -> None:
    factory = RuntimeFactory()
    hand = make_device(runtime_factory=factory).open()

    sample = hand.get_angles(timeout=0.1)

    assert isinstance(sample, AngleSample)


@pytest.mark.parametrize(("model", "side"), _MODEL_CONFIGS)
def test_every_declared_command_has_one_typed_facade_route(
    model: str,
    side: str | None,
) -> None:
    factory = RuntimeFactory()
    hand = make_device(model=model, side=side, runtime_factory=factory).open()
    capabilities = hand.get_capabilities()

    for command in capabilities.commands:
        values = tuple(
            command.range_for(index).minimum for index in range(len(command.channels))
        )
        getattr(hand, _COMMAND_METHODS[command.semantic])(values)
        assert factory.runtime is not None
        assert factory.runtime.submissions[-1] == (command.semantic, values)


@pytest.mark.parametrize(("model", "side"), _MODEL_CONFIGS)
def test_every_declared_telemetry_channel_has_one_typed_facade_route(
    model: str,
    side: str | None,
) -> None:
    factory = RuntimeFactory()
    hand = make_device(model=model, side=side, runtime_factory=factory).open()

    for telemetry in hand.get_capabilities().telemetry:
        sample = getattr(hand, _TELEMETRY_METHODS[telemetry.semantic])(timeout=0.1)
        assert sample is getattr(
            hand.get_snapshot(),
            _SNAPSHOT_ATTRIBUTES[telemetry.semantic],
        )


def test_unsupported_capability_is_reported_before_closed_state() -> None:
    hand = make_device(model="o6", side="right")

    with pytest.raises(UnsupportedOperationError):
        hand.get_speeds(timeout=0.1)
    with pytest.raises(UnsupportedOperationError):
        hand.set_torques((0,) * 6)
    with pytest.raises(UnsupportedOperationError):
        hand.set_percent_speeds((0,) * 6)


@pytest.mark.parametrize("timeout", (0, -1, float("nan"), float("inf"), True))
def test_timeout_validation_happens_before_closed_state(timeout: object) -> None:
    hand = make_device()

    with pytest.raises(ValidationError, match="timeout"):
        hand.get_angles(timeout=timeout)  # ty: ignore[invalid-argument-type]


def test_declared_discrete_operations_route_through_bounded_operation_api() -> None:
    factory = RuntimeFactory()
    hand = make_device(
        model="l30",
        side=None,
        runtime_factory=factory,
    ).open()

    hand.enable()
    hand.disable()
    hand.calibrate()

    assert factory.runtime is not None
    assert factory.runtime.operations == ["enable", "disable", "calibrate"]


@pytest.mark.parametrize("name", _PUBLIC_METHODS)
def test_single_facade_freezes_required_method_names(name: str) -> None:
    assert callable(getattr(HandDevice, name))


def test_values_parameters_accept_general_iterables() -> None:
    factory = RuntimeFactory()
    hand = make_device(runtime_factory=factory).open()
    values: Iterable[int] = (value for value in range(6))

    hand.set_angles(values)

    assert factory.runtime is not None
    assert factory.runtime.submissions[-1][1] == (0, 1, 2, 3, 4, 5)


def test_public_facade_contains_no_second_calling_style() -> None:
    public_methods = {
        name
        for name in dir(HandDevice)
        if not name.startswith("_") and callable(getattr(HandDevice, name))
    }

    assert public_methods == set(_PUBLIC_METHODS)
