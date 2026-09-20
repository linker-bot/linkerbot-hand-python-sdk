"""Aggregation preserves ages, support transitions and one total deadline."""

from contextlib import closing
from dataclasses import replace
from types import SimpleNamespace

import pytest

import linkerbot_hand.api.device as device_module
from linkerbot_hand import (
    HandDevice,
    HandSide,
    PollingPlan,
    TelemetrySemantic,
    TimeoutError,
)
from linkerbot_hand.api._force import merge_force_samples
from linkerbot_hand.api._initial_state import make_initial_snapshot
from linkerbot_hand.api.readings import TactileMatrix
from linkerbot_hand.protocols.base import SamplePayload
from linkerbot_hand.runtime.snapshot_store import SnapshotStore

from .classic_can_fake import ScriptedClassicCANTransport, make_script, wait_until
from .o30_canfd_fake import O30DeviceScript, ScriptedO30Transport
from .test_force_polling import independent_fields


def matrix_payload(caps):
    return SamplePayload(
        TelemetrySemantic.FORCE,
        tactile=tuple(
            TactileMatrix(name=name, rows=12, columns=6, raw=((7,) * 6,) * 12)
            for name in caps.force_field("fn").sensor_names
        ),
    )


@pytest.mark.parametrize("first_duration", [0.01, 1.01])
def test_getter_shares_deadline_and_accounts_for_waiting_on_later_fields(
    monkeypatch, first_duration
):
    caps = independent_fields()
    caps = replace(
        caps,
        telemetry=tuple(
            replace(channel, stale_after_s=0.1) for channel in caps.telemetry
        ),
    )
    store = SnapshotStore(caps, make_initial_snapshot(caps))
    hand = HandDevice(model="L6", side="left", transport=object())
    clock = [10.0]
    reads = []
    monkeypatch.setattr(device_module, "monotonic", lambda: clock[0])
    monkeypatch.setattr(hand, "get_capabilities", lambda: caps)
    monkeypatch.setattr(
        hand, "get_snapshot", lambda: SimpleNamespace(connection_generation=0)
    )

    def read(semantic, timeout, expected_type, **kwargs):
        reads.append((semantic, timeout))
        clock[0] += first_duration if semantic is TelemetrySemantic.FORCE else 0.5
        payload = (
            matrix_payload(caps)
            if semantic is TelemetrySemantic.FORCE
            else SamplePayload(semantic, raw=(1, 2, 3, 4, 5))
        )
        return store.publish(
            payload,
            generation=0,
            collected_at=clock[0] + 100,
            collected_monotonic=clock[0],
        )

    monkeypatch.setattr(hand, "_read", read)
    if first_duration > 1:
        with pytest.raises(TimeoutError, match="total timeout"):
            hand.get_force(timeout=1)
        assert reads == [(TelemetrySemantic.FORCE, 1)]
        cached = store.view(captured_at=112, monotonic_now=12).force
        assert cached.fn.meta.valid and cached.fns is None
    else:
        sample = hand.get_force(timeout=1)
        assert reads[1] == (TelemetrySemantic.FORCE_FNS, pytest.approx(0.99))
        assert sample.fn.meta.age_s == pytest.approx(0.5)
        assert sample.fn.meta.stale
        assert sample.fns.meta.age_s == 0 and not sample.fns.meta.stale
        assert sample.fn.meta.collected_at == pytest.approx(110.01)
        assert sample.fns.meta.collected_at == pytest.approx(110.51)


def test_support_change_preserves_acquisition_and_old_snapshot():
    caps = independent_fields()
    store = SnapshotStore(caps, make_initial_snapshot(caps))
    store.publish(
        matrix_payload(caps), generation=0, collected_at=100, collected_monotonic=10
    )
    old = store.view(captured_at=100, monotonic_now=10)
    disabled = replace(
        caps, firmware_unsupported_telemetry=(TelemetrySemantic.FORCE_FNS,)
    )
    store.observe_force_capabilities(disabled, generation=0)
    current = store.view(captured_at=101, monotonic_now=11)
    assert current.force.status("fns") == "unsupported"
    assert old.force.status("fns") == "not_read"
    assert current.force.fn.raw is old.force.fn.raw
    assert current.force.fn.meta.sequence == old.force.fn.meta.sequence
    assert current.force.fn.meta.collected_at == old.force.fn.meta.collected_at
    assert current.force.fn.meta.age_s == 1
    assert current.snapshot_sequence == old.snapshot_sequence + 1


def test_optional_force_downgrade_updates_snapshot_and_recovery_resets_it():
    script = make_script("L6", HandSide.LEFT)
    script.unsupported_queries.add(0xC6)
    hand = HandDevice(
        model="L6", side="left", transport=ScriptedClassicCANTransport(script)
    )
    with closing(hand.open()):
        hand.start_polling(PollingPlan(force_fn_hz=20, angles_hz=20))
        wait_until(
            lambda: hand.get_snapshot().force.status("fn") == "unsupported", timeout_s=3
        )
        assert hand.get_health().connected
        assert hand.get_capabilities().force_field("fn").firmware_unsupported
        hand.stop_polling()
        hand.recover(timeout=1)
        assert hand.get_snapshot().force.status("fn") == "not_read"


def test_o30_shared_fields_follow_observed_descriptors():
    script = O30DeviceScript(sensor_variant="TSSP_JZG")
    hand = HandDevice(model="O30", side="right", transport=ScriptedO30Transport(script))
    with closing(hand.open()):
        hand.get_force()
        assert hand.get_capabilities().force_field("fn").shared_fields == ("fn", "fns")


def test_scalar_only_aggregate_raw_refers_to_latest_query():
    caps = independent_fields()
    store = SnapshotStore(caps, make_initial_snapshot(caps))
    samples = tuple(
        store.publish(
            SamplePayload(TelemetrySemantic.FORCE_FNS, raw=(value,) * 5),
            generation=0,
            collected_at=value,
            collected_monotonic=value,
        )
        for value in (1, 2)
    )
    assert merge_force_samples(*samples).raw == (2,) * 5
