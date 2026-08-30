from __future__ import annotations

import statistics
import threading
import time
from dataclasses import FrozenInstanceError

import pytest

from linkerbot_hand.api._initial_state import make_initial_snapshot
from linkerbot_hand.api.device_info import DeviceInfo, VersionField
from linkerbot_hand.api.enums import DataOrigin, HandSide
from linkerbot_hand.api.errors import ProtocolError
from linkerbot_hand.api.readings import (
    ConfiguredSpeedSample,
    ForceSample,
    SampleMeta,
    TactileMatrix,
)
from linkerbot_hand.capabilities.registry import get_declared_capabilities
from linkerbot_hand.capabilities.types import DeviceCapabilities, TelemetrySemantic
from linkerbot_hand.protocols import SamplePayload
from linkerbot_hand.runtime.snapshot_store import SnapshotStore


def make_store(*, model: str = "l6") -> tuple[SnapshotStore, DeviceCapabilities]:
    capabilities = get_declared_capabilities(model, side="left")
    store = SnapshotStore(capabilities, make_initial_snapshot(capabilities))
    store.begin_generation(capabilities, generation=1, captured_at=100.0)
    store.commit_generation(generation=1)
    return store, capabilities


def test_candidate_generation_remains_hidden_until_atomic_commit() -> None:
    capabilities = get_declared_capabilities("l6", side="left")
    store = SnapshotStore(capabilities, make_initial_snapshot(capabilities))
    store.begin_generation(capabilities, generation=1, captured_at=100.0)
    store.publish(
        SamplePayload(semantic=TelemetrySemantic.ANGLES, raw=(1,) * 6),
        generation=1,
        collected_at=101.0,
        collected_monotonic=10.0,
    )

    before = store.view(captured_at=101.0, monotonic_now=10.0)
    assert before.connection_generation == 0
    assert before.angles is not None and not before.angles.meta.valid

    store.commit_generation(generation=1)
    after = store.view(captured_at=101.0, monotonic_now=10.0)
    assert after.connection_generation == 1
    assert after.angles is not None and after.angles.meta.valid


def test_snapshot_waiter_wakes_only_for_visible_cache_revisions() -> None:
    capabilities = get_declared_capabilities("l6", side="left")
    store = SnapshotStore(capabilities, make_initial_snapshot(capabilities))
    initial = store.view(captured_at=1.0, monotonic_now=1.0)
    store.begin_generation(capabilities, generation=1, captured_at=2.0)
    store.publish(
        SamplePayload(semantic=TelemetrySemantic.ANGLES, raw=(1,) * 6),
        generation=1,
        collected_at=2.0,
        collected_monotonic=2.0,
    )

    assert not store.wait_for_update(
        connection_generation=initial.connection_generation,
        snapshot_sequence=initial.snapshot_sequence,
        timeout_s=0.01,
    )

    waiting = threading.Event()
    result: list[bool] = []

    def wait_for_commit() -> None:
        waiting.set()
        result.append(
            store.wait_for_update(
                connection_generation=initial.connection_generation,
                snapshot_sequence=initial.snapshot_sequence,
                timeout_s=0.5,
            )
        )

    thread = threading.Thread(target=wait_for_commit)
    thread.start()
    assert waiting.wait(0.1)
    store.commit_generation(generation=1)
    thread.join(0.5)

    assert not thread.is_alive()
    assert result == [True]


def test_snapshot_waiter_wakes_for_current_generation_publication() -> None:
    store, _capabilities = make_store()
    previous = store.view(captured_at=1.0, monotonic_now=1.0)
    waiting = threading.Event()
    result: list[bool] = []

    def wait_for_sample() -> None:
        waiting.set()
        result.append(
            store.wait_for_update(
                connection_generation=previous.connection_generation,
                snapshot_sequence=previous.snapshot_sequence,
                timeout_s=0.5,
            )
        )

    thread = threading.Thread(target=wait_for_sample)
    thread.start()
    assert waiting.wait(0.1)
    store.publish(
        SamplePayload(semantic=TelemetrySemantic.ANGLES, raw=(1,) * 6),
        generation=1,
        collected_at=2.0,
        collected_monotonic=2.0,
    )
    thread.join(0.5)

    assert not thread.is_alive()
    assert result == [True]


def test_snapshot_waiter_can_ignore_command_feedback_only_revisions() -> None:
    store, _capabilities = make_store()
    previous = store.view(captured_at=1.0, monotonic_now=1.0)
    waiting = threading.Event()
    result: list[bool] = []

    def wait_for_query() -> None:
        waiting.set()
        result.append(
            store.wait_for_update(
                connection_generation=previous.connection_generation,
                snapshot_sequence=previous.snapshot_sequence,
                timeout_s=0.5,
                wake_on_command_feedback=False,
            )
        )

    thread = threading.Thread(target=wait_for_query)
    thread.start()
    assert waiting.wait(0.1)
    store.publish(
        SamplePayload(
            semantic=TelemetrySemantic.ANGLES,
            raw=(1,) * 6,
            origin=DataOrigin.DEVICE_COMMAND_FEEDBACK,
        ),
        generation=1,
        collected_at=2.0,
        collected_monotonic=2.0,
    )
    time.sleep(0.02)
    assert thread.is_alive()

    store.publish(
        SamplePayload(semantic=TelemetrySemantic.ANGLES, raw=(2,) * 6),
        generation=1,
        collected_at=3.0,
        collected_monotonic=3.0,
    )
    thread.join(0.5)

    assert not thread.is_alive()
    assert result == [True]


def test_snapshot_change_wait_times_out_without_a_cache_revision() -> None:
    store, _capabilities = make_store()
    previous = store.view(captured_at=1.0, monotonic_now=1.0)

    change = store.wait_for_change(
        connection_generation=previous.connection_generation,
        snapshot_sequence=previous.snapshot_sequence,
        timeout_s=0.01,
    )

    assert change is None


def test_filtered_snapshot_change_preserves_query_revision_after_command_echo() -> None:
    store, _capabilities = make_store()
    previous = store.view(captured_at=1.0, monotonic_now=1.0)
    store.publish(
        SamplePayload(
            semantic=TelemetrySemantic.ANGLES,
            raw=(2,) * 6,
            origin=DataOrigin.DEVICE_RESPONSE,
        ),
        generation=1,
        collected_at=2.0,
        collected_monotonic=2.0,
    )
    store.publish(
        SamplePayload(
            semantic=TelemetrySemantic.ANGLES,
            raw=(3,) * 6,
            origin=DataOrigin.DEVICE_COMMAND_FEEDBACK,
        ),
        generation=1,
        collected_at=3.0,
        collected_monotonic=3.0,
    )

    change = store.wait_for_change(
        connection_generation=previous.connection_generation,
        snapshot_sequence=previous.snapshot_sequence,
        timeout_s=0.01,
        wake_on_command_feedback=False,
    )

    assert change is not None
    assert change.angles is not None
    assert change.angles.raw == (2,) * 6
    assert change.angles.meta.origin is DataOrigin.DEVICE_RESPONSE
    assert tuple(joint.angle_raw for joint in change.joints) == (2,) * 6
    latest = store.view(captured_at=3.0, monotonic_now=3.0)
    assert latest.angles is not None
    assert latest.angles.raw == (3,) * 6


def test_snapshot_change_keeps_joint_projection_on_the_same_revision() -> None:
    store, _capabilities = make_store()
    previous = store.view(captured_at=1.0, monotonic_now=1.0)
    store.publish(
        SamplePayload(semantic=TelemetrySemantic.ANGLES, raw=(7,) * 6),
        generation=1,
        collected_at=2.0,
        collected_monotonic=2.0,
    )

    change = store.wait_for_change(
        connection_generation=previous.connection_generation,
        snapshot_sequence=previous.snapshot_sequence,
        timeout_s=0.01,
    )

    assert change is not None
    assert change.angles is not None
    assert tuple(joint.angle_raw for joint in change.joints) == change.angles.raw


def test_aged_change_preserves_filtered_revision_and_ages_every_channel() -> None:
    store, _capabilities = make_store(model="l6")
    previous = store.view(captured_at=1.0, monotonic_now=1.0)
    store.publish(
        SamplePayload(semantic=TelemetrySemantic.CURRENTS, raw=(7,) * 6),
        generation=1,
        collected_at=1.0,
        collected_monotonic=1.0,
    )
    store.publish(
        SamplePayload(
            semantic=TelemetrySemantic.ANGLES,
            raw=(2,) * 6,
            origin=DataOrigin.DEVICE_RESPONSE,
        ),
        generation=1,
        collected_at=2.0,
        collected_monotonic=2.0,
    )
    store.publish(
        SamplePayload(
            semantic=TelemetrySemantic.ANGLES,
            raw=(3,) * 6,
            origin=DataOrigin.DEVICE_COMMAND_FEEDBACK,
        ),
        generation=1,
        collected_at=2.01,
        collected_monotonic=2.01,
    )

    change = store.wait_for_aged_change(
        connection_generation=previous.connection_generation,
        snapshot_sequence=previous.snapshot_sequence,
        timeout_s=0.01,
        captured_at=lambda: 2.02,
        monotonic_now=lambda: 2.02,
        wake_on_command_feedback=False,
    )

    assert change is not None
    assert change.angles is not None
    assert change.angles.raw == (2,) * 6
    assert change.angles.meta.origin is DataOrigin.DEVICE_RESPONSE
    assert change.angles.meta.age_s == pytest.approx(0.02)
    assert not change.angles.meta.stale
    assert change.currents is not None
    assert change.currents.meta.age_s == pytest.approx(1.02)
    assert change.currents.meta.stale
    assert tuple(joint.angle_raw for joint in change.joints) == (2,) * 6


def test_snapshot_change_reports_generation_device_info_and_invalidation() -> None:
    store, capabilities = make_store()
    previous = store.view(captured_at=1.0, monotonic_now=1.0)
    store.invalidate(generation=1, captured_at=2.0)

    invalidated = store.wait_for_change(
        connection_generation=previous.connection_generation,
        snapshot_sequence=previous.snapshot_sequence,
        timeout_s=0.01,
    )

    assert invalidated is not None
    assert invalidated.connection_generation == 1
    store.begin_generation(capabilities, generation=2, captured_at=3.0)
    device_info = DeviceInfo(
        model=capabilities.model,
        serial_number="CHANGE-TEST",
        pcb_version=None,
        firmware_version=VersionField(raw=(2, 4, 5), text="2.4.5"),
        mechanical_version=None,
        product_code=None,
        sensor_variant=None,
        side=capabilities.side,
        raw_identity=b"CHANGE-TEST",
        meta=SampleMeta.unavailable(),
    )
    store.publish_device_info(
        device_info,
        generation=2,
        collected_at=3.0,
        collected_monotonic=3.0,
    )
    store.commit_generation(generation=2)
    committed = store.wait_for_change(
        connection_generation=invalidated.connection_generation,
        snapshot_sequence=invalidated.snapshot_sequence,
        timeout_s=0.01,
    )
    assert committed is not None
    assert committed.connection_generation == 2
    before_info = committed
    store.publish_device_info(
        device_info,
        generation=2,
        collected_at=4.0,
        collected_monotonic=4.0,
    )
    info_change = store.wait_for_change(
        connection_generation=before_info.connection_generation,
        snapshot_sequence=before_info.snapshot_sequence,
        timeout_s=0.01,
    )
    assert info_change is not None
    assert info_change.device_info is not None
    assert info_change.device_info.meta.sequence == 2


def test_angle_publication_keeps_raw_and_derives_declared_representations() -> None:
    store, capabilities = make_store()
    mappings = tuple(joint.percent_mapping for joint in capabilities.joints)
    assert all(mapping is not None for mapping in mappings)
    raw = tuple(mapping.raw_at_zero for mapping in mappings if mapping is not None)

    sample = store.publish(
        SamplePayload(semantic=TelemetrySemantic.ANGLES, raw=raw),
        generation=1,
        collected_at=101.0,
        collected_monotonic=10.0,
    )
    snapshot = store.view(captured_at=101.1, monotonic_now=10.1)

    assert sample is snapshot.angles or snapshot.angles is not None
    assert snapshot.angles is not None
    assert snapshot.angles.raw == raw
    assert snapshot.angles.percent == (0.0,) * 6
    assert snapshot.angles.meta.sequence == 1
    assert snapshot.angles.meta.age_s == pytest.approx(0.1)
    assert snapshot.angles.meta.connection_generation == 1
    assert tuple(joint.angle_raw for joint in snapshot.joints) == raw
    with pytest.raises(FrozenInstanceError):
        snapshot.snapshot_sequence = 99  # ty: ignore[invalid-assignment]


def test_out_of_range_feedback_is_preserved_and_marked() -> None:
    store, capabilities = make_store()
    raw = tuple(joint.raw_angle_range.maximum + 1 for joint in capabilities.joints)

    store.publish(
        SamplePayload(semantic=TelemetrySemantic.ANGLES, raw=raw),
        generation=1,
        collected_at=1.0,
        collected_monotonic=1.0,
    )
    angles = store.view(captured_at=1.0, monotonic_now=1.0).angles

    assert angles is not None
    assert angles.raw == raw
    assert not angles.meta.within_declared_range
    assert all(value is not None for value in angles.percent)


def test_each_channel_has_independent_sequence_age_and_staleness() -> None:
    store, _capabilities = make_store()
    store.publish(
        SamplePayload(semantic=TelemetrySemantic.ANGLES, raw=(0,) * 6),
        generation=1,
        collected_at=1.0,
        collected_monotonic=1.0,
    )
    store.publish(
        SamplePayload(semantic=TelemetrySemantic.CURRENTS, raw=(7,) * 6),
        generation=1,
        collected_at=1.2,
        collected_monotonic=1.2,
    )

    snapshot = store.view(captured_at=2.0, monotonic_now=2.0)

    assert snapshot.angles is not None and snapshot.angles.meta.stale
    assert snapshot.currents is not None and snapshot.currents.meta.stale
    assert snapshot.angles.meta.age_s == 1.0
    assert snapshot.currents.meta.age_s == pytest.approx(0.8)
    assert snapshot.angles.meta.sequence == snapshot.currents.meta.sequence == 1


def test_command_cache_origin_is_not_presented_as_device_measurement() -> None:
    store, _capabilities = make_store()
    configured = store.publish(
        SamplePayload(
            semantic=TelemetrySemantic.CONFIGURED_SPEEDS,
            raw=(80,) * 6,
            origin=DataOrigin.COMMAND_CACHE,
        ),
        generation=1,
        collected_at=1.0,
        collected_monotonic=1.0,
    )

    assert isinstance(configured, ConfiguredSpeedSample)
    assert configured.meta.origin is DataOrigin.COMMAND_CACHE
    snapshot = store.view(captured_at=1.0, monotonic_now=1.0)
    assert snapshot.configured_speeds is not None
    assert snapshot.measured_speeds is None


def test_complete_tactile_transaction_is_published_atomically() -> None:
    store, _capabilities = make_store()
    tactile = tuple(
        TactileMatrix(
            name=name,
            rows=12,
            columns=6,
            raw=tuple(tuple(index for _ in range(6)) for _ in range(12)),
            online=True,
        )
        for index, name in enumerate(("thumb", "index", "middle", "ring", "pinky"))
    )

    force = store.publish(
        SamplePayload(semantic=TelemetrySemantic.FORCE, tactile=tactile),
        generation=1,
        collected_at=1.0,
        collected_monotonic=1.0,
    )

    assert isinstance(force, ForceSample)
    assert force.tactile == tactile
    assert force.meta.sequence == 1


def test_identity_is_sequenced_and_generation_loss_invalidates_every_sample() -> None:
    store, _capabilities = make_store()
    info = DeviceInfo(
        model="L6",
        serial_number="FAKE-1",
        pcb_version=None,
        firmware_version=VersionField(
            raw=(2, 4, 0), text="2.4.0", major=2, minor=4, patch=0
        ),
        mechanical_version=None,
        product_code=None,
        sensor_variant="new",
        side=HandSide.LEFT,
        raw_identity=b"L6-FAKE-1",
        meta=SampleMeta.unavailable(),
    )
    published = store.publish_device_info(
        info,
        generation=1,
        collected_at=1.0,
        collected_monotonic=1.0,
    )
    store.publish(
        SamplePayload(semantic=TelemetrySemantic.ANGLES, raw=(1,) * 6),
        generation=1,
        collected_at=1.0,
        collected_monotonic=1.0,
    )

    assert published.meta.valid and published.meta.sequence == 1
    store.invalidate(generation=1, captured_at=2.0)
    snapshot = store.view(captured_at=2.0, monotonic_now=2.0)

    assert snapshot.device_info is not None and not snapshot.device_info.meta.valid
    assert snapshot.angles is not None and not snapshot.angles.meta.valid
    assert all(joint.angle_raw is None for joint in snapshot.joints)
    with pytest.raises(ProtocolError, match="stale"):
        store.publish(
            SamplePayload(semantic=TelemetrySemantic.ANGLES, raw=(2,) * 6),
            generation=0,
            collected_at=2.0,
            collected_monotonic=2.0,
        )


def test_snapshot_reads_are_zero_io_scale_and_p95_below_one_millisecond() -> None:
    store, _capabilities = make_store()
    store.publish(
        SamplePayload(semantic=TelemetrySemantic.ANGLES, raw=(1,) * 6),
        generation=1,
        collected_at=1.0,
        collected_monotonic=1.0,
    )

    durations = []
    for index in range(20_000):
        started = time.perf_counter_ns()
        snapshot = store.view(
            captured_at=2.0 + index * 1e-6,
            monotonic_now=2.0 + index * 1e-6,
        )
        durations.append((time.perf_counter_ns() - started) / 1e9)

    assert snapshot.angles is not None
    assert statistics.quantiles(durations, n=100)[94] < 0.001
