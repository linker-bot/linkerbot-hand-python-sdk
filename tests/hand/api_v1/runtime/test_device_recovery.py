"""Connection generation, recovery, and lifecycle-admission contracts."""

from __future__ import annotations

import threading
import time

import pytest

from linkerbot_hand.api import (
    CommandStage,
    DeviceConfig,
    DeviceNotReadyError,
    HandError,
    IdentityMismatchError,
    PollingPlan,
    TimeoutError,
)
from linkerbot_hand.capabilities.types import CommandSemantic, TelemetrySemantic

from .factories import (
    hold_next_idle_deadline_check,
    make_hand,
    make_shared_hands,
    opened_runtime,
)
from .fakes import wait_until


def test_failed_recovery_baseline_rolls_back_to_invalid_previous_generation() -> None:
    hand, state, transport = make_hand()
    hand.open()
    generation = hand.get_health().connection_generation
    transport.current_dispatcher.disconnect()
    wait_until(lambda: not hand.get_health().connected)
    with state.lock:
        state.drop_reads.add(TelemetrySemantic.ANGLES)

    try:
        with pytest.raises(TimeoutError, match="response"):
            hand.recover(timeout=1)

        snapshot = hand.get_snapshot()
        assert snapshot.connection_generation == generation
        assert snapshot.device_info is None
        assert snapshot.angles is not None and not snapshot.angles.meta.valid
        assert hand.get_health().connection_generation == generation
        assert not hand.get_health().connected
    finally:
        with state.lock:
            state.drop_reads.clear()
        hand.close()


def test_recovery_admission_rejects_new_polling_plan_and_restores_old_plan() -> None:
    hand, _state, transport = make_hand()
    hand.open()
    original = PollingPlan(angles_hz=10)
    hand.start_polling(original)
    transport.recover_started.clear()
    transport.recover_gate.clear()
    errors: list[HandError] = []

    def recover() -> None:
        try:
            hand.recover(timeout=1.5)
        except HandError as error:
            errors.append(error)

    recovery = threading.Thread(target=recover)
    recovery.start()
    assert transport.recover_started.wait(timeout=1)
    try:
        with pytest.raises(DeviceNotReadyError):
            hand.start_polling(PollingPlan(angles_hz=20))
    finally:
        transport.recover_gate.set()
    recovery.join(timeout=2)

    try:
        assert not recovery.is_alive()
        assert errors == []
        health = hand.get_health()
        assert health.connected
        angles = next(item for item in health.channels if item.channel == "angles")
        assert angles.requested_hz == 10
    finally:
        hand.close()


def test_automatic_reconnect_verifies_baseline_and_advances_generation() -> None:
    hand, _state, transport = make_hand(
        config=DeviceConfig(
            auto_reconnect=True,
            reconnect_initial_delay_s=0.01,
            reconnect_max_delay_s=0.02,
        )
    )
    hand.open()
    generation = hand.get_health().connection_generation
    try:
        transport.current_dispatcher.disconnect()

        wait_until(
            lambda: (
                hand.get_health().connected
                and hand.get_health().connection_generation == generation + 1
            ),
            timeout_s=1.5,
        )

        health = hand.get_health()
        assert health.reconnect_count == 1
        snapshot = hand.get_snapshot()
        assert snapshot.connection_generation == generation + 1
        assert snapshot.angles is not None and snapshot.angles.meta.valid
    finally:
        hand.close()


def test_close_open_rejects_a_replacement_physical_device() -> None:
    hand, state, _transport = make_hand()
    hand.open()
    generation = hand.get_health().connection_generation
    hand.close()
    with state.lock:
        state.fingerprint = "REPLACEMENT"

    with pytest.raises(IdentityMismatchError, match="fingerprint changed"):
        hand.open()

    assert not hand.get_health().connected
    assert hand.get_health().connection_generation == generation
    snapshot = hand.get_snapshot()
    assert snapshot.connection_generation == generation
    assert snapshot.angles is not None and not snapshot.angles.meta.valid
    hand.close()


def test_shared_endpoints_coalesce_one_physical_transport_recovery() -> None:
    config = DeviceConfig(
        auto_reconnect=True,
        reconnect_initial_delay_s=0.01,
        reconnect_max_delay_s=0.02,
    )
    l6, _l6_state, o6, _o6_state, transport = make_shared_hands(config=config)
    l6.open()
    o6.open()
    try:
        transport.current_dispatcher.disconnect()

        wait_until(
            lambda: (
                l6.get_health().connected
                and o6.get_health().connected
                and l6.get_health().connection_generation == 2
                and o6.get_health().connection_generation == 2
            ),
            timeout_s=2,
        )

        assert len(transport.dispatchers) == 2
        assert l6.get_health().reconnect_count == 1
        assert o6.get_health().reconnect_count == 1
    finally:
        l6.close()
        o6.close()


def test_recovery_on_one_physical_transport_does_not_block_another() -> None:
    recovering_hand, _recovering_state, recovering_transport = make_hand()
    independent_hand, _independent_state, _independent_transport = make_hand()
    recovering_hand.open()
    independent_hand.open()
    recovering_transport.recover_started.clear()
    recovering_transport.recover_gate.clear()
    errors: list[BaseException] = []

    def recover() -> None:
        try:
            recovering_hand.recover(timeout=1.5)
        except BaseException as error:  # noqa: BLE001 - test thread boundary
            errors.append(error)

    recovery = threading.Thread(target=recover)
    recovery.start()
    assert recovering_transport.recover_started.wait(timeout=1)
    try:
        handle = independent_hand.set_angles((25,) * 6)

        assert handle.wait(timeout=0.5) is CommandStage.DISPATCHED
        assert independent_hand.get_health().connected
    finally:
        recovering_transport.recover_gate.set()
        recovery.join(timeout=2)
        recovering_hand.close()
        independent_hand.close()

    assert not recovery.is_alive()
    assert errors == []


def test_recovery_rebuilds_tx_arbiter_after_transport_advanced_then_raised() -> None:
    hand, _state, transport = make_hand()
    hand.open()
    transport.current_dispatcher.disconnect()
    wait_until(lambda: not hand.get_health().connected)
    transport.recover_error_after_open = RuntimeError(
        "simulated post-open recovery failure"
    )
    try:
        with pytest.raises(HandError, match="post-open recovery failure"):
            hand.recover(timeout=1)

        assert transport.get_health().connected
        assert not hand.get_health().connected
        transport.recover_error_after_open = None

        hand.recover(timeout=1)

        handle = hand.set_angles((10,) * 6)
        assert handle.wait(timeout=1) is CommandStage.DISPATCHED
        assert len(transport.dispatchers) == 2
    finally:
        transport.recover_error_after_open = None
        hand.close()


def test_recovery_drops_a_target_queued_behind_another_endpoint() -> None:
    l6, l6_state, o6, _o6_state, transport = make_shared_hands()
    l6.open()
    o6.open()
    transport.command_send_started.clear()
    transport.command_gate.clear()
    errors: list[BaseException] = []
    try:
        with l6_state.lock:
            l6_state.command_log.clear()
        blocker = o6.set_angles((30,) * 6)
        assert transport.command_send_started.wait(timeout=1)
        stale = l6.set_angles((10,) * 6)
        wait_until(lambda: l6.get_health().transport.pending_tx >= 2)

        def recover() -> None:
            try:
                l6.recover(timeout=2)
            except BaseException as error:  # noqa: BLE001 - test thread boundary
                errors.append(error)

        recovery = threading.Thread(target=recover)
        recovery.start()
        wait_until(lambda: stale.stage is CommandStage.FAILED)

        with l6_state.lock:
            assert l6_state.command_log == []
        transport.command_gate.set()
        recovery.join(timeout=2)

        assert not recovery.is_alive()
        assert errors == []
        assert blocker.wait(timeout=1) is CommandStage.DISPATCHED
        assert stale.stage is CommandStage.FAILED
        with l6_state.lock:
            assert l6_state.command_log == []

        fresh = l6.set_angles((20,) * 6)
        assert fresh.wait(timeout=1) is CommandStage.DISPATCHED
        with l6_state.lock:
            assert l6_state.command_log == [(CommandSemantic.ANGLES, (20,) * 6)]
    finally:
        transport.command_gate.set()
        l6.close()
        o6.close()


def test_recovery_never_replaces_an_arbiter_whose_claimed_send_did_not_drain() -> None:
    hand, _state, transport = make_hand(config=DeviceConfig(shutdown_timeout_s=0.02))
    hand.open()
    transport.command_send_started.clear()
    transport.command_gate.clear()
    stale = hand.set_angles((10,) * 6)
    assert transport.command_send_started.wait(timeout=1)
    try:
        with pytest.raises(TimeoutError, match="stop in time"):
            hand.recover(timeout=0.5)
        assert stale.stage is CommandStage.ACCEPTED
        assert len(transport.dispatchers) == 1

        with pytest.raises(TimeoutError, match="stop in time"):
            hand.recover(timeout=0.5)
        assert len(transport.dispatchers) == 1

        transport.command_gate.set()
        assert stale.wait(timeout=1) is CommandStage.DISPATCHED
        hand.recover(timeout=0.5)

        assert len(transport.dispatchers) == 2
        fresh = hand.set_angles((20,) * 6)
        assert fresh.wait(timeout=1) is CommandStage.DISPATCHED
    finally:
        transport.command_gate.set()
        hand.close()


def test_idle_explicit_recovery_cannot_be_lost_before_actor_wait() -> None:
    hand, _state, _transport = make_hand()
    hand.open()
    runtime = opened_runtime(hand)
    entered, release, restore = hold_next_idle_deadline_check(hand)
    errors: list[BaseException] = []

    def recover() -> None:
        try:
            hand.recover(timeout=1)
        except BaseException as error:  # noqa: BLE001 - test thread boundary
            errors.append(error)

    recovery = threading.Thread(target=recover)
    try:
        assert entered.wait(timeout=1)
        recovery.start()
        wait_until(runtime._actor._recovery_requested)
        restore()
        release.set()
        recovery.join(timeout=1.5)

        assert not recovery.is_alive()
        assert errors == []
        assert hand.get_health().connection_generation == 2
    finally:
        restore()
        release.set()
        recovery.join(timeout=1.5)
        hand.close()


def test_faulted_actor_waits_instead_of_spinning_on_a_closed_route() -> None:
    hand, _state, transport = make_hand()
    hand.open()
    runtime = opened_runtime(hand)
    actor = runtime._actor
    original = actor._wait_for_work
    calls = 0
    calls_lock = threading.Lock()

    def counted_wait() -> None:
        nonlocal calls
        with calls_lock:
            calls += 1
        original()

    actor._wait_for_work = counted_wait  # ty: ignore[invalid-assignment]
    try:
        transport.current_dispatcher.disconnect()
        wait_until(lambda: not hand.get_health().connected)
        time.sleep(0.05)

        with calls_lock:
            assert calls <= 2
    finally:
        actor._wait_for_work = original  # ty: ignore[invalid-assignment]
        hand.close()


def test_recovery_baseline_remains_hidden_until_generation_commit() -> None:
    hand, _state, transport = make_hand()
    hand.open()
    old_generation = hand.get_snapshot().connection_generation
    transport.current_dispatcher.disconnect()
    wait_until(lambda: not hand.get_health().connected)
    transport.read_send_started.clear()
    transport.read_gate.clear()
    errors: list[BaseException] = []

    def recover() -> None:
        try:
            hand.recover(timeout=1)
        except BaseException as error:  # noqa: BLE001 - test thread boundary
            errors.append(error)

    recovery = threading.Thread(target=recover)
    recovery.start()
    try:
        assert transport.read_send_started.wait(timeout=1)
        during = hand.get_snapshot()
        assert during.connection_generation == old_generation
        assert during.angles is not None and not during.angles.meta.valid

        transport.read_gate.set()
        recovery.join(timeout=1)

        assert not recovery.is_alive()
        assert errors == []
        after = hand.get_snapshot()
        assert after.connection_generation == old_generation + 1
        assert after.angles is not None and after.angles.meta.valid
    finally:
        transport.read_gate.set()
        recovery.join(timeout=1)
        hand.close()
