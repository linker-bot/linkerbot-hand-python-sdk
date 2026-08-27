from __future__ import annotations

import threading

import pytest

from linkerbot_hand.api.errors import (
    ClosedError,
    DeviceNotReadyError,
    QueueFullError,
    TimeoutError,
)
from linkerbot_hand.capabilities.types import TelemetrySemantic
from linkerbot_hand.runtime.transactions import TransactionCoordinator


class ManualClock:
    def __init__(self) -> None:
        self.now = 0.0
        self._condition = threading.Condition()

    def monotonic(self) -> float:
        with self._condition:
            return self.now

    def time(self) -> float:
        with self._condition:
            return self.now

    def wait(self, event: threading.Event, timeout: float | None) -> bool:
        with self._condition:
            deadline = None if timeout is None else self.now + timeout
            while not event.is_set():
                if deadline is not None and self.now >= deadline:
                    return False
                self._condition.wait(timeout=0.001)
            return True

    def advance(self, seconds: float) -> None:
        with self._condition:
            self.now += seconds
            self._condition.notify_all()


def make_coordinator(
    *, capacity: int = 4
) -> tuple[TransactionCoordinator, ManualClock, threading.Event]:
    clock = ManualClock()
    wake = threading.Event()
    coordinator = TransactionCoordinator(
        waiter_capacity=capacity,
        clock=clock,
        wake=wake.set,
    )
    return coordinator, clock, wake


def test_equivalent_active_reads_join_one_transaction() -> None:
    coordinator, _clock, wake = make_coordinator()
    coordinator.activate()

    first = coordinator.request(
        TelemetrySemantic.ANGLES,
        start_sequence=4,
        deadline_s=1.0,
    )
    second = coordinator.request(
        TelemetrySemantic.ANGLES,
        start_sequence=4,
        deadline_s=1.0,
    )

    assert wake.is_set()
    assert coordinator.pending_count == 1
    assert coordinator.waiter_count == 2
    claim = coordinator.take_next()
    assert claim is not None
    assert claim.key is TelemetrySemantic.ANGLES
    assert coordinator.take_next() is None

    result = object()
    coordinator.complete(TelemetrySemantic.ANGLES, result, sequence=5)
    assert first.wait() is result
    assert second.wait() is result
    assert coordinator.pending_count == 0


def test_poll_joins_an_inflight_active_read_without_a_second_claim() -> None:
    coordinator, _clock, _wake = make_coordinator()
    coordinator.activate()
    waiter = coordinator.request(
        TelemetrySemantic.CURRENTS,
        start_sequence=0,
        deadline_s=1.0,
    )
    first_claim = coordinator.take_next()
    assert first_claim is not None

    assert coordinator.request_poll(TelemetrySemantic.CURRENTS)
    assert coordinator.pending_count == 1
    assert coordinator.take_next() is None

    coordinator.complete(TelemetrySemantic.CURRENTS, (1, 2), sequence=1)
    assert waiter.wait() == (1, 2)


def test_one_waiter_timeout_does_not_cancel_shared_transaction() -> None:
    coordinator, clock, _wake = make_coordinator()
    coordinator.activate()
    expired = coordinator.request(
        TelemetrySemantic.ANGLES,
        start_sequence=1,
        deadline_s=0.1,
    )
    remaining = coordinator.request(
        TelemetrySemantic.ANGLES,
        start_sequence=1,
        deadline_s=1.0,
    )
    assert coordinator.take_next() is not None

    clock.advance(0.1)
    with pytest.raises(TimeoutError):
        expired.wait()

    assert coordinator.pending_count == 1
    assert coordinator.waiter_count == 1
    coordinator.complete(TelemetrySemantic.ANGLES, "fresh", sequence=2)
    assert remaining.wait() == "fresh"


def test_waiters_are_bounded_even_when_they_coalesce() -> None:
    coordinator, _clock, _wake = make_coordinator(capacity=2)
    coordinator.activate()
    coordinator.request(
        TelemetrySemantic.ANGLES,
        start_sequence=0,
        deadline_s=1.0,
    )
    coordinator.request(
        TelemetrySemantic.ANGLES,
        start_sequence=0,
        deadline_s=1.0,
    )

    with pytest.raises(QueueFullError):
        coordinator.request(
            TelemetrySemantic.ANGLES,
            start_sequence=0,
            deadline_s=1.0,
        )


def test_generation_end_rejects_new_reads_and_fails_existing_waiters() -> None:
    coordinator, _clock, _wake = make_coordinator()
    with pytest.raises(TimeoutError, match="not available"):
        coordinator.request(
            TelemetrySemantic.ANGLES,
            start_sequence=0,
            deadline_s=1.0,
        )

    coordinator.activate()
    waiter = coordinator.request(
        TelemetrySemantic.ANGLES,
        start_sequence=0,
        deadline_s=1.0,
    )
    error = DeviceNotReadyError("connection ended", generation=1)
    coordinator.reject_new(error)
    coordinator.fail_all(error)

    with pytest.raises(DeviceNotReadyError, match="connection ended"):
        waiter.wait()
    with pytest.raises(DeviceNotReadyError, match="connection ended"):
        coordinator.request(
            TelemetrySemantic.ANGLES,
            start_sequence=0,
            deadline_s=1.0,
        )


def test_pending_waiter_cancel_removes_unused_transaction() -> None:
    coordinator, clock, _wake = make_coordinator()
    coordinator.activate()
    waiter = coordinator.request(
        TelemetrySemantic.ANGLES,
        start_sequence=0,
        deadline_s=0.1,
    )
    clock.advance(0.1)

    with pytest.raises(TimeoutError):
        waiter.wait()

    assert coordinator.pending_count == 0
    coordinator.reject_new(ClosedError("closed"))
