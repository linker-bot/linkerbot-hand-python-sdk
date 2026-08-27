from __future__ import annotations

import pytest

from linkerbot_hand.api.config import PollingPlan
from linkerbot_hand.api.errors import PollingPlanError
from linkerbot_hand.api.health import DataAge
from linkerbot_hand.capabilities.registry import get_declared_capabilities
from linkerbot_hand.capabilities.types import TelemetrySemantic
from linkerbot_hand.runtime.poll_scheduler import PollScheduler


def data_ages_for_l6() -> dict[TelemetrySemantic, DataAge]:
    capabilities = get_declared_capabilities("l6", side="left")
    return {
        telemetry.semantic: DataAge(
            channel=telemetry.semantic.value,
            age_s=None,
            valid=False,
            stale=True,
            sequence=0,
            connection_generation=0,
        )
        for telemetry in capabilities.telemetry
    }


def test_disabled_channels_never_create_poll_work() -> None:
    scheduler = PollScheduler(get_declared_capabilities("l6", side="left"))

    scheduler.replace(PollingPlan(), now=0.0)

    assert not scheduler.active
    assert scheduler.take_due(now=1_000.0) is None
    assert scheduler.next_deadline() is None


def test_due_ticks_keep_only_the_next_deadline_without_backlog() -> None:
    scheduler = PollScheduler(get_declared_capabilities("l6", side="left"))
    scheduler.replace(PollingPlan(angles_hz=100), now=0.0)

    assert scheduler.take_due(now=0.0) is TelemetrySemantic.ANGLES
    assert scheduler.take_due(now=0.0) is None

    assert scheduler.take_due(now=1.0) is TelemetrySemantic.ANGLES
    assert scheduler.take_due(now=1.0) is None
    deadline = scheduler.next_deadline()
    assert deadline is not None and deadline > 1.0

    metrics = {
        item.channel: item
        for item in scheduler.metrics(
            now=1.0,
            data_ages=data_ages_for_l6(),
        )
    }
    assert metrics["angles"].missed_deadlines == 99


def test_equivalent_active_read_satisfies_poll_and_moves_deadline() -> None:
    scheduler = PollScheduler(get_declared_capabilities("l6", side="left"))
    scheduler.replace(PollingPlan(angles_hz=10), now=0.0)

    assert scheduler.take_due(now=0.0) is TelemetrySemantic.ANGLES
    scheduler.mark_completed(TelemetrySemantic.ANGLES, now=0.05)

    assert scheduler.take_due(now=0.1) is None
    assert scheduler.take_due(now=0.15) is TelemetrySemantic.ANGLES


def test_equal_plan_is_noop_and_replacement_restarts_deadlines() -> None:
    scheduler = PollScheduler(get_declared_capabilities("l6", side="left"))
    plan = PollingPlan(angles_hz=20)

    assert scheduler.replace(plan, now=1.0)
    assert not scheduler.replace(plan, now=99.0)
    assert scheduler.next_deadline() == 1.0

    assert scheduler.replace(PollingPlan(angles_hz=10), now=2.0)
    assert scheduler.next_deadline() == 2.0
    assert scheduler.stop()
    assert not scheduler.stop()


def test_static_transaction_budget_is_rejected_instead_of_silent_throttling() -> None:
    scheduler = PollScheduler(get_declared_capabilities("l6", side="left"))

    with pytest.raises(PollingPlanError, match="utilization"):
        scheduler.replace(PollingPlan(force_hz=30), now=0.0)


def test_metrics_keep_declared_channels_even_when_polling_is_off() -> None:
    capabilities = get_declared_capabilities("l6", side="left")
    scheduler = PollScheduler(capabilities)

    metrics = scheduler.metrics(now=1.0, data_ages=data_ages_for_l6())

    assert {item.channel for item in metrics} == {
        item.semantic.value for item in capabilities.telemetry
    }
    assert all(item.requested_hz == 0 for item in metrics)
