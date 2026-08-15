"""Thread-safe logical command scheduling primitives."""

from __future__ import annotations

import threading
from collections import deque

from ..backend import Clock
from ..contracts import AckSupport, CommandPolicy, CommandStage
from ..errors import HandSDKError, QueueFullError, ValidationError
from .model import CommandHandle, HandCommand


class CommandScheduler:
    """Bounded scheduler with one shared slot for continuous motion targets.

    The scheduler performs no device I/O. Continuous command kinds share one
    latest-wins slot so changing control mode cannot leave an older target
    waiting behind the new one. Discrete operations use a separate bounded
    FIFO.
    """

    def __init__(
        self,
        *,
        clock: Clock,
        discrete_queue_size: int,
    ) -> None:
        """Initialize independent latest-wins and bounded FIFO queues.

        Args:
            clock: Clock used for externally visible transition timestamps.
            discrete_queue_size: Maximum number of queued discrete commands.

        Raises:
            ValidationError: If ``discrete_queue_size`` is not positive.
        """
        if discrete_queue_size <= 0:
            raise ValidationError("discrete_queue_size must be positive")
        self._clock = clock
        self._discrete_queue_size = discrete_queue_size
        self._lock = threading.Lock()
        self._latest_motion: CommandHandle | None = None
        self._unclaimed_motion: CommandHandle | None = None
        self._discrete: deque[CommandHandle] = deque()
        self._next_command_id = 1
        self._superseded_commands = 0
        self._prefer_discrete = True

    @property
    def pending_count(self) -> int:
        """Return commands waiting in scheduler-owned slots."""
        with self._lock:
            return (
                len(self._discrete)
                + int(self._latest_motion is not None)
                + int(self._unclaimed_motion is not None)
            )

    def is_unclaimed(self, handle: CommandHandle) -> bool:
        """Return whether ``handle`` still occupies the pre-dispatch slot."""
        with self._lock:
            return self._unclaimed_motion is handle

    @property
    def superseded_commands(self) -> int:
        """Return the cumulative number of targets superseded before dispatch."""
        with self._lock:
            return self._superseded_commands

    def submit(
        self,
        command: HandCommand,
        *,
        ack_support: AckSupport,
        policy: CommandPolicy | None = None,
    ) -> CommandHandle:
        """Accept a validated command without performing I/O.

        Args:
            command: Validated command to schedule.
            ack_support: Whether the protocol explicitly acknowledges the kind.
            policy: Queueing policy, or ``None`` to select the required default.

        Returns:
            A handle initially in :attr:`CommandStage.ACCEPTED`.

        Raises:
            ValidationError: If the policy does not match the command class.
            QueueFullError: If the bounded discrete FIFO is full.
        """
        effective_policy = policy or (
            CommandPolicy.LATEST if command.kind.is_continuous else CommandPolicy.FIFO
        )
        if command.kind.is_continuous and effective_policy is not CommandPolicy.LATEST:
            raise ValidationError("continuous commands require CommandPolicy.LATEST")
        if (
            not command.kind.is_continuous
            and effective_policy is not CommandPolicy.FIFO
        ):
            raise ValidationError("discrete commands require CommandPolicy.FIFO")

        accepted_at = self._clock.time()
        with self._lock:
            if not command.kind.is_continuous and (
                len(self._discrete) >= self._discrete_queue_size
            ):
                raise QueueFullError(
                    f"discrete command queue capacity is {self._discrete_queue_size}"
                )

            handle = CommandHandle(
                self._next_command_id,
                command,
                ack_support=ack_support,
                accepted_at=accepted_at,
            )
            self._next_command_id += 1

            if command.kind.is_continuous:
                previous_unclaimed = self._unclaimed_motion
                self._unclaimed_motion = None
                if previous_unclaimed is not None:
                    previous_unclaimed._transition(CommandStage.SUPERSEDED, accepted_at)
                    self._superseded_commands += 1

                previous = self._latest_motion
                self._latest_motion = handle
                if previous is not None:
                    previous._transition(CommandStage.SUPERSEDED, accepted_at)
                    self._superseded_commands += 1
            else:
                self._discrete.append(handle)
            return handle

    def pop_next(self) -> CommandHandle | None:
        """Pop one command while alternating between ready queue classes.

        Returns:
            The next handle, or ``None`` when both queue classes are empty.
        """
        with self._lock:
            if self._latest_motion is None and not self._discrete:
                return None
            if self._latest_motion is not None and self._discrete:
                if self._prefer_discrete:
                    handle = self._discrete.popleft()
                else:
                    handle = self._latest_motion
                    self._latest_motion = None
                    self._unclaimed_motion = handle
                self._prefer_discrete = not self._prefer_discrete
                return handle
            if self._discrete:
                self._prefer_discrete = False
                return self._discrete.popleft()
            handle = self._latest_motion
            self._latest_motion = None
            self._unclaimed_motion = handle
            self._prefer_discrete = True
            return handle

    def claim(self, handle: CommandHandle) -> bool:
        """Claim a command immediately before its first transport send.

        Args:
            handle: Worker-owned handle to claim.

        Returns:
            ``False`` when a continuous handle was superseded before the claim.
        """
        with self._lock:
            if not handle.command.kind.is_continuous:
                return handle.stage is CommandStage.ACCEPTED
            if self._unclaimed_motion is not handle:
                return False
            self._unclaimed_motion = None
            return handle.stage is CommandStage.ACCEPTED

    def finish(self, handle: CommandHandle) -> None:
        """Release an unclaimed worker-owned command after dispatch exits.

        Args:
            handle: Handle whose dispatch attempt has ended.
        """
        with self._lock:
            if self._unclaimed_motion is handle:
                self._unclaimed_motion = None

    def clear(self, error: HandSDKError) -> tuple[CommandHandle, ...]:
        """Fail and remove every command that has not been dispatched.

        Args:
            error: Failure recorded on each removed handle.

        Returns:
            Handles removed from latest-wins and FIFO storage.
        """
        timestamp = self._clock.time()
        with self._lock:
            handles = list(self._discrete)
            self._discrete.clear()
            if self._latest_motion is not None:
                handles.append(self._latest_motion)
                self._latest_motion = None
            if self._unclaimed_motion is not None:
                handles.append(self._unclaimed_motion)
                self._unclaimed_motion = None
            for handle in handles:
                handle._transition(CommandStage.FAILED, timestamp, error)
            return tuple(handles)


__all__ = ["CommandScheduler"]
