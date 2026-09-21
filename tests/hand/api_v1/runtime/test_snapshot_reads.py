"""Read optimizations must preserve exact statistics and snapshot revisions."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError

import pytest

from linkerbot_hand.api._initial_state import make_initial_snapshot
from linkerbot_hand.api.enums import DataOrigin
from linkerbot_hand.api.errors import ValidationError
from linkerbot_hand.api.readings import TactileMatrix
from linkerbot_hand.capabilities.registry import get_declared_capabilities
from linkerbot_hand.capabilities.types import DeviceCapabilities, TelemetrySemantic
from linkerbot_hand.protocols import SamplePayload
from linkerbot_hand.runtime._snapshot_samples import SAMPLE_ATTRIBUTES, build_joints
from linkerbot_hand.runtime.snapshot_store import SnapshotStore

from .factories import make_hand


def make_store(*, model: str = "l6") -> tuple[SnapshotStore, DeviceCapabilities]:
    capabilities = get_declared_capabilities(
        model, side=None if model == "l30" else "left"
    )
    store = SnapshotStore(capabilities, make_initial_snapshot(capabilities))
    store.begin_generation(capabilities, generation=1, captured_at=100.0)
    store.commit_generation(generation=1)
    return store, capabilities


def test_joint_projection_reuse_keeps_age_fresh_and_invalidates_on_generation() -> None:
    store, capabilities = make_store()
    store.publish(
        SamplePayload(semantic=TelemetrySemantic.ANGLES, raw=(50,) * 6),
        generation=1,
        collected_at=100.0,
        collected_monotonic=10.0,
    )
    first = store.view(captured_at=100.0, monotonic_now=10.0)
    stale = store.view(captured_at=110.0, monotonic_now=20.0)
    assert first.angles is not None and stale.angles is not None
    assert first.joints is stale.joints
    assert first.angles.meta.age_s == 0.0 and not first.angles.meta.stale
    assert stale.angles.meta.age_s == 10.0 and stale.angles.meta.stale
    assert stale.captured_at == 110.0
    assert stale.snapshot_sequence == first.snapshot_sequence
    with pytest.raises(FrozenInstanceError):
        stale.joints[0].angle_raw = 12  # ty: ignore[invalid-assignment]

    store.invalidate(generation=1, captured_at=111.0)
    invalid = store.view(captured_at=111.0, monotonic_now=21.0)
    assert all(joint.angle_raw is None for joint in invalid.joints)
    assert first.joints[0].angle_raw == 50
    store.begin_generation(capabilities, generation=2, captured_at=112.0)
    store.publish(
        SamplePayload(semantic=TelemetrySemantic.ANGLES, raw=(60,) * 6),
        generation=2,
        collected_at=112.0,
        collected_monotonic=22.0,
    )
    store.commit_generation(generation=2)
    current = store.view(captured_at=112.0, monotonic_now=22.0)
    assert current.connection_generation == 2
    assert all(joint.angle_raw == 60 for joint in current.joints)


@pytest.mark.parametrize("model", ["l6", "o6", "l20", "l20lite", "l30", "o20", "o30"])
def test_channel_samples_match_full_view_and_projection_after_each_publication(
    model: str,
) -> None:
    store, capabilities = make_store(model=model)
    initial = store.view(captured_at=100.0, monotonic_now=10.0)
    projected = {
        TelemetrySemantic.ANGLES,
        TelemetrySemantic.MEASURED_SPEEDS,
        TelemetrySemantic.CONFIGURED_SPEEDS,
        TelemetrySemantic.CURRENTS,
        TelemetrySemantic.CURRENT_LIMITS,
        TelemetrySemantic.TORQUES_LIMITS,
        TelemetrySemantic.TORQUES,
        TelemetrySemantic.TEMPERATURES,
        TelemetrySemantic.FAULTS,
    }
    for semantic in projected:
        telemetry = capabilities.telemetry_channel(semantic)
        if telemetry is None:
            continue
        for value in (10, 20):
            store.publish(
                SamplePayload(semantic=semantic, raw=(value,) * telemetry.shape[0]),
                generation=1,
                collected_at=100.0,
                collected_monotonic=10.0,
            )
            view = store.view(captured_at=100.01, monotonic_now=10.01)
            assert view.joints == build_joints(view, capabilities)
            for channel, attribute in SAMPLE_ATTRIBUTES.items():
                assert store.sample(
                    channel, captured_at=100.01, monotonic_now=10.01
                ) == getattr(view, attribute)
    assert all(joint.angle_raw is None for joint in initial.joints)
    store.invalidate(generation=1, captured_at=101.0)
    invalid = store.view(captured_at=101.0, monotonic_now=11.0)
    for channel, attribute in SAMPLE_ATTRIBUTES.items():
        assert store.sample(channel, captured_at=101.0, monotonic_now=11.0) == getattr(
            invalid, attribute
        )


def test_filtered_waiter_keeps_its_own_projection_after_new_command_feedback() -> None:
    store, _capabilities = make_store()
    for raw, origin in (
        (10, DataOrigin.DEVICE_RESPONSE),
        (20, DataOrigin.DEVICE_COMMAND_FEEDBACK),
    ):
        store.publish(
            SamplePayload(
                semantic=TelemetrySemantic.ANGLES, raw=(raw,) * 6, origin=origin
            ),
            generation=1,
            collected_at=100.0,
            collected_monotonic=10.0,
        )
    current = store.view(captured_at=100.1, monotonic_now=10.1)
    filtered = store.wait_for_aged_change(
        connection_generation=1,
        snapshot_sequence=0,
        timeout_s=0.1,
        captured_at=lambda: 100.1,
        monotonic_now=lambda: 10.1,
        wake_on_command_feedback=False,
    )
    assert filtered is not None and filtered.angles is not None
    assert all(j.angle_raw == 10 for j in filtered.joints)
    assert all(j.angle_raw == 20 for j in current.joints)
    assert filtered.angles.meta.age_s == pytest.approx(0.1)
    assert store.view(captured_at=100.1, monotonic_now=10.1).joints == current.joints


def test_force_only_update_reuses_joint_projection_and_preserves_old_snapshot() -> None:
    store, capabilities = make_store()
    first = store.view(captured_at=100.0, monotonic_now=10.0)
    force = capabilities.telemetry_channel(TelemetrySemantic.FORCE)
    assert force is not None
    _, rows, columns = force.shape
    matrices = tuple(
        TactileMatrix(
            name=name, rows=rows, columns=columns, raw=((1,) * columns,) * rows
        )
        for name in force.channels
    )
    store.publish(
        SamplePayload(semantic=TelemetrySemantic.FORCE, tactile=matrices),
        generation=1,
        collected_at=100.0,
        collected_monotonic=10.0,
    )
    second = store.view(captured_at=100.1, monotonic_now=10.1)
    assert second.joints is first.joints
    assert second.snapshot_sequence > first.snapshot_sequence
    assert first.force is not None and not first.force.meta.valid
    assert second.force is not None and second.force.meta.valid


def test_concurrent_publish_and_read_never_mix_channel_and_joint_revisions() -> None:
    store, capabilities = make_store(model="o30")
    n = len(capabilities.joints)
    barrier = threading.Barrier(3)

    def writer() -> None:
        barrier.wait(timeout=3)
        for index in range(1000):
            store.publish(
                SamplePayload(semantic=TelemetrySemantic.ANGLES, raw=(index,) * n),
                generation=1,
                collected_at=float(index),
                collected_monotonic=float(index),
            )

    def reader() -> None:
        barrier.wait(timeout=3)
        for _ in range(500):
            snap = store.view(captured_at=2000.0, monotonic_now=2000.0)
            assert snap.joints == build_joints(snap, capabilities)

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(writer), pool.submit(reader), pool.submit(reader)]
        for future in futures:
            future.result(timeout=10)


def test_repeated_public_reads_are_zero_io_and_angles_still_request_fresh_data() -> (
    None
):
    hand, state, transport = make_hand()
    hand.open()
    try:
        first = hand.get_snapshot()
        assert first.angles is not None
        queries = list(state.query_log)
        before = transport.get_health()
        for _ in range(100):
            hand.get_health()
            current = hand.get_snapshot()
            assert current.angles is not None
            assert current.angles.meta.sequence == first.angles.meta.sequence
            assert current.joints is first.joints
        after = transport.get_health()
        assert after.tx_frames == before.tx_frames
        assert after.rx_frames == before.rx_frames
        assert state.query_log == queries

        angles = hand.get_angles(timeout=1)
        assert angles.meta.sequence > first.angles.meta.sequence
        assert state.query_log == [*queries, TelemetrySemantic.ANGLES]
    finally:
        hand.close()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
@pytest.mark.parametrize("argument", ["captured_at", "monotonic_now"])
def test_direct_sample_read_still_validates_time(argument: str, value: float) -> None:
    store, _capabilities = make_store()
    times = {"captured_at": 100.0, "monotonic_now": 10.0, argument: value}
    with pytest.raises(ValidationError):
        store.sample(TelemetrySemantic.ANGLES, **times)
