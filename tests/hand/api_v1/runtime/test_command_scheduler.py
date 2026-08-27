from __future__ import annotations

import threading

import pytest

from linkerbot_hand.api.commands import AckSupport, CommandFamily, CommandStage
from linkerbot_hand.api.errors import ClosedError, DeviceNotReadyError, QueueFullError
from linkerbot_hand.capabilities.types import CommandSemantic
from linkerbot_hand.runtime.command_scheduler import (
    CommandIngress,
    SchedulingPolicy,
)


class MonotonicCounter:
    def __init__(self) -> None:
        self._value = 0.0
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            self._value += 0.001
            return self._value


def make_ingress(*, capacity: int = 4) -> tuple[CommandIngress, threading.Event]:
    wake = threading.Event()
    ingress = CommandIngress(
        discrete_capacity=capacity,
        monotonic=MonotonicCounter(),
        wake=wake.set,
    )
    return ingress, wake


def test_lifecycle_admission_and_latest_slot_share_one_boundary() -> None:
    ingress, wake = make_ingress()

    with pytest.raises(ClosedError):
        ingress.submit(
            CommandSemantic.ANGLES,
            (1, 2),
            ack_support=AckSupport.UNSUPPORTED,
        )

    ingress.activate(generation=1)
    first = ingress.submit(
        CommandSemantic.ANGLES,
        (1, 2),
        ack_support=AckSupport.UNSUPPORTED,
    )
    second = ingress.submit(
        CommandSemantic.ANGLES,
        (3, 4),
        ack_support=AckSupport.UNSUPPORTED,
    )

    assert wake.is_set()
    assert first.stage is CommandStage.SUPERSEDED
    assert first.done
    assert second.stage is CommandStage.ACCEPTED
    assert ingress.pending_count == 1

    ingress.reject_new(DeviceNotReadyError("connection lost", generation=1))
    with pytest.raises(DeviceNotReadyError, match="connection lost"):
        ingress.submit(
            CommandSemantic.ANGLES,
            (5, 6),
            ack_support=AckSupport.UNSUPPORTED,
        )

    claimed = ingress.take_next()
    assert claimed is not None
    assert claimed.values == (3, 4)


def test_latest_wins_is_independent_for_each_continuous_family() -> None:
    ingress, _ = make_ingress()
    ingress.activate(generation=1)

    angle = ingress.submit(
        CommandSemantic.ANGLES,
        (10,),
        ack_support=AckSupport.UNSUPPORTED,
    )
    speed = ingress.submit(
        CommandSemantic.SPEEDS,
        (20,),
        ack_support=AckSupport.UNSUPPORTED,
    )
    current = ingress.submit(
        CommandSemantic.CURRENT_LIMITS,
        (30,),
        ack_support=AckSupport.UNSUPPORTED,
    )
    effort = ingress.submit(
        CommandSemantic.EFFORT_LIMITS,
        (40,),
        ack_support=AckSupport.UNSUPPORTED,
    )

    assert ingress.pending_count == 4
    claimed = tuple(ingress.take_next() for _ in range(4))
    assert all(item is not None for item in claimed)
    assert {item.handle for item in claimed if item is not None} == {
        angle,
        speed,
        current,
        effort,
    }


def test_configuration_and_discrete_operations_share_a_bounded_fifo() -> None:
    ingress, _ = make_ingress(capacity=3)
    ingress.activate(generation=1)

    acceleration = ingress.submit(
        CommandSemantic.ACCELERATIONS,
        (1,),
        ack_support=AckSupport.UNSUPPORTED,
    )
    stall = ingress.submit(
        CommandSemantic.STALL_THRESHOLDS,
        (2,),
        ack_support=AckSupport.SUPPORTED,
    )
    clear = ingress.submit_operation(
        "clear_faults",
        ack_support=AckSupport.SUPPORTED,
    )

    with pytest.raises(QueueFullError):
        ingress.submit_operation("enable", ack_support=AckSupport.SUPPORTED)

    queued = tuple(ingress.take_next() for _ in range(3))
    assert tuple(item.handle for item in queued if item is not None) == (
        acceleration,
        stall,
        clear,
    )
    assert all(
        item is not None and item.policy is SchedulingPolicy.FIFO for item in queued
    )


def test_high_rate_submission_remains_capacity_one_and_sends_final_target() -> None:
    ingress, _ = make_ingress()
    ingress.activate(generation=1)

    handles = [
        ingress.submit(
            CommandSemantic.ANGLES,
            (value,),
            ack_support=AckSupport.UNSUPPORTED,
        )
        for value in range(1_000)
    ]

    assert ingress.pending_count == 1
    assert all(handle.stage is CommandStage.SUPERSEDED for handle in handles[:-1])
    final = ingress.take_next()
    assert final is not None
    assert final.values == (999,)
    assert final.handle is handles[-1]

    metrics = {item.family: item for item in ingress.metrics()}
    angles = metrics[CommandFamily.ANGLE_TARGET]
    assert angles.accepted == 1_000
    assert angles.superseded == 999
    assert angles.pending == 0


def test_generation_end_fails_every_unclaimed_command_without_replay() -> None:
    ingress, _ = make_ingress()
    ingress.activate(generation=7)
    angle = ingress.submit(
        CommandSemantic.ANGLES,
        (10,),
        ack_support=AckSupport.UNSUPPORTED,
    )
    operation = ingress.submit_operation(
        "calibrate",
        ack_support=AckSupport.UNSUPPORTED,
    )
    error = DeviceNotReadyError("generation ended", generation=7)

    ingress.reject_new(error)
    failed = ingress.fail_pending(error)

    assert failed == (angle, operation)
    assert angle.stage is CommandStage.FAILED
    assert operation.stage is CommandStage.FAILED
    assert ingress.pending_count == 0

    ingress.activate(generation=8)
    assert ingress.take_next() is None


def test_concurrent_close_gate_never_accepts_after_rejection_returns() -> None:
    ingress, _ = make_ingress()
    ingress.activate(generation=1)
    rejected = threading.Event()
    accepted_after_rejection: list[object] = []

    def submitter() -> None:
        value = 0
        while not rejected.is_set():
            try:
                ingress.submit(
                    CommandSemantic.ANGLES,
                    (value,),
                    ack_support=AckSupport.UNSUPPORTED,
                )
            except ClosedError:
                break
            value += 1
        for _ in range(100):
            try:
                accepted_after_rejection.append(
                    ingress.submit(
                        CommandSemantic.ANGLES,
                        (value,),
                        ack_support=AckSupport.UNSUPPORTED,
                    )
                )
            except ClosedError:
                pass

    thread = threading.Thread(target=submitter)
    thread.start()
    ingress.reject_new(ClosedError("closing", generation=1))
    rejected.set()
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert accepted_after_rejection == []
