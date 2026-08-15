"""The session's only normal command and polling I/O worker."""

from __future__ import annotations

import threading
from dataclasses import dataclass

from ..backend import DispatchResult
from ..commands.model import CommandHandle
from ..contracts import AckSupport, CommandStage, ConnectionState, SensorType
from ..errors import (
    ClosedError,
    DeviceFaultError,
    DeviceNotReadyError,
    TimeoutError,
    TransportError,
    normalize_error,
)
from .recovery import _RecoveryRuntime
from .state import _Lifecycle

_SCHEDULER_EPSILON_S = 1e-9


@dataclass(frozen=True, slots=True)
class _StopWork:
    pass


@dataclass(frozen=True, slots=True)
class _WaitWork:
    timeout: float | None


@dataclass(frozen=True, slots=True)
class _RecoveryWork:
    token: int


@dataclass(frozen=True, slots=True)
class _CommandWork:
    handle: CommandHandle
    generation: int


@dataclass(frozen=True, slots=True)
class _PollWork:
    sensor: SensorType
    generation: int
    poll_epoch: int


_Work = _StopWork | _WaitWork | _RecoveryWork | _CommandWork | _PollWork


class _WorkerRuntime(_RecoveryRuntime):
    """Select and execute all normal command, polling, and recovery I/O."""

    def _worker_loop(
        self, stop_event: threading.Event, wake_event: threading.Event
    ) -> None:
        while not stop_event.is_set():
            work = self._take_work(stop_event, wake_event)
            if isinstance(work, _StopWork):
                return
            if isinstance(work, _WaitWork):
                self._clock.wait(wake_event, work.timeout)
                continue
            if isinstance(work, _RecoveryWork):
                self._execute_recovery(work.token, stop_event)
                continue
            if isinstance(work, _CommandWork):
                self._execute_command(work.handle, work.generation)
                continue
            self._execute_poll(work.sensor, work.generation, work.poll_epoch)

    def _take_work(
        self, stop_event: threading.Event, wake_event: threading.Event
    ) -> _Work:
        with self._condition:
            if stop_event.is_set() or self._lifecycle is not _Lifecycle.OPEN:
                return _StopWork()
            now = self._clock.monotonic()

            if self._pending_recovery_token is not None:
                token = self._pending_recovery_token
                self._pending_recovery_token = None
                self._active_recovery_token = token
                self._connection_state = ConnectionState.RECONNECTING
                return _RecoveryWork(token)

            if (
                self._connection_state is ConnectionState.ERROR
                and self._config.auto_reconnect
            ):
                due = self._next_reconnect_at or now
                if due <= now:
                    token = self._allocate_recovery_token_locked()
                    self._active_recovery_token = token
                    self._connection_state = ConnectionState.RECONNECTING
                    return _RecoveryWork(token)
                wake_event.clear()
                return _WaitWork(max(0.0, due - now))

            if self._connection_state is not ConnectionState.CONNECTED:
                wake_event.clear()
                return _WaitWork(None)

            due_sensor: SensorType | None = None
            due_poll_at: float | None = None
            if self._polling and self._poll_deadlines:
                due_sensor, due_poll_at = min(
                    self._poll_deadlines.items(), key=lambda item: item[1]
                )
            poll_ready = (
                due_poll_at is not None and due_poll_at <= now + _SCHEDULER_EPSILON_S
            )
            command_pending = self._scheduler.pending_count > 0
            command_ready = (
                command_pending and self._next_command_at <= now + _SCHEDULER_EPSILON_S
            )

            choose_poll = poll_ready and (not command_ready or self._prefer_poll)
            if choose_poll and due_sensor is not None and due_poll_at is not None:
                self._prefer_poll = False
                self._advance_poll_deadline_locked(due_sensor, due_poll_at, now)
                self._poll_inflight_epoch = self._poll_epoch
                return _PollWork(
                    due_sensor,
                    self._connection_generation,
                    self._poll_epoch,
                )
            if command_ready:
                handle = self._scheduler.pop_next()
                if handle is not None:
                    self._prefer_poll = True
                    self._inflight_handle = handle
                    self._next_command_at = now + 1.0 / self._config.command_rate_hz
                    return _CommandWork(
                        handle,
                        self._connection_generation,
                    )

            deadlines: list[float] = []
            if command_pending:
                deadlines.append(self._next_command_at)
            if due_poll_at is not None:
                deadlines.append(due_poll_at)
            timeout = None if not deadlines else max(0.0, min(deadlines) - now)
            wake_event.clear()
            return _WaitWork(timeout)

    def _execute_command(self, handle: CommandHandle, generation: int) -> None:
        with self._condition:
            backend = self._backend
            if (
                backend is None
                or generation != self._connection_generation
                or self._connection_state is not ConnectionState.CONNECTED
            ):
                error = DeviceNotReadyError("connection changed before dispatch")
                handle._transition(CommandStage.FAILED, self._clock.time(), error)
                self._finish_inflight_locked(handle)
                return

        claim_attempted = False
        claimed = False
        dispatched = False

        def claim_dispatch() -> bool:
            nonlocal claim_attempted, claimed
            with self._condition:
                if claim_attempted:
                    return claimed
                claim_attempted = True
                if (
                    self._lifecycle is not _Lifecycle.OPEN
                    or self._connection_state is not ConnectionState.CONNECTED
                    or generation != self._connection_generation
                    or self._inflight_handle is not handle
                ):
                    return False
                claimed = self._scheduler.claim(handle)
                return claimed

        def mark_dispatched() -> None:
            nonlocal dispatched
            with self._condition:
                if (
                    not claimed
                    or dispatched
                    or self._lifecycle is not _Lifecycle.OPEN
                    or self._connection_state is not ConnectionState.CONNECTED
                    or generation != self._connection_generation
                    or self._inflight_handle is not handle
                ):
                    return
                dispatched = True
                now = self._clock.time()
                if handle.ack_support is AckSupport.SUPPORTED:
                    handle._transition(CommandStage.DISPATCHED, now)
                self._last_successful_send_at = now
                self._consecutive_errors = 0

        try:
            with self._io_lock:
                result = backend.dispatch(
                    handle.command,
                    claim_dispatch=claim_dispatch,
                    mark_dispatched=mark_dispatched,
                )
            if not isinstance(result, DispatchResult):
                raise TransportError("backend.dispatch() returned an invalid result")
            if not claim_attempted:
                raise TransportError(
                    "backend returned without claiming the command for dispatch"
                )
            if not claimed:
                with self._condition:
                    if handle.stage is CommandStage.ACCEPTED:
                        raise TransportError(
                            "backend dispatch claim was rejected for an active command"
                        )
                    self._finish_inflight_locked(handle)
                return
            if not dispatched:
                raise TransportError(
                    "backend returned without marking the command dispatched"
                )

            with self._condition:
                if (
                    generation != self._connection_generation
                    or self._lifecycle is not _Lifecycle.OPEN
                ):
                    self._finish_inflight_locked(handle)
                    return
                if handle.ack_support is AckSupport.UNSUPPORTED:
                    handle._transition(CommandStage.DISPATCHED, self._clock.time())
                elif result.acknowledged is True:
                    handle._transition(CommandStage.ACKNOWLEDGED, self._clock.time())
                elif result.acknowledged is False:
                    error = DeviceFaultError("device rejected the command")
                    handle._transition(CommandStage.FAILED, self._clock.time(), error)
                    self._record_error_locked(error)
                else:
                    error = TimeoutError(
                        "backend did not provide the required device ACK"
                    )
                    handle._transition(CommandStage.FAILED, self._clock.time(), error)
                    self._record_error_locked(error)
                    self._mark_disconnected_locked(error)
                self._finish_inflight_locked(handle)
        except BaseException as exc:
            error = normalize_error(exc)
            with self._condition:
                handle._transition(CommandStage.FAILED, self._clock.time(), error)
                self._record_error_locked(error)
                if isinstance(error, (ClosedError, TransportError, TimeoutError)):
                    self._mark_disconnected_locked(error)
                self._finish_inflight_locked(handle)

    def _execute_poll(
        self, sensor: SensorType, generation: int, poll_epoch: int
    ) -> None:
        with self._condition:
            backend = self._backend
            if (
                backend is None
                or generation != self._connection_generation
                or self._connection_state is not ConnectionState.CONNECTED
            ):
                self._finish_poll_locked(poll_epoch)
                return
        try:
            with self._io_lock:
                reading = backend.poll(sensor)
            self._validate_reading(reading, sensor)
            collected_monotonic = self._clock.monotonic()
            collected_wall = self._clock.time()
            with self._condition:
                if (
                    generation == self._connection_generation
                    and self._lifecycle is _Lifecycle.OPEN
                    and self._connection_state is ConnectionState.CONNECTED
                ):
                    published = self._snapshot_cache.publish(
                        reading,
                        generation=generation,
                        collected_monotonic=collected_monotonic,
                        collected_wall=collected_wall,
                    )
                    self._last_successful_send_at = collected_wall
                    if published:
                        self._last_successful_receive_at = collected_wall
                        self._consecutive_errors = 0
                self._finish_poll_locked(poll_epoch)
        except BaseException as exc:
            error = normalize_error(exc)
            with self._condition:
                self._record_error_locked(error)
                if isinstance(error, (ClosedError, TransportError, TimeoutError)):
                    self._mark_disconnected_locked(error)
                self._finish_poll_locked(poll_epoch)


__all__ = ["_WorkerRuntime"]
