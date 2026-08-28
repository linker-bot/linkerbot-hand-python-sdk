"""Non-blocking command handles and precise delivery-stage semantics."""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from enum import Enum

from .errors import ErrorInfo, LifecycleError, TimeoutError, ValidationError


class CommandStage(str, Enum):
    """Observable delivery stage of an accepted device command."""

    ACCEPTED = "accepted"
    DISPATCHED = "dispatched"
    ACKNOWLEDGED = "acknowledged"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class AckSupport(str, Enum):
    """Whether a command has a matchable positive device-protocol ACK."""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"


class CommandFamily(str, Enum):
    """Independent scheduling and latest-wins domains."""

    ANGLE_TARGET = "angle_target"
    SPEED_SETTING = "speed_setting"
    ACCELERATION_SETTING = "acceleration_setting"
    CURRENT_LIMIT = "current_limit"
    EFFORT_LIMIT = "effort_limit"
    TORQUE_TARGET = "torque_target"
    MOTION_TIME_SETTING = "motion_time_setting"
    STALL_CONFIGURATION = "stall_configuration"
    LIMIT_COMPENSATION = "limit_compensation"
    DISCRETE_OPERATION = "discrete_operation"


@dataclass(frozen=True, slots=True)
class CommandTransition:
    """One immutable command stage transition."""

    stage: CommandStage
    timestamp: float
    sent_frames: int


def _coerce_timeout(timeout: float | None) -> float | None:
    if timeout is None:
        return None
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise ValidationError("timeout must be finite and positive")
    return float(timeout)


class CommandHandle:
    """Thread-safe observer for one non-blocking command.

    ``DISPATCHED`` means every request frame was successfully handed to the
    transport. It is terminal only when :attr:`ack_support` is unsupported.
    The handle never represents mechanical arrival at the target.
    """

    def __init__(
        self,
        command_id: int,
        family: CommandFamily,
        *,
        ack_support: AckSupport,
        accepted_at: float,
    ) -> None:
        if type(command_id) is not int or command_id <= 0:
            raise ValidationError("command_id must be a positive int")
        if not isinstance(family, CommandFamily):
            raise ValidationError("family must be CommandFamily")
        if not isinstance(ack_support, AckSupport):
            raise ValidationError("ack_support must be AckSupport")
        if (
            isinstance(accepted_at, bool)
            or not isinstance(accepted_at, (int, float))
            or not math.isfinite(accepted_at)
        ):
            raise ValidationError("accepted_at must be finite numeric")
        self.command_id = command_id
        self.family = family
        self.ack_support = ack_support
        self.accepted_at = float(accepted_at)
        self._lock = threading.Lock()
        self._completed = threading.Event()
        self._stage = CommandStage.ACCEPTED
        self._sent_frames = 0
        self._error: ErrorInfo | None = None
        self._history = [CommandTransition(CommandStage.ACCEPTED, self.accepted_at, 0)]

    @property
    def stage(self) -> CommandStage:
        """Return the latest delivery stage."""

        with self._lock:
            return self._stage

    @property
    def sent_frames(self) -> int:
        """Return how many command frames the transport sent successfully."""

        with self._lock:
            return self._sent_frames

    @property
    def error(self) -> ErrorInfo | None:
        """Return structured terminal failure information, if present."""

        with self._lock:
            return self._error

    @property
    def history(self) -> tuple[CommandTransition, ...]:
        """Return an immutable copy of the stage history."""

        with self._lock:
            return tuple(self._history)

    @property
    def done(self) -> bool:
        """Return whether the handle reached its protocol terminal stage."""

        return self._completed.is_set()

    def wait(self, timeout: float | None = None) -> CommandStage:
        """Wait for protocol completion without waiting for mechanical arrival."""

        timeout_s = _coerce_timeout(timeout)
        if not self._completed.wait(timeout_s):
            raise TimeoutError(
                f"command {self.command_id} did not complete before the deadline"
            )
        return self.stage

    def _mark_dispatched(self, *, timestamp: float, sent_frames: int) -> None:
        self._transition(
            CommandStage.DISPATCHED,
            timestamp=timestamp,
            sent_frames=sent_frames,
        )

    def _mark_acknowledged(self, *, timestamp: float) -> None:
        self._transition(
            CommandStage.ACKNOWLEDGED,
            timestamp=timestamp,
            sent_frames=self.sent_frames,
        )

    def _mark_failed(
        self,
        error: BaseException,
        *,
        timestamp: float,
        sent_frames: int,
    ) -> None:
        self._transition(
            CommandStage.FAILED,
            timestamp=timestamp,
            sent_frames=sent_frames,
            error=error,
        )

    def _mark_superseded(self, *, timestamp: float) -> None:
        self._transition(
            CommandStage.SUPERSEDED,
            timestamp=timestamp,
            sent_frames=0,
        )

    def _transition(
        self,
        stage: CommandStage,
        *,
        timestamp: float,
        sent_frames: int,
        error: BaseException | None = None,
    ) -> None:
        if (
            isinstance(timestamp, bool)
            or not isinstance(timestamp, (int, float))
            or not math.isfinite(timestamp)
        ):
            raise ValidationError("transition timestamp must be finite numeric")
        if type(sent_frames) is not int or sent_frames < 0:
            raise ValidationError("sent_frames must be a non-negative int")
        if stage is CommandStage.DISPATCHED and sent_frames == 0:
            raise ValidationError(
                "a dispatched command must have sent at least one frame"
            )
        with self._lock:
            if self._completed.is_set():
                raise LifecycleError(
                    f"command {self.command_id} already completed as "
                    f"{self._stage.value}"
                )
            allowed = {
                CommandStage.ACCEPTED: {
                    CommandStage.DISPATCHED,
                    CommandStage.FAILED,
                    CommandStage.SUPERSEDED,
                },
                CommandStage.DISPATCHED: {
                    CommandStage.ACKNOWLEDGED,
                    CommandStage.FAILED,
                },
            }
            if stage not in allowed.get(self._stage, set()):
                raise LifecycleError(
                    f"illegal command transition {self._stage.value} -> {stage.value}"
                )
            if timestamp < self._history[-1].timestamp:
                raise LifecycleError("command transition timestamp moved backwards")
            if (
                self._stage is CommandStage.DISPATCHED
                and sent_frames != self._sent_frames
            ):
                raise LifecycleError(
                    "post-dispatch transition changed the sent frame count"
                )
            if stage is CommandStage.ACKNOWLEDGED and (
                self.ack_support is AckSupport.UNSUPPORTED
            ):
                raise LifecycleError("unsupported ACK cannot be acknowledged")
            self._stage = stage
            self._sent_frames = sent_frames
            self._error = None if error is None else ErrorInfo.from_exception(error)
            self._history.append(
                CommandTransition(stage, float(timestamp), sent_frames)
            )
            if stage in {
                CommandStage.ACKNOWLEDGED,
                CommandStage.FAILED,
                CommandStage.SUPERSEDED,
            } or (
                stage is CommandStage.DISPATCHED
                and self.ack_support is AckSupport.UNSUPPORTED
            ):
                self._completed.set()


__all__ = [
    "AckSupport",
    "CommandFamily",
    "CommandHandle",
    "CommandStage",
    "CommandTransition",
]
