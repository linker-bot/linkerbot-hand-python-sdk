from __future__ import annotations

from dataclasses import replace

import pytest

from linkerbot_hand.api.config import PollingPlan
from linkerbot_hand.api.enums import AngleFeedbackPolicy, DataOrigin
from linkerbot_hand.api.errors import PollingPlanError
from linkerbot_hand.api.health import DataAge
from linkerbot_hand.capabilities.registry import get_declared_capabilities
from linkerbot_hand.capabilities.types import CommandSemantic, TelemetrySemantic
from linkerbot_hand.runtime._clock import SystemClock
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
            origin=DataOrigin.NONE,
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


def test_poll_response_time_does_not_reduce_the_requested_frequency() -> None:
    scheduler = PollScheduler(get_declared_capabilities("l6", side="left"))
    scheduler.replace(PollingPlan(angles_hz=10), now=0.0)

    assert scheduler.take_due(now=0.0) is TelemetrySemantic.ANGLES
    scheduler.mark_completed(
        TelemetrySemantic.ANGLES,
        now=0.05,
        requested_by_poll=True,
    )

    assert scheduler.take_due(now=0.099) is None
    assert scheduler.take_due(now=0.1) is TelemetrySemantic.ANGLES


def test_named_due_take_does_not_advance_unrelated_channels() -> None:
    scheduler = PollScheduler(get_declared_capabilities("l6", side="left"))
    scheduler.replace(PollingPlan(angles_hz=100, temperature_hz=10), now=0.0)

    assert (
        scheduler.take_due_channel(TelemetrySemantic.ANGLES, now=0.0)
        is TelemetrySemantic.ANGLES
    )
    assert scheduler.take_due_channel(TelemetrySemantic.ANGLES, now=0.0) is None
    assert scheduler.take_due(now=0.0) is TelemetrySemantic.TEMPERATURES


def test_active_read_satisfies_the_next_poll_without_creating_backlog() -> None:
    scheduler = PollScheduler(get_declared_capabilities("l6", side="left"))
    scheduler.replace(PollingPlan(angles_hz=10), now=0.0)
    assert scheduler.take_due(now=0.0) is TelemetrySemantic.ANGLES
    scheduler.mark_completed(
        TelemetrySemantic.ANGLES,
        now=0.01,
        requested_by_poll=True,
    )

    scheduler.mark_completed(
        TelemetrySemantic.ANGLES,
        now=0.05,
        requested_by_poll=False,
    )

    assert scheduler.take_due(now=0.1) is None
    assert scheduler.take_due(now=0.15) is TelemetrySemantic.ANGLES

    metrics = {
        item.channel: item
        for item in scheduler.metrics(now=0.15, data_ages=data_ages_for_l6())
    }
    assert metrics["angles"].completed_reads == 2


def test_adaptive_angle_command_feedback_does_not_count_as_a_read() -> None:
    scheduler = PollScheduler(get_declared_capabilities("l6", side="left"))
    scheduler.replace(PollingPlan(angles_hz=10), now=0.0)

    assert scheduler.mark_angle_command_feedback(now=0.0)
    assert scheduler.take_due(now=0.099) is None
    assert scheduler.take_due(now=0.1) is TelemetrySemantic.ANGLES

    metrics = next(
        item
        for item in scheduler.metrics(now=0.1, data_ages=data_ages_for_l6())
        if item.channel == "angles"
    )
    assert metrics.completed_reads == 0
    assert metrics.command_feedback_samples == 1
    assert metrics.feedback_samples == 1
    assert metrics.feedback_policy is AngleFeedbackPolicy.ADAPTIVE


def test_query_only_caches_command_feedback_without_suppressing_queries() -> None:
    scheduler = PollScheduler(get_declared_capabilities("l6", side="left"))
    scheduler.replace(
        PollingPlan(
            angles_hz=10,
            angle_feedback=AngleFeedbackPolicy.QUERY_ONLY,
        ),
        now=0.0,
    )

    assert not scheduler.mark_angle_command_feedback(now=0.0)
    assert scheduler.take_due(now=0.0) is TelemetrySemantic.ANGLES

    metrics = next(
        item
        for item in scheduler.metrics(now=0.01, data_ages=data_ages_for_l6())
        if item.channel == "angles"
    )
    assert metrics.command_feedback_samples == 1
    assert metrics.feedback_samples == 0
    assert metrics.feedback_policy is AngleFeedbackPolicy.QUERY_ONLY


def test_feedback_only_records_missed_deadlines_without_queries() -> None:
    scheduler = PollScheduler(get_declared_capabilities("l6", side="left"))
    scheduler.replace(
        PollingPlan(
            angles_hz=10,
            angle_feedback=AngleFeedbackPolicy.COMMAND_FEEDBACK_ONLY,
        ),
        now=0.0,
    )

    assert scheduler.take_due(now=0.0) is None
    assert scheduler.take_due(now=0.1) is None
    assert scheduler.mark_angle_command_feedback(now=0.15)
    assert scheduler.take_due(now=0.249) is None

    metrics = next(
        item
        for item in scheduler.metrics(now=0.25, data_ages=data_ages_for_l6())
        if item.channel == "angles"
    )
    assert metrics.missed_deadlines == 1
    assert metrics.completed_reads == 0
    assert metrics.command_feedback_samples == 1
    assert metrics.feedback_samples == 1
    assert metrics.feedback_policy is AngleFeedbackPolicy.COMMAND_FEEDBACK_ONLY


def test_feedback_only_rejects_profile_without_current_state_responses() -> None:
    capabilities = get_declared_capabilities("l6", side="left")
    angles = capabilities.command(CommandSemantic.ANGLES)
    assert angles is not None
    no_feedback = replace(
        capabilities,
        commands=tuple(
            replace(command, returns_current_state=False)
            if command.semantic is CommandSemantic.ANGLES
            else command
            for command in capabilities.commands
        ),
    )
    scheduler = PollScheduler(no_feedback)

    with pytest.raises(PollingPlanError, match="current-state feedback"):
        scheduler.replace(
            PollingPlan(
                angles_hz=10,
                angle_feedback=AngleFeedbackPolicy.COMMAND_FEEDBACK_ONLY,
            ),
            now=0.0,
        )


def test_adaptive_no_feedback_profile_never_uses_commands_as_feedback() -> None:
    capabilities = get_declared_capabilities("o20", side="left")
    scheduler = PollScheduler(capabilities)
    scheduler.replace(PollingPlan(angles_hz=10), now=0.0)

    assert not scheduler.angle_command_can_fulfill_feedback
    assert scheduler.take_due(now=0.0) is TelemetrySemantic.ANGLES


def test_equal_plan_is_noop_and_replacement_restarts_deadlines() -> None:
    scheduler = PollScheduler(get_declared_capabilities("l6", side="left"))
    plan = PollingPlan(angles_hz=20)

    assert scheduler.replace(plan, now=1.0)
    scheduler.mark_completed(
        TelemetrySemantic.ANGLES,
        now=1.01,
        requested_by_poll=True,
    )
    assert not scheduler.replace(plan, now=99.0)
    assert scheduler.next_deadline() == 1.0
    assert (
        next(
            item
            for item in scheduler.metrics(now=99.0, data_ages=data_ages_for_l6())
            if item.channel == "angles"
        ).completed_reads
        == 1
    )

    assert scheduler.replace(PollingPlan(angles_hz=10), now=2.0)
    assert scheduler.next_deadline() == 2.0
    assert (
        next(
            item
            for item in scheduler.metrics(now=2.0, data_ages=data_ages_for_l6())
            if item.channel == "angles"
        ).completed_reads
        == 0
    )
    assert scheduler.stop()
    assert (
        next(
            item
            for item in scheduler.metrics(now=3.0, data_ages=data_ages_for_l6())
            if item.channel == "angles"
        ).completed_reads
        == 0
    )
    assert not scheduler.stop()


def test_high_requested_rates_are_admitted_without_static_throttling() -> None:
    for model, side in (("l6", "left"), ("o6", "right")):
        scheduler = PollScheduler(get_declared_capabilities(model, side=side))

        assert scheduler.replace(
            PollingPlan(angles_hz=10_000, force_hz=1_000),
            now=0.0,
        )
        assert scheduler.take_due(now=0.0) is TelemetrySemantic.ANGLES
        assert scheduler.take_due(now=0.0) is TelemetrySemantic.FORCE


def test_metrics_keep_declared_channels_even_when_polling_is_off() -> None:
    capabilities = get_declared_capabilities("l6", side="left")
    scheduler = PollScheduler(capabilities)

    metrics = scheduler.metrics(now=1.0, data_ages=data_ages_for_l6())

    assert {item.channel for item in metrics} == {
        item.semantic.value for item in capabilities.telemetry
    }
    assert all(item.requested_hz == 0 for item in metrics)


def test_stop_prevents_new_poll_claims_and_idle_tracks_started_work() -> None:
    scheduler = PollScheduler(get_declared_capabilities("l6", side="left"))
    clock = SystemClock()
    scheduler.replace(PollingPlan(angles_hz=10), now=0.0)

    assert scheduler.begin_poll(TelemetrySemantic.ANGLES)
    assert not scheduler.wait_idle(clock=clock, timeout_s=0)
    assert scheduler.stop()
    assert not scheduler.begin_poll(TelemetrySemantic.ANGLES)

    scheduler.finish_poll()
    assert scheduler.wait_idle(clock=clock, timeout_s=0)
