from __future__ import annotations

import threading
import time

import pytest

from linkerbot_hand.api import (
    AngleSample,
    ClosedError,
    CommandStage,
    DeviceConfig,
    DeviceNotReadyError,
    ErrorCode,
    IdentityMismatchError,
    LifecycleError,
    PollingPlan,
)
from linkerbot_hand.capabilities.types import CommandSemantic, TelemetrySemantic

from .factories import make_duplicate_endpoint_hands, make_hand, make_shared_hands
from .fakes import wait_until


def test_constructor_is_zero_io_and_open_verifies_identity_and_baseline() -> None:
    hand, state, transport = make_hand()

    assert transport.dispatchers == []
    assert not hand.get_capabilities().identity.verified
    initial_angles = hand.get_snapshot().angles
    assert initial_angles is not None and not initial_angles.meta.valid

    hand.open()
    try:
        assert hand.get_capabilities().identity.verified
        snapshot = hand.get_snapshot()
        assert snapshot.connection_generation == 1
        assert snapshot.device_info is not None
        assert snapshot.device_info.serial_number == "FAKE-L6-0001"
        assert snapshot.angles is not None and snapshot.angles.meta.valid
        assert state.query_log == [TelemetrySemantic.ANGLES]
    finally:
        hand.close()


def test_one_runtime_consumes_multiple_exact_routes_from_one_subscription() -> None:
    hand, state, transport = make_hand(
        route_keys=("fake-hand-identity", "fake-hand-state")
    )

    hand.open()
    try:
        assert state.query_log == [TelemetrySemantic.ANGLES]
        assert transport.get_health().route_count == 2
        angles = hand.get_angles(timeout=0.1)
        assert angles.meta.valid
        assert transport.get_health().route_count == 2
    finally:
        hand.close()

    assert transport.get_health().route_count == 0


def test_shared_devices_report_the_same_physical_transport_traffic() -> None:
    l6, _l6_state, o6, _o6_state, transport = make_shared_hands()
    l6.open()
    o6.open()
    try:
        before = transport.get_health()
        assert before.tx_frames > 0
        assert before.tx_bytes > 0
        assert before.rx_frames > 0
        assert before.rx_bytes > 0

        for hand in (l6, o6):
            projected = hand.get_health().transport
            assert projected.tx_frames == before.tx_frames
            assert projected.tx_bytes == before.tx_bytes
            assert projected.rx_frames == before.rx_frames
            assert projected.rx_bytes == before.rx_bytes

        for _ in range(100):
            l6.get_health()
            o6.get_health()
        after_health_reads = transport.get_health()
        assert after_health_reads.tx_frames == before.tx_frames
        assert after_health_reads.tx_bytes == before.tx_bytes
        assert after_health_reads.rx_frames == before.rx_frames
        assert after_health_reads.rx_bytes == before.rx_bytes

        handle = l6.set_angles((10,) * 6)
        assert handle.wait(timeout=1) is CommandStage.DISPATCHED
        after_command = transport.get_health()
        assert after_command.tx_frames == before.tx_frames + 1
        assert after_command.tx_bytes > before.tx_bytes

        for hand in (l6, o6):
            projected = hand.get_health().transport
            assert projected.tx_frames == after_command.tx_frames
            assert projected.tx_bytes == after_command.tx_bytes
            assert projected.rx_frames == after_command.rx_frames
            assert projected.rx_bytes == after_command.rx_bytes
    finally:
        l6.close()
        o6.close()


@pytest.mark.parametrize(
    ("route_keys", "message"),
    (
        ((), "non-empty tuple"),
        (("duplicate", "duplicate"), "unique"),
    ),
)
def test_protocol_route_group_rejects_empty_or_duplicate_keys(
    route_keys: tuple[str, ...],
    message: str,
) -> None:
    hand, _state, transport = make_hand()
    transport.backends["fake-hand"].route_keys = route_keys

    with pytest.raises(LifecycleError, match=message):
        hand.open()

    hand.close()


def test_identity_refresh_faults_a_replaced_device_and_invalidates_state() -> None:
    hand, state, _transport = make_hand()
    hand.open()
    with state.lock:
        state.fingerprint = "REPLACEMENT"
    try:
        with pytest.raises(IdentityMismatchError, match="fingerprint changed"):
            hand.get_device_info(timeout=1)

        wait_until(lambda: not hand.get_health().connected)
        health = hand.get_health()
        assert health.lifecycle_state.value == "faulted"
        assert health.recent_error is not None
        assert health.recent_error.code is ErrorCode.IDENTITY_MISMATCH
        angles = hand.get_snapshot().angles
        assert angles is not None and not angles.meta.valid
        with pytest.raises(DeviceNotReadyError):
            hand.set_angles((10,) * 6)
    finally:
        hand.close()


def test_command_is_nonblocking_and_dispatched_only_after_transport_send() -> None:
    hand, state, transport = make_hand()
    hand.open()
    transport.command_gate.clear()
    started = time.perf_counter()

    handle = hand.set_angles((10,) * 6)

    assert time.perf_counter() - started < 0.01
    assert handle.stage is CommandStage.ACCEPTED
    assert state.command_log == []
    transport.command_gate.set()
    assert handle.wait(timeout=1) is CommandStage.DISPATCHED
    assert state.command_log == [(CommandSemantic.ANGLES, (10,) * 6)]
    hand.close()


def test_close_timeout_is_retained_in_health_until_retry_succeeds() -> None:
    hand, _state, transport = make_hand(config=DeviceConfig(shutdown_timeout_s=0.02))
    hand.open()
    transport.command_send_started.clear()
    transport.command_gate.clear()
    hand.set_angles((10,) * 6)
    assert transport.command_send_started.wait(timeout=1)
    try:
        with pytest.raises(LifecycleError, match="did not close cleanly"):
            hand.close()

        health = hand.get_health()
        assert health.lifecycle_state.value == "faulted"
        assert health.recent_error is not None
        assert health.recent_error.code is ErrorCode.LIFECYCLE
    finally:
        transport.command_gate.set()
        hand.close()

    assert hand.get_health().lifecycle_state.value == "closed"


def test_equivalent_active_reads_coalesce_and_return_post_call_sample() -> None:
    hand, state, transport = make_hand()
    hand.open()
    with state.lock:
        state.query_log.clear()
        state.readings[TelemetrySemantic.ANGLES] = (25,) * 6
    transport.read_send_started.clear()
    transport.read_gate.clear()
    results: list[AngleSample] = []

    def read() -> None:
        results.append(hand.get_angles(timeout=1))

    first = threading.Thread(target=read)
    second = threading.Thread(target=read)
    first.start()
    assert transport.read_send_started.wait(timeout=1)
    second.start()
    transport.read_gate.set()
    first.join(timeout=1)
    second.join(timeout=1)

    assert not first.is_alive() and not second.is_alive()
    assert len(results) == 2
    assert results[0] is results[1]
    assert results[0].raw == (25,) * 6
    with state.lock:
        assert state.query_log == [TelemetrySemantic.ANGLES]
    hand.close()


def test_stop_polling_produces_no_later_sensor_queries_and_snapshot_is_zero_io() -> (
    None
):
    hand, state, _transport = make_hand()
    hand.open()
    with state.lock:
        state.query_log.clear()
    hand.start_polling(PollingPlan(angles_hz=50))
    wait_until(lambda: len(state.query_log) >= 3)
    hand.stop_polling()
    with state.lock:
        stopped_count = len(state.query_log)
    io_before = len(_transport.sent)

    for _ in range(10_000):
        hand.get_snapshot()
        hand.get_health()
    time.sleep(0.05)

    with state.lock:
        assert len(state.query_log) == stopped_count
    assert len(_transport.sent) == io_before
    hand.close()


def test_stop_polling_waits_for_a_claimed_query_before_returning() -> None:
    hand, state, transport = make_hand()
    hand.open()
    errors: list[BaseException] = []
    try:
        with state.lock:
            state.query_log.clear()
        transport.read_send_started.clear()
        transport.read_gate.clear()
        hand.start_polling(PollingPlan(angles_hz=10))
        assert transport.read_send_started.wait(timeout=1)

        def stop_polling() -> None:
            try:
                hand.stop_polling()
            except BaseException as error:  # noqa: BLE001 - test thread boundary
                errors.append(error)

        stopper = threading.Thread(target=stop_polling)
        stopper.start()
        time.sleep(0.02)
        assert stopper.is_alive()
        transport.read_gate.set()
        stopper.join(timeout=1)

        assert not stopper.is_alive()
        assert errors == []
        with state.lock:
            stopped_count = len(state.query_log)
        time.sleep(0.05)
        with state.lock:
            assert len(state.query_log) == stopped_count
    finally:
        transport.read_gate.set()
        hand.close()


def test_one_thousand_targets_remain_bounded_and_final_value_wins() -> None:
    hand, state, transport = make_hand()
    hand.open()
    try:
        with state.lock:
            state.command_log.clear()
        transport.command_send_started.clear()
        transport.command_gate.clear()

        first = hand.set_angles((0,) * 6)
        assert transport.command_send_started.wait(timeout=1)
        targets = [value % 256 for value in range(1, 1_001)]
        handles = [hand.set_angles((value,) * 6) for value in targets]

        assert hand.get_health().pending_commands <= 1
        assert all(handle.done for handle in handles[:-1])
        assert all(handle.stage is CommandStage.SUPERSEDED for handle in handles[:-1])
        transport.command_gate.set()
        assert first.wait(timeout=1) is CommandStage.DISPATCHED
        assert handles[-1].wait(timeout=1) is CommandStage.DISPATCHED
        with state.lock:
            assert state.command_log[-1] == (
                CommandSemantic.ANGLES,
                (targets[-1],) * 6,
            )
            assert len(state.command_log) == 2
        health = hand.get_health()
        assert health.superseded_commands >= 999
    finally:
        transport.command_gate.set()
        hand.close()


def test_recovery_discards_old_target_revalidates_identity_and_restores_polling() -> (
    None
):
    hand, state, transport = make_hand(
        config=DeviceConfig(command_rate_hz=1, auto_reconnect=False)
    )
    hand.open()
    hand.start_polling(PollingPlan(angles_hz=10))
    first = hand.set_angles((1,) * 6)
    assert first.wait(timeout=1) is CommandStage.DISPATCHED
    old = hand.set_angles((2,) * 6)

    transport.current_dispatcher.disconnect()
    wait_until(lambda: not hand.get_health().connected)

    assert old.wait(timeout=1) is CommandStage.FAILED
    with pytest.raises(DeviceNotReadyError):
        hand.set_angles((3,) * 6)
    generation_before = hand.get_health().connection_generation
    hand.recover(timeout=1)

    health = hand.get_health()
    assert health.connected
    assert health.connection_generation == generation_before + 1
    assert health.reconnect_count == 1
    assert health.polling_active
    time.sleep(1.05)
    with state.lock:
        assert (CommandSemantic.ANGLES, (2,) * 6) not in state.command_log
        assert (CommandSemantic.ANGLES, (3,) * 6) not in state.command_log
    hand.close()


def test_identity_change_faults_recovery_without_replaying_commands() -> None:
    hand, state, transport = make_hand()
    hand.open()
    transport.current_dispatcher.disconnect()
    wait_until(lambda: not hand.get_health().connected)
    with state.lock:
        state.fingerprint = "REPLACEMENT"

    with pytest.raises(IdentityMismatchError, match="fingerprint changed"):
        hand.recover(timeout=1)

    assert not hand.get_health().connected
    hand.close()


def test_one_hundred_lifecycle_cycles_leave_no_runtime_threads_or_routes() -> None:
    hand, _state, transport = make_hand()

    for _ in range(100):
        hand.open()
        hand.open()
        hand.close()
        hand.close()

    runtime_threads = [
        thread
        for thread in threading.enumerate()
        if thread.name.startswith("LinkerbotHand.")
        or thread.name == "LinkerbotTransmitArbiter.tx"
    ]
    assert runtime_threads == []
    assert len(transport.dispatchers) == 100
    assert all(dispatcher.stop_count == 1 for dispatcher in transport.dispatchers)


def test_closed_runtime_rejects_active_work_but_cache_remains_readable() -> None:
    hand, _state, _transport = make_hand()
    hand.open()
    hand.close()

    angles = hand.get_snapshot().angles
    assert angles is not None and not angles.meta.valid
    assert not hand.get_health().connected
    with pytest.raises(ClosedError):
        hand.get_angles(timeout=0.1)


def test_closing_one_shared_endpoint_cancels_only_its_transport_work() -> None:
    l6, l6_state, o6, o6_state, transport = make_shared_hands()
    l6.open()
    o6.open()
    try:
        with l6_state.lock:
            l6_state.command_log.clear()
        with o6_state.lock:
            o6_state.command_log.clear()
        transport.command_send_started.clear()
        transport.command_gate.clear()

        o6_handle = o6.set_angles((20,) * 6)
        assert transport.command_send_started.wait(timeout=1)
        l6_handle = l6.set_angles((10,) * 6)
        wait_until(lambda: l6.get_health().transport.pending_tx >= 2)

        started = time.monotonic()
        l6.close()
        assert time.monotonic() - started < 0.5
        assert l6_handle.wait(timeout=1) is CommandStage.FAILED
        with l6_state.lock:
            assert l6_state.command_log == []

        transport.command_gate.set()
        assert o6_handle.wait(timeout=1) is CommandStage.DISPATCHED
        with o6_state.lock:
            assert o6_state.command_log == [(CommandSemantic.ANGLES, (20,) * 6)]
        assert o6.get_health().connected
    finally:
        transport.command_gate.set()
        l6.close()
        o6.close()


def test_duplicate_device_runtime_cannot_claim_an_active_endpoint() -> None:
    first, duplicate, state, _transport = make_duplicate_endpoint_hands()
    first.open()
    try:
        with pytest.raises(DeviceNotReadyError, match="active runtime"):
            duplicate.open()

        handle = first.set_angles((10,) * 6)
        assert handle.wait(timeout=1) is CommandStage.DISPATCHED
        with state.lock:
            assert state.command_log[-1] == (CommandSemantic.ANGLES, (10,) * 6)
    finally:
        duplicate.close()
        first.close()
