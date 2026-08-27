"""Deadline-based, non-backlogging SDK polling scheduler."""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass

from ..api.config import PollingPlan
from ..api.errors import PollingPlanError, ValidationError
from ..api.health import ChannelMetrics, DataAge
from ..capabilities.types import DeviceCapabilities, TelemetrySemantic
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
    completed: int = 0
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

    def replace(self, plan: PollingPlan, *, now: float) -> bool:
        """Atomically install a plan; equal plans are strict no-ops."""

        if not isinstance(plan, PollingPlan):
            raise ValidationError("plan must be PollingPlan")
        _validate_time(now)
        self._validate_budget(plan)
        with self._lock:
            if plan == self._plan:
                return False
            self._plan = plan
            self._states = {
                POLL_CHANNELS[channel]: _PollState(
                    requested_hz=rate,
                    period_s=1.0 / rate,
                    next_deadline_s=now,
                    started_at_s=now,
                )
                for channel, rate in plan.enabled_rates()
            }
            return True

    def stop(self) -> bool:
        """Cancel every future deadline without retaining missed ticks."""

        with self._lock:
            changed = self._plan is not None
            self._plan = None
            self._states.clear()
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
            state.missed_deadlines += max(0, periods - 1)
            state.next_deadline_s += periods * state.period_s
            return semantic

    def mark_completed(
        self,
        semantic: TelemetrySemantic,
        *,
        now: float,
        requested_by_poll: bool,
    ) -> None:
        """Count a sample without adding response time to periodic deadlines."""

        _validate_time(now)
        if not isinstance(requested_by_poll, bool):
            raise ValidationError("requested_by_poll must be bool")
        with self._lock:
            state = self._states.get(semantic)
            if state is None:
                return
            state.completed += 1
            if not requested_by_poll:
                state.next_deadline_s = max(
                    state.next_deadline_s,
                    now + state.period_s,
                )

    def mark_timeout(self, semantic: TelemetrySemantic) -> None:
        """Count one failed due transaction without creating a retry backlog."""

        with self._lock:
            state = self._states.get(semantic)
            if state is not None:
                state.timeouts += 1

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

    @property
    def active(self) -> bool:
        """Return whether at least one periodic query is enabled."""

        with self._lock:
            return bool(self._states)

    @property
    def plan(self) -> PollingPlan | None:
        """Return the immutable installed plan."""

        with self._lock:
            return self._plan

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
        state = self._states.get(semantic)
        elapsed = 0.0 if state is None else max(0.0, now - state.started_at_s)
        achieved = (
            0.0 if state is None or elapsed <= 0 else float(state.completed) / elapsed
        )
        return ChannelMetrics(
            channel=semantic.value,
            requested_hz=0.0 if state is None else state.requested_hz,
            achieved_hz=achieved,
            missed_deadlines=0 if state is None else state.missed_deadlines,
            timeouts=0 if state is None else state.timeouts,
            data_age=data_ages[semantic],
        )

    def _validate_budget(self, plan: PollingPlan) -> None:
        utilization = 0.0
        for channel, rate in plan.enabled_rates():
            semantic = POLL_CHANNELS[channel]
            telemetry = self._capabilities.telemetry_channel(semantic)
            if telemetry is None or not telemetry.device_queried:
                raise PollingPlanError(
                    f"{self._capabilities.model} cannot poll {channel}",
                    operation="start_polling",
                    model=self._capabilities.model,
                )
            if (
                telemetry.verified_max_hz is not None
                and rate > telemetry.verified_max_hz
            ):
                raise PollingPlanError(
                    f"{channel} rate exceeds verified maximum "
                    f"{telemetry.verified_max_hz:g} Hz",
                    operation="start_polling",
                    model=self._capabilities.model,
                )
            utilization += rate * telemetry.estimated_transaction_s
        if utilization > 1.0 + 1e-12:
            raise PollingPlanError(
                f"polling plan estimated utilization is {utilization:.3f}, above 1.0",
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
