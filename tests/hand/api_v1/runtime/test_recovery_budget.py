"""Recovery execution deadlines are separate from each caller's wait budget."""

from __future__ import annotations

import threading
import time

import pytest

from linkerbot_hand import DeviceConfig, HandError, TimeoutError, ValidationError
from linkerbot_hand.capabilities.types import TelemetrySemantic

from .factories import make_hand, opened_runtime
from .fakes import wait_until


def test_recovery_budget_stops_baseline_and_keeps_old_generation_invalid() -> None:
    hand, state, _transport = make_hand(
        config=DeviceConfig(recovery_timeout_s=0.06, transaction_timeout_s=1)
    )
    hand.open()
    state.drop_reads.add(TelemetrySemantic.ANGLES)
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError, match="recovery execution budget"):
            hand.recover(timeout=1)

        assert time.monotonic() - started < 0.4
        assert hand.get_health().connection_generation == 1
        assert not hand.get_health().connected
        angles = hand.get_snapshot().angles
        assert angles is not None and not angles.meta.valid
    finally:
        hand.close()


def test_recovery_wait_budget_includes_claimed_send_drain() -> None:
    hand, _state, transport = make_hand(config=DeviceConfig(shutdown_timeout_s=1))
    hand.open()
    transport.command_gate.clear()
    hand.set_angles((10,) * 6)
    assert transport.command_send_started.wait(1)
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError, match="did not drain"):
            hand.recover(timeout=0.04)

        assert time.monotonic() - started < 0.3
        assert len(transport.dispatchers) == 1
    finally:
        transport.command_gate.set()
        hand.close()


def test_short_caller_does_not_cancel_shared_recovery_or_extend_its_deadline() -> None:
    hand, _state, transport = make_hand(config=DeviceConfig(recovery_timeout_s=1))
    hand.open()
    transport.recover_gate.clear()
    errors: list[HandError] = []

    def recover() -> None:
        try:
            hand.recover(timeout=1)
        except HandError as error:
            errors.append(error)

    thread = threading.Thread(target=recover)
    thread.start()
    try:
        assert transport.recover_started.wait(1)
        deadline = opened_runtime(hand)._actor._recovery.deadline_at_s
        with pytest.raises(TimeoutError, match="recover did not complete"):
            hand.recover(timeout=0.02)
        assert opened_runtime(hand)._actor._recovery.deadline_at_s == deadline
        transport.recover_gate.set()
        thread.join(1)

        assert not thread.is_alive()
        assert not errors
        assert hand.get_health().connection_generation == 2
        assert hand.get_health().connected
    finally:
        transport.recover_gate.set()
        thread.join(2)
        hand.close()


def test_blocking_transport_return_after_budget_cannot_publish_success() -> None:
    hand, _state, transport = make_hand(config=DeviceConfig(recovery_timeout_s=0.04))
    hand.open()
    transport.recover_gate.clear()
    try:
        with pytest.raises(TimeoutError, match="recover did not complete"):
            hand.recover(timeout=0.08)
        assert transport.recover_started.is_set()
        transport.recover_gate.set()
        runtime = opened_runtime(hand)
        wait_until(lambda: not runtime._actor._performing_recovery)

        assert not hand.get_health().connected
        assert hand.get_health().connection_generation == 1
        angles = hand.get_snapshot().angles
        assert angles is not None and not angles.meta.valid
    finally:
        transport.recover_gate.set()
        hand.close()


def test_waiting_for_lifecycle_lock_uses_the_caller_budget() -> None:
    hand, _state, _transport = make_hand()
    hand.open()
    runtime = opened_runtime(hand)
    errors: list[HandError] = []

    def recover() -> None:
        try:
            hand.recover(timeout=0.03)
        except HandError as error:
            errors.append(error)

    try:
        with runtime._operation_lock:
            thread = threading.Thread(target=recover)
            thread.start()
            thread.join(0.3)
            assert not thread.is_alive()
        assert len(errors) == 1
        assert isinstance(errors[0], TimeoutError)
        assert hand.get_health().connected
        assert hand.get_health().connection_generation == 1
    finally:
        hand.close()


@pytest.mark.parametrize("value", (0, -1, True, float("nan"), float("inf"), None))
def test_recovery_budget_must_be_finite_positive(value: object) -> None:
    with pytest.raises(ValidationError, match="recovery_timeout_s"):
        DeviceConfig(recovery_timeout_s=value)  # ty: ignore[invalid-argument-type]
