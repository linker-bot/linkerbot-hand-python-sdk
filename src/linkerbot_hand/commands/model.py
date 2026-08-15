"""Command values, handles, and lifecycle transitions."""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass

from ..contracts.enums import AckSupport, CommandKind, CommandStage
from ..contracts.errors import ErrorInfo
from ..errors import TimeoutError, ValidationError


@dataclass(frozen=True, slots=True)
class HandCommand:
    """Immutable command submitted to the scheduler.

    Attributes:
        kind: Requested continuous or discrete operation.
        values: Ordered joint values; discrete operations use an empty tuple.
    """

    kind: CommandKind
    values: tuple[float | int, ...] = ()


@dataclass(frozen=True, slots=True)
class CommandTransition:
    """One timestamped command-stage transition.

    Attributes:
        stage: Stage entered by the command.
        timestamp: Wall-clock transition time in seconds.
    """

    stage: CommandStage
    timestamp: float


def coerce_timeout(timeout: float | None) -> float | None:
    """Normalize an optional wait timeout.

    Args:
        timeout: Non-negative seconds, or ``None`` for no timeout.

    Returns:
        The timeout as a float, or ``None``.

    Raises:
        ValidationError: If the timeout is negative, non-numeric, or non-finite.
    """
    if timeout is None:
        return None
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValidationError("timeout must be finite and non-negative")
    try:
        numeric = float(timeout)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValidationError(
            "timeout must be finite and non-negative", cause=exc
        ) from exc
    if not math.isfinite(numeric) or numeric < 0:
        raise ValidationError("timeout must be finite and non-negative")
    return numeric


class CommandHandle:
    """Thread-safe handle for observing scheduler and transport progress.

    A handle starts in :attr:`CommandStage.ACCEPTED`. For protocols without an
    explicit ACK, :attr:`CommandStage.DISPATCHED` is terminal and means only
    that the transport send succeeded.

    Attributes:
        command_id: Session-local monotonically increasing identifier.
        command: Validated command represented by this handle.
        ack_support: Whether an explicit device ACK is expected.
    """

    def __init__(
        self,
        command_id: int,
        command: HandCommand,
        *,
        ack_support: AckSupport,
        accepted_at: float,
    ) -> None:
        """Initialize an accepted command handle.

        Args:
            command_id: Session-local command identifier.
            command: Validated command submitted to the scheduler.
            ack_support: Whether this command kind has protocol ACK support.
            accepted_at: Wall-clock acceptance timestamp.
        """
        self.command_id = command_id
        self.command = command
        self.ack_support = ack_support
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._stage = CommandStage.ACCEPTED
        self._history = [CommandTransition(CommandStage.ACCEPTED, accepted_at)]
        self._error: ErrorInfo | None = None

    @property
    def stage(self) -> CommandStage:
        """Return the latest observed command stage."""
        with self._lock:
            return self._stage

    @property
    def history(self) -> tuple[CommandTransition, ...]:
        """Return an immutable copy of all stage transitions."""
        with self._lock:
            return tuple(self._history)

    @property
    def error(self) -> ErrorInfo | None:
        """Return terminal failure information, if the command failed."""
        with self._lock:
            return self._error

    @property
    def done(self) -> bool:
        """Whether the command has reached a terminal observable stage."""
        return self._done.is_set()

    def wait(self, timeout: float | None = None) -> CommandStage:
        """Wait for the command to reach a terminal stage.

        Args:
            timeout: Maximum seconds to wait, or ``None`` without a deadline.

        Returns:
            Terminal command stage.

        Raises:
            ValidationError: If ``timeout`` is invalid.
            TimeoutError: If the command does not finish before the deadline.
        """
        timeout_s = coerce_timeout(timeout)
        if not self._done.wait(timeout_s):
            raise TimeoutError(f"command {self.command_id} did not finish in time")
        return self.stage

    def _transition(
        self,
        stage: CommandStage,
        timestamp: float,
        error: BaseException | None = None,
    ) -> None:
        with self._lock:
            terminal = self._stage in {
                CommandStage.ACKNOWLEDGED,
                CommandStage.FAILED,
                CommandStage.SUPERSEDED,
            } or (
                self._stage is CommandStage.DISPATCHED
                and self.ack_support is AckSupport.UNSUPPORTED
            )
            if terminal or stage is self._stage:
                return
            self._stage = stage
            self._history.append(CommandTransition(stage, timestamp))
            if error is not None:
                self._error = ErrorInfo.from_exception(error)
            terminal = stage in {
                CommandStage.ACKNOWLEDGED,
                CommandStage.FAILED,
                CommandStage.SUPERSEDED,
            } or (
                stage is CommandStage.DISPATCHED
                and self.ack_support is AckSupport.UNSUPPORTED
            )
            if terminal:
                self._done.set()


__all__ = [
    "CommandHandle",
    "CommandTransition",
    "ErrorInfo",
    "HandCommand",
    "coerce_timeout",
]
