"""Thread-safe command admission and bounded scheduling for one device."""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from ..api.commands import AckSupport, CommandFamily, CommandHandle, CommandStage
from ..api.errors import (
    ClosedError,
    DeviceNotReadyError,
    HandError,
    QueueFullError,
    ValidationError,
)
from ..api.health import CommandFamilyMetrics
from ..api.readings import RawNumber
from ..capabilities.types import CommandSemantic


class SchedulingPolicy(str, Enum):
    """Internal queue behavior selected from command semantics."""

    LATEST = "latest"
    FIFO = "fifo"


_COMMAND_FAMILIES = {
    CommandSemantic.ANGLES: CommandFamily.ANGLE_TARGET,
    CommandSemantic.SPEEDS: CommandFamily.SPEED_SETTING,
    CommandSemantic.ACCELERATIONS: CommandFamily.ACCELERATION_SETTING,
    CommandSemantic.CURRENT_LIMITS: CommandFamily.CURRENT_LIMIT,
    CommandSemantic.EFFORT_LIMITS: CommandFamily.EFFORT_LIMIT,
    CommandSemantic.TORQUES: CommandFamily.TORQUE_TARGET,
    CommandSemantic.MOTION_TIMES: CommandFamily.MOTION_TIME_SETTING,
    CommandSemantic.STALL_DETECTION_TIMES: CommandFamily.STALL_CONFIGURATION,
    CommandSemantic.STALL_THRESHOLDS: CommandFamily.STALL_CONFIGURATION,
    CommandSemantic.STALL_HOLD_CURRENTS: CommandFamily.STALL_CONFIGURATION,
    CommandSemantic.LIMIT_COMPENSATIONS: CommandFamily.LIMIT_COMPENSATION,
}

_LATEST_FAMILIES = {
    CommandFamily.ANGLE_TARGET,
    CommandFamily.SPEED_SETTING,
    CommandFamily.CURRENT_LIMIT,
    CommandFamily.EFFORT_LIMIT,
    CommandFamily.TORQUE_TARGET,
}


def command_family_for(semantic: CommandSemantic) -> CommandFamily:
    """Return the independent scheduling family for a public semantic."""

    if not isinstance(semantic, CommandSemantic):
        raise ValidationError("semantic must be CommandSemantic")
    return _COMMAND_FAMILIES[semantic]


def scheduling_policy_for(family: CommandFamily) -> SchedulingPolicy:
    """Return latest-wins only for continuous target families."""

    return (
        SchedulingPolicy.LATEST if family in _LATEST_FAMILIES else SchedulingPolicy.FIFO
    )


@dataclass(frozen=True, slots=True)
class ScheduledCommand:
    """One accepted command claimed by the device actor."""

    sequence: int
    family: CommandFamily
    policy: SchedulingPolicy
    handle: CommandHandle
    semantic: CommandSemantic | None = None
    values: tuple[RawNumber, ...] = ()
    operation: str | None = None

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or self.sequence <= 0:
            raise ValidationError("sequence must be a positive int")
        if not isinstance(self.family, CommandFamily):
            raise ValidationError("family must be CommandFamily")
        if not isinstance(self.policy, SchedulingPolicy):
            raise ValidationError("policy must be SchedulingPolicy")
        if not isinstance(self.handle, CommandHandle):
            raise ValidationError("handle must be CommandHandle")
        is_command = self.semantic is not None
        is_operation = self.operation is not None
        if is_command == is_operation:
            raise ValidationError("scheduled work must be a command or operation")
        if is_command and not isinstance(self.semantic, CommandSemantic):
            raise ValidationError("semantic must be CommandSemantic or None")
        if is_operation and (not isinstance(self.operation, str) or not self.operation):
            raise ValidationError("operation must be a non-empty str or None")


@dataclass(slots=True)
class _MutableMetrics:
    accepted: int = 0
    dispatched: int = 0
    acknowledged: int = 0
    failed: int = 0
    superseded: int = 0


class CommandIngress:
    """Atomic lifecycle admission, latest slots, and bounded configuration FIFO."""

    def __init__(
        self,
        *,
        discrete_capacity: int,
        monotonic: Callable[[], float],
        wake: Callable[[], None],
    ) -> None:
        if type(discrete_capacity) is not int or discrete_capacity <= 0:
            raise ValidationError("discrete_capacity must be a positive int")
        if not callable(monotonic) or not callable(wake):
            raise ValidationError("monotonic and wake must be callable")
        self._capacity = discrete_capacity
        self._monotonic = monotonic
        self._wake = wake
        self._lock = threading.Lock()
        self._accepting = False
        self._admission_error: HandError = ClosedError("device is closed")
        self._generation = 0
        self._next_command_id = 0
        self._next_sequence = 0
        self._latest: dict[CommandFamily, ScheduledCommand] = {}
        self._fifo: deque[ScheduledCommand] = deque()
        self._metrics = {family: _MutableMetrics() for family in CommandFamily}

    def activate(self, *, generation: int) -> None:
        """Open admission for one verified connection generation."""

        if type(generation) is not int or generation <= 0:
            raise ValidationError("generation must be a positive int")
        with self._lock:
            if self._latest or self._fifo:
                raise DeviceNotReadyError("cannot activate with retained commands")
            self._generation = generation
            self._accepting = True
            self._admission_error = DeviceNotReadyError(
                "device connection is not ready",
                generation=generation,
            )

    def reject_new(self, error: HandError) -> None:
        """Close admission atomically without changing already accepted work."""

        if not isinstance(error, HandError):
            raise ValidationError("error must be HandError")
        with self._lock:
            self._accepting = False
            self._admission_error = error

    def submit(
        self,
        semantic: CommandSemantic,
        values: tuple[RawNumber, ...],
        *,
        ack_support: AckSupport,
    ) -> CommandHandle:
        """Accept one validated command without encoding or transport access."""

        family = command_family_for(semantic)
        policy = scheduling_policy_for(family)
        return self._submit(
            family=family,
            policy=policy,
            ack_support=ack_support,
            semantic=semantic,
            values=values,
        )

    def submit_operation(
        self,
        operation: str,
        *,
        ack_support: AckSupport,
    ) -> CommandHandle:
        """Accept one discrete operation into the shared bounded FIFO."""

        if not isinstance(operation, str) or not operation:
            raise ValidationError("operation must be a non-empty str")
        return self._submit(
            family=CommandFamily.DISCRETE_OPERATION,
            policy=SchedulingPolicy.FIFO,
            ack_support=ack_support,
            operation=operation,
        )

    def _submit(
        self,
        *,
        family: CommandFamily,
        policy: SchedulingPolicy,
        ack_support: AckSupport,
        semantic: CommandSemantic | None = None,
        values: tuple[RawNumber, ...] = (),
        operation: str | None = None,
    ) -> CommandHandle:
        if not isinstance(ack_support, AckSupport):
            raise ValidationError("ack_support must be AckSupport")
        replaced: ScheduledCommand | None = None
        with self._lock:
            if not self._accepting:
                raise self._admission_error
            if policy is SchedulingPolicy.FIFO and len(self._fifo) >= self._capacity:
                raise QueueFullError(
                    "device configuration and operation queue is full",
                    generation=self._generation,
                )
            self._next_command_id += 1
            self._next_sequence += 1
            now = self._monotonic()
            handle = CommandHandle(
                command_id=self._next_command_id,
                family=family,
                ack_support=ack_support,
                accepted_at=now,
            )
            scheduled = ScheduledCommand(
                sequence=self._next_sequence,
                family=family,
                policy=policy,
                handle=handle,
                semantic=semantic,
                values=values,
                operation=operation,
            )
            if policy is SchedulingPolicy.LATEST:
                replaced = self._latest.get(family)
                self._latest[family] = scheduled
            else:
                self._fifo.append(scheduled)
            metrics = self._metrics[family]
            metrics.accepted += 1
            if replaced is not None:
                replaced.handle._mark_superseded(timestamp=now)
                metrics.superseded += 1
        self._wake()
        return handle

    def take_next(self) -> ScheduledCommand | None:
        """Atomically claim the oldest currently retained command."""

        with self._lock:
            candidates = tuple(self._latest.values())
            if self._fifo:
                candidates += (self._fifo[0],)
            if not candidates:
                return None
            command = min(candidates, key=lambda value: value.sequence)
            if command.policy is SchedulingPolicy.LATEST:
                del self._latest[command.family]
            else:
                claimed = self._fifo.popleft()
                if claimed is not command:
                    raise RuntimeError("command FIFO changed while locked")
            return command

    def take_latest(self, family: CommandFamily) -> ScheduledCommand | None:
        """Temporarily take one newer target for transport-level replacement."""

        if not isinstance(family, CommandFamily):
            raise ValidationError("family must be CommandFamily")
        if scheduling_policy_for(family) is not SchedulingPolicy.LATEST:
            raise ValidationError("family does not use latest scheduling")
        with self._lock:
            return self._latest.pop(family, None)

    def restore_latest(self, command: ScheduledCommand) -> bool:
        """Restore a target when its prior transport job was already claimed.

        A concurrent newer target remains authoritative. If lifecycle admission
        closed while the command was temporarily removed, it fails instead of
        being retained for a later connection generation.
        """

        if not isinstance(command, ScheduledCommand):
            raise ValidationError("command must be ScheduledCommand")
        if command.policy is not SchedulingPolicy.LATEST:
            raise ValidationError("only latest commands can be restored")
        retained = False
        with self._lock:
            metrics = self._metrics[command.family]
            now = self._monotonic()
            if not self._accepting:
                command.handle._mark_failed(
                    self._admission_error,
                    timestamp=now,
                    sent_frames=0,
                )
                metrics.failed += 1
            else:
                newer = self._latest.get(command.family)
                if newer is None:
                    self._latest[command.family] = command
                    retained = True
                else:
                    if newer.sequence <= command.sequence:
                        raise RuntimeError("latest command sequence moved backwards")
                    command.handle._mark_superseded(timestamp=now)
                    metrics.superseded += 1
        if retained:
            self._wake()
        return retained

    def fail_pending(self, error: HandError) -> tuple[CommandHandle, ...]:
        """Fail and remove all unclaimed work from an ending generation."""

        if not isinstance(error, HandError):
            raise ValidationError("error must be HandError")
        with self._lock:
            pending = tuple(self._latest.values()) + tuple(self._fifo)
            self._latest.clear()
            self._fifo.clear()
            now = self._monotonic()
            for command in pending:
                command.handle._mark_failed(error, timestamp=now, sent_frames=0)
                self._metrics[command.family].failed += 1
            return tuple(command.handle for command in pending)

    def record_stage(self, handle: CommandHandle, stage: CommandStage) -> None:
        """Record one actor-owned terminal or delivery transition."""

        if not isinstance(handle, CommandHandle) or not isinstance(stage, CommandStage):
            raise ValidationError("handle and stage have invalid types")
        with self._lock:
            metrics = self._metrics[handle.family]
            if stage is CommandStage.DISPATCHED:
                metrics.dispatched += 1
            elif stage is CommandStage.ACKNOWLEDGED:
                metrics.acknowledged += 1
            elif stage is CommandStage.FAILED:
                metrics.failed += 1
            elif stage is CommandStage.SUPERSEDED:
                metrics.superseded += 1

    @property
    def pending_count(self) -> int:
        """Return bounded unclaimed depth without transport access."""

        with self._lock:
            return len(self._latest) + len(self._fifo)

    def has_pending(self, family: CommandFamily) -> bool:
        """Return whether one scheduling family has unclaimed work."""

        if not isinstance(family, CommandFamily):
            raise ValidationError("family must be CommandFamily")
        with self._lock:
            if family in self._latest:
                return True
            return any(command.family is family for command in self._fifo)

    @property
    def discrete_depth(self) -> int:
        """Return current configuration/discrete FIFO depth."""

        with self._lock:
            return len(self._fifo)

    def metrics(self) -> tuple[CommandFamilyMetrics, ...]:
        """Return immutable counters and current per-family pending depths."""

        with self._lock:
            pending = {family: 0 for family in CommandFamily}
            for family in self._latest:
                pending[family] += 1
            for command in self._fifo:
                pending[command.family] += 1
            return tuple(
                CommandFamilyMetrics(
                    family=family,
                    pending=pending[family],
                    accepted=value.accepted,
                    dispatched=value.dispatched,
                    acknowledged=value.acknowledged,
                    failed=value.failed,
                    superseded=value.superseded,
                )
                for family, value in self._metrics.items()
            )


__all__ = [
    "CommandIngress",
    "ScheduledCommand",
    "SchedulingPolicy",
    "command_family_for",
    "scheduling_policy_for",
]
