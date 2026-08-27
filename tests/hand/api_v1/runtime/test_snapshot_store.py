from __future__ import annotations

import statistics
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
