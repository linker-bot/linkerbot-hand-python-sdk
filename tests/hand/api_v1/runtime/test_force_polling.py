"""Field rates share physical schedules without hiding their requested rates."""

from contextlib import closing
from dataclasses import replace
from time import monotonic

import pytest

from linkerbot_hand import (
    HandDevice,
    PollingPlan,
    TelemetrySemantic,
    ValidationError,
    get_declared_capabilities,
)
from linkerbot_hand.api._initial_state import make_initial_snapshot
from linkerbot_hand.api.enums import SampleProjection
from linkerbot_hand.capabilities.polling import resolve_poll_rates
from linkerbot_hand.protocols.base import SamplePayload
from linkerbot_hand.runtime.poll_scheduler import PollScheduler
from linkerbot_hand.runtime.snapshot_store import SnapshotStore

from .o30_canfd_fake import O30DeviceScript, ScriptedO30Transport


def independent_fields():
    caps = get_declared_capabilities("L6", side="left")
    force = caps.telemetry_channel(TelemetrySemantic.FORCE)
    scalar = replace(
        force,
        semantic=TelemetrySemantic.FORCE_FNS,
        shape=(5,),
        payload_projection=SampleProjection.RAW_VECTOR,
        force_fields=("fns",),
    )
    return replace(caps, telemetry=(*caps.telemetry, scalar))


@pytest.mark.parametrize(
    "plan",
    [
        PollingPlan(force_fn_hz=20),
        PollingPlan(force_fns_hz=20),
        PollingPlan(force_fn_hz=10, force_fns_hz=20),
        PollingPlan(force_hz=20),
    ],
)
def test_o30_has_one_physical_deadline_at_maximum_rate(plan):
    caps = get_declared_capabilities("O30", side="left")
    assert resolve_poll_rates(plan, caps) == {TelemetrySemantic.FORCE: 20}
    scheduler = PollScheduler(caps)
    scheduler.replace(plan, now=0)
    assert scheduler.take_due(now=0) is TelemetrySemantic.FORCE
    assert scheduler.take_due(now=0) is None
    scheduler.mark_completed(TelemetrySemantic.FORCE, now=0.01, requested_by_poll=True)
    assert scheduler.take_due(now=0.049) is None
    assert scheduler.take_due(now=0.05) is TelemetrySemantic.FORCE
    assert scheduler.take_due(now=0.05) is None
    assert scheduler.next_deadline() == pytest.approx(0.1)
    fields = {field.field: field for field in scheduler.force_metrics(now=0.05)}
    assert fields["fn"].query_hz == fields["fns"].query_hz == 20


def test_independent_fields_have_independent_deadlines_and_default_rates():
    caps = independent_fields()
    assert resolve_poll_rates(PollingPlan(force_hz=10), caps) == {
        TelemetrySemantic.FORCE: 10,
        TelemetrySemantic.FORCE_FNS: 10,
    }
    scheduler = PollScheduler(caps)
    scheduler.replace(PollingPlan(force_fn_hz=20, force_fns_hz=10), now=0)
    assert scheduler.take_due(now=0) is TelemetrySemantic.FORCE
    assert scheduler.take_due(now=0) is TelemetrySemantic.FORCE_FNS
    assert scheduler.take_due(now=0.05) is TelemetrySemantic.FORCE
    assert scheduler.take_due(now=0.05) is None
    assert scheduler.take_due(now=0.1) is TelemetrySemantic.FORCE
    assert scheduler.take_due(now=0.1) is TelemetrySemantic.FORCE_FNS


@pytest.mark.parametrize("field", ["force_fn_hz", "force_fns_hz"])
def test_shorthand_rejects_conflicting_positive_field_rate(field):
    with pytest.raises(ValidationError, match="force_hz"):
        PollingPlan(force_hz=10, **{field: 20})
    PollingPlan(force_hz=10, **{field: 0})
    PollingPlan(force_hz=10, force_px_hz=5)


def test_scalar_publication_does_not_refresh_matrix_or_overwrite_raw_data():
    from linkerbot_hand.api.readings import TactileMatrix

    caps = independent_fields()
    store = SnapshotStore(caps, make_initial_snapshot(caps))
    matrices = tuple(
        TactileMatrix(name=name, rows=12, columns=6, raw=((7,) * 6,) * 12)
        for name in caps.force_field("fn").sensor_names
    )
    store.publish(
        SamplePayload(TelemetrySemantic.FORCE, tactile=matrices),
        generation=0,
        collected_at=100,
        collected_monotonic=10,
    )
    first = store.view(captured_at=100, monotonic_now=10)
    store.publish(
        SamplePayload(TelemetrySemantic.FORCE_FNS, raw=(0, 1, 2, 3, 4)),
        generation=0,
        collected_at=101,
        collected_monotonic=11,
    )
    second = store.view(captured_at=101.1, monotonic_now=11.1)
    assert second.force.fn.meta.collected_at == 100
    assert second.force.fn.meta.age_s == pytest.approx(1.1)
    assert second.force.fn.meta.stale
    assert second.force.fns.meta.collected_at == 101
    assert second.force.fns.meta.age_s == pytest.approx(0.1)
    assert not second.force.fns.meta.stale
    assert second.force.fns.raw[0] == (0,)
    assert second.force.fns.valid_values[0] == (True,)
    assert second.force.tactile is matrices
    assert first.force.fns is None
    assert first.force.fn.meta.age_s == 0
    ages = store.data_ages(captured_at=102, monotonic_now=12)
    assert ages[TelemetrySemantic.FORCE].age_s == 2
    assert ages[TelemetrySemantic.FORCE_FNS].age_s == 1


def test_o30_unknown_absent_field_disables_poll_without_disconnect(caplog):
    script = O30DeviceScript(sensor_variant="TSSP_JZG")
    hand = HandDevice(model="O30", side="right", transport=ScriptedO30Transport(script))
    with closing(hand.open()):
        hand.start_polling(PollingPlan(force_ft_hz=20))
        deadline = monotonic() + 1
        while hand.get_capabilities().force_field("ft").supported is None:
            assert monotonic() < deadline
            previous = hand.get_snapshot()
            hand.wait_for_snapshot(previous, timeout=0.01)
        health = hand.get_health()
        assert health.connected and not health.polling_active
        assert "does not expose ft" in caplog.text
        fields = {field.field: field for field in health.force_fields}
        assert fields["ft"].requested_hz == 20
        assert fields["ft"].query_hz == fields["ft"].achieved_hz == 0
        hand.start_polling(PollingPlan(force_fn_hz=20, force_fns_hz=10))
        fields = {field.field: field for field in hand.get_health().force_fields}
        assert fields["fn"].requested_hz == 20
        assert fields["fns"].requested_hz == 10
        assert fields["fn"].query_hz == fields["fns"].query_hz == 20
        hand.stop_polling()
        assert not hand.get_health().polling_active
