"""Deadline-based, non-backlogging SDK polling scheduler."""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass

from ..api.config import PollingPlan
from ..api.enums import AngleFeedbackPolicy
from ..api.errors import PollingPlanError, ValidationError
from ..api.health import ChannelMetrics, DataAge
from ..capabilities.types import (
    CommandSemantic,
    DeviceCapabilities,
    TelemetrySemantic,
)
from ._clock import RuntimeClock

POLL_CHANNELS = {
    "angles": TelemetrySemantic.ANGLES,
    "speeds": TelemetrySemantic.MEASURED_SPEEDS,
    "force": TelemetrySemantic.FORCE,
    "temperature": TelemetrySemantic.TEMPERATURES,
    "current": TelemetrySemantic.CURRENTS,
    "faults": TelemetrySemantic.FAULTS,
    "torques": TelemetrySemantic.TORQUES,
    "voltage": TelemetrySemantic.VOLTAGES,
}

_CHANNEL_NAMES = {semantic: name for name, semantic in POLL_CHANNELS.items()}
_DEADLINE_EPSILON_S = 1e-12


@dataclass(slots=True)
class _PollState:
    requested_hz: float
    period_s: float
    next_deadline_s: float
    started_at_s: float
    completed_reads: int = 0
    command_feedback_samples: int = 0
    feedback_samples: int = 0
    missed_deadlines: int = 0
    timeouts: int = 0


class PollScheduler:
    """Own only the next deadline for every enabled telemetry channel."""

    def __init__(self, capabilities: DeviceCapabilities) -> None:
        if not isinstance(capabilities, DeviceCapabilities):
            raise ValidationError("capabilities must be DeviceCapabilities")
        self._capabilities = capabilities
        self._lock = threading.Lock()
        self._plan: PollingPlan | None = None
        self._states: dict[TelemetrySemantic, _PollState] = {}
        self._disabled_states: dict[TelemetrySemantic, _PollState] = {}
        self._in_flight = 0
        self._idle = threading.Event()
        self._idle.set()

    def reconfigure(self, capabilities: DeviceCapabilities) -> None:
        """Replace the verified profile only while polling is stopped."""

        if not isinstance(capabilities, DeviceCapabilities):
            raise ValidationError("capabilities must be DeviceCapabilities")
        with self._lock:
            if self._states:
                raise PollingPlanError("cannot change capabilities while polling")
            self._capabilities = capabilities
            self._disabled_states.clear()

    def replace(self, plan: PollingPlan, *, now: float) -> bool:
        """Atomically install a plan; equal plans are strict no-ops."""

        if not isinstance(plan, PollingPlan):
            raise ValidationError("plan must be PollingPlan")
        _validate_time(now)
        self._validate_channels(plan)
        with self._lock:
            if plan == self._plan:
                return False
            self._plan = plan
            states: dict[TelemetrySemantic, _PollState] = {}
            for channel, rate in plan.enabled_rates():
                semantic = POLL_CHANNELS[channel]
                period_s = 1.0 / rate
                waits_for_first_period = semantic is TelemetrySemantic.ANGLES and (
                    plan.angle_feedback is AngleFeedbackPolicy.COMMAND_FEEDBACK_ONLY
                )
                states[semantic] = _PollState(
                    requested_hz=rate,
                    period_s=period_s,
                    next_deadline_s=now + period_s if waits_for_first_period else now,
                    started_at_s=now,
                )
            self._states = states
            self._disabled_states.clear()
            return True

    def stop(self) -> bool:
        """Cancel every future deadline without retaining missed ticks."""

        with self._lock:
            changed = self._plan is not None
            self._plan = None
            self._states.clear()
            self._disabled_states.clear()
            return changed

    def take_due(self, *, now: float) -> TelemetrySemantic | None:
        """Take one most-overdue channel and advance directly into the future."""

        _validate_time(now)
        with self._lock:
            due = tuple(
                (semantic, state)
                for semantic, state in self._states.items()
                if state.next_deadline_s <= now + _DEADLINE_EPSILON_S
            )
            if not due:
                return None
            semantic, state = min(
                due,
                key=lambda item: (
                    item[1].next_deadline_s,
                    tuple(POLL_CHANNELS.values()).index(item[0]),
                ),
            )
            periods = (
                math.floor(max(0.0, now - state.next_deadline_s) / state.period_s) + 1
            )
            state.next_deadline_s += periods * state.period_s
            if (
                semantic is TelemetrySemantic.ANGLES
                and self._angle_feedback_policy_locked()
                is AngleFeedbackPolicy.COMMAND_FEEDBACK_ONLY
            ):
                state.missed_deadlines += periods
                return None
            state.missed_deadlines += max(0, periods - 1)
            return semantic

    def mark_completed(
        self,
        semantic: TelemetrySemantic,
        *,
        now: float,
        requested_by_poll: bool,
    ) -> None:
        """Count one complete read without adding latency to poll deadlines."""

        _validate_time(now)
        if not isinstance(requested_by_poll, bool):
            raise ValidationError("requested_by_poll must be bool")
        with self._lock:
            state = self._states.get(semantic)
            if state is None:
                return
            state.completed_reads += 1
            fulfills_feedback = not (
                semantic is TelemetrySemantic.ANGLES
                and self._angle_feedback_policy_locked()
                is AngleFeedbackPolicy.COMMAND_FEEDBACK_ONLY
            )
            if fulfills_feedback:
                state.feedback_samples += 1
            if fulfills_feedback and not requested_by_poll:
                state.next_deadline_s = max(
                    state.next_deadline_s,
                    now + state.period_s,
                )

    def mark_angle_command_feedback(self, *, now: float) -> bool:
        """Record verified current-state feedback from an angle command.

        Returns:
            ``True`` when the sample fulfills the active feedback policy.
        """

        _validate_time(now)
        with self._lock:
            state = self._states.get(TelemetrySemantic.ANGLES)
            if state is None:
                return False
            if not self._angle_commands_return_current_state_locked():
                raise RuntimeError(
                    "angle command feedback was recorded for a capability "
                    "without verified current-state responses"
                )
            state.command_feedback_samples += 1
            policy = self._angle_feedback_policy_locked()
            if policy is AngleFeedbackPolicy.QUERY_ONLY:
                return False
            state.feedback_samples += 1
            state.next_deadline_s = max(
                state.next_deadline_s,
                now + state.period_s,
            )
            return True

    def mark_command_feedback_timeout(self) -> None:
        """Count one missing response from a verified angle command."""

        with self._lock:
            state = self._states.get(TelemetrySemantic.ANGLES)
            if state is not None and self._angle_commands_return_current_state_locked():
                state.timeouts += 1

    def mark_timeout(self, semantic: TelemetrySemantic) -> None:
        """Count one failed due transaction without creating a retry backlog."""

        with self._lock:
            state = self._states.get(semantic) or self._disabled_states.get(semantic)
            if state is not None:
                state.timeouts += 1

    def disable_firmware_unsupported(
        self,
        semantic: TelemetrySemantic,
        capabilities: DeviceCapabilities,
    ) -> None:
        """Stop one channel while retaining its final timeout metrics."""

        if not isinstance(semantic, TelemetrySemantic):
            raise ValidationError("semantic must be TelemetrySemantic")
        if not isinstance(capabilities, DeviceCapabilities):
            raise ValidationError("capabilities must be DeviceCapabilities")
        if semantic not in capabilities.firmware_unsupported_telemetry:
            raise ValidationError("capabilities do not mark semantic unsupported")
        with self._lock:
            state = self._states.pop(semantic, None)
            if state is not None:
                self._disabled_states[semantic] = state
            self._capabilities = capabilities

    def begin_poll(self, semantic: TelemetrySemantic) -> bool:
        """Register a due poll only if its plan is still active."""

        if not isinstance(semantic, TelemetrySemantic):
            raise ValidationError("semantic must be TelemetrySemantic")
        with self._lock:
            if semantic not in self._states:
                return False
            self._in_flight += 1
            self._idle.clear()
            return True

    def finish_poll(self) -> None:
        """Release one registered poll and wake a stopping caller."""

        with self._lock:
            if self._in_flight <= 0:
                raise RuntimeError("poll in-flight count moved below zero")
            self._in_flight -= 1
            if self._in_flight == 0:
                self._idle.set()

    def wait_idle(self, *, clock: RuntimeClock, timeout_s: float) -> bool:
        """Wait boundedly until every poll that already started has finished."""

        if not callable(getattr(clock, "wait", None)):
            raise ValidationError("clock must provide wait()")
        if (
            isinstance(timeout_s, bool)
            or not isinstance(timeout_s, (int, float))
            or not math.isfinite(timeout_s)
            or timeout_s < 0
        ):
            raise ValidationError("timeout_s must be finite and non-negative")
        return clock.wait(self._idle, float(timeout_s))

    def next_deadline(self) -> float | None:
        """Return the next scheduler wake-up time without changing state."""

        with self._lock:
            if not self._states:
                return None
            return min(state.next_deadline_s for state in self._states.values())

    def is_due(self, semantic: TelemetrySemantic, *, now: float) -> bool:
        """Return whether one enabled channel has reached its next deadline."""

        if not isinstance(semantic, TelemetrySemantic):
            raise ValidationError("semantic must be TelemetrySemantic")
        _validate_time(now)
        with self._lock:
            state = self._states.get(semantic)
            return (
                state is not None and state.next_deadline_s <= now + _DEADLINE_EPSILON_S
            )

    @property
    def active(self) -> bool:
        """Return whether at least one periodic feedback channel is enabled."""

        with self._lock:
            return bool(self._states)

    @property
    def plan(self) -> PollingPlan | None:
        """Return the immutable installed plan."""

        with self._lock:
            return self._plan

    @property
    def angle_command_can_fulfill_feedback(self) -> bool:
        """Return whether an angle command can satisfy the current plan."""

        with self._lock:
            return (
                TelemetrySemantic.ANGLES in self._states
                and self._angle_commands_return_current_state_locked()
                and self._angle_feedback_policy_locked()
                is not AngleFeedbackPolicy.QUERY_ONLY
            )

    def metrics(
        self,
        *,
        now: float,
        data_ages: dict[TelemetrySemantic, DataAge],
    ) -> tuple[ChannelMetrics, ...]:
        """Return requested/achieved rates while preserving all declared channels."""

        _validate_time(now)
        with self._lock:
            return tuple(
                self._channel_metrics(telemetry.semantic, now, data_ages)
                for telemetry in self._capabilities.telemetry
            )

    def _channel_metrics(
        self,
        semantic: TelemetrySemantic,
        now: float,
        data_ages: dict[TelemetrySemantic, DataAge],
    ) -> ChannelMetrics:
        active_state = self._states.get(semantic)
        state = active_state or self._disabled_states.get(semantic)
        elapsed = 0.0 if state is None else max(0.0, now - state.started_at_s)
        achieved = (
            0.0
            if state is None or elapsed <= 0
            else float(state.feedback_samples) / elapsed
        )
        return ChannelMetrics(
            channel=semantic.value,
            requested_hz=0.0 if active_state is None else active_state.requested_hz,
            achieved_hz=achieved,
            missed_deadlines=0 if state is None else state.missed_deadlines,
            timeouts=0 if state is None else state.timeouts,
            completed_reads=0 if state is None else state.completed_reads,
            command_feedback_samples=(
                0 if state is None else state.command_feedback_samples
            ),
            feedback_samples=0 if state is None else state.feedback_samples,
            feedback_policy=(
                self._angle_feedback_policy_locked()
                if state is not None and semantic is TelemetrySemantic.ANGLES
                else None
            ),
            data_age=data_ages[semantic],
        )

    def _angle_feedback_policy_locked(self) -> AngleFeedbackPolicy:
        if self._plan is None:
            return AngleFeedbackPolicy.ADAPTIVE
        return self._plan.angle_feedback

    def _angle_commands_return_current_state_locked(self) -> bool:
        command = self._capabilities.command(CommandSemantic.ANGLES)
        return command is not None and command.returns_current_state

    def _validate_channels(self, plan: PollingPlan) -> None:
        for channel, _rate in plan.enabled_rates():
            semantic = POLL_CHANNELS[channel]
            telemetry = self._capabilities.telemetry_channel(semantic)
            if (
                telemetry is None
                or not telemetry.device_queried
                or not self._capabilities.telemetry_supported(semantic)
            ):
                raise PollingPlanError(
                    f"{self._capabilities.model} cannot poll {channel}",
                    operation="start_polling",
                    model=self._capabilities.model,
                )
        if (
            plan.angles_hz > 0
            and plan.angle_feedback is AngleFeedbackPolicy.COMMAND_FEEDBACK_ONLY
        ):
            angles = self._capabilities.command(CommandSemantic.ANGLES)
            if angles is None or not angles.returns_current_state:
                raise PollingPlanError(
                    f"{self._capabilities.model} angle commands do not return "
                    "verified current-state feedback",
                    operation="start_polling",
                    model=self._capabilities.model,
                )


def _validate_time(value: object) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValidationError("scheduler time must be finite numeric")


__all__ = ["POLL_CHANNELS", "PollScheduler"]
