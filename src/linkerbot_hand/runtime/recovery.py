"""Explicit and automatic connection recovery on the session I/O worker."""

from __future__ import annotations

import threading
import time

from ..backend import BackendReading, HandBackend
from ..capabilities import specialize_model_capabilities
from ..commands.model import coerce_timeout
from ..contracts import CommandStage, ConnectionState, SensorType
from ..errors import (
    ClosedError,
    DeviceNotReadyError,
    HandSDKError,
    TimeoutError,
    TransportError,
    normalize_error,
)
from .state import _Lifecycle, _SessionState


class _RecoveryRuntime(_SessionState):
    """Coordinate explicit and automatic recovery on the single I/O worker."""

    def recover(self, timeout: float | None = None) -> None:
        """Replace the backend and publish a verified connection generation.

        Recovery clears queued and inflight commands before replacing the
        backend. The new backend identity is verified and current supported
        sensor state is read before publication. Targets from before or during
        the disconnection are never replayed.

        Args:
            timeout: Maximum seconds to wait, or ``None`` without a deadline.

        Raises:
            ValidationError: If ``timeout`` is invalid.
            ClosedError: If the session is closed before recovery completes.
            TimeoutError: If recovery does not finish before the deadline.
            HandSDKError: If reconnect, identity verification, or initial reads
                fail.
        """
        timeout_s = coerce_timeout(timeout)
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        backend_to_interrupt: HandBackend | None = None
        with self._condition:
            if self._lifecycle is not _Lifecycle.OPEN:
                raise ClosedError("cannot recover a closed session")
            if self._active_recovery_token is not None:
                token = self._active_recovery_token
            elif self._pending_recovery_token is not None:
                token = self._pending_recovery_token
            elif self._preparing_recovery_token is not None:
                token = self._preparing_recovery_token
            else:
                token = self._allocate_recovery_token_locked()
                self._preparing_recovery_token = token
                self._connection_state = ConnectionState.RECONNECTING
                error = DeviceNotReadyError(
                    "queued command discarded before connection recovery"
                )
                self._scheduler.clear(error)
                if self._inflight_handle is not None:
                    self._inflight_handle._transition(
                        CommandStage.FAILED, self._clock.time(), error
                    )
                backend_to_interrupt = self._backend
                if backend_to_interrupt is None:
                    self._preparing_recovery_token = None
                    self._pending_recovery_token = token
                    self._wake_event.set()

        if backend_to_interrupt is not None:
            try:
                backend_to_interrupt.close()
            except BaseException:
                # This close is the backend contract's cancellation hook.
                pass
            with self._condition:
                if self._preparing_recovery_token == token:
                    self._preparing_recovery_token = None
                if self._lifecycle is not _Lifecycle.OPEN:
                    self._recovery_results.setdefault(
                        token, ClosedError("session closed before recovery started")
                    )
                    self._condition.notify_all()
                else:
                    self._pending_recovery_token = token
                    self._connection_state = ConnectionState.RECONNECTING
                    self._condition.notify_all()
                    self._wake_event.set()

        with self._condition:
            while token not in self._recovery_results:
                if deadline is None:
                    self._condition.wait()
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("connection recovery did not finish in time")
                    self._condition.wait(remaining)
            error = self._recovery_results[token]
            if error is not None:
                raise error

    def _execute_recovery(self, token: int, stop_event: threading.Event) -> None:
        with self._condition:
            old_backend = self._backend
            sensors = self._snapshot_cache.sensor_types
            prospective_generation = self._connection_generation + 1

        if old_backend is not None:
            try:
                old_backend.close()
            except BaseException as exc:
                error = normalize_error(exc)
                with self._condition:
                    if self._lifecycle is _Lifecycle.OPEN and not stop_event.is_set():
                        self._connection_state = ConnectionState.ERROR
                        self._record_error_locked(error)
                        if self._config.auto_reconnect:
                            self._schedule_reconnect_locked()
                    else:
                        error = ClosedError(
                            "session closed while releasing the old backend",
                            cause=error,
                        )
                    self._finish_recovery_locked(token, error)
                return
            with self._condition:
                if self._backend is old_backend:
                    self._backend = None

        backend: HandBackend | None = None
        staged: list[tuple[BackendReading, float, float]] = []
        optional_errors: list[HandSDKError] = []
        try:
            if stop_event.is_set():
                raise ClosedError("session closed during recovery")
            backend = self._backend_factory()
            with self._condition:
                if self._lifecycle is not _Lifecycle.OPEN or stop_event.is_set():
                    raise ClosedError("session closed during recovery")
                self._candidate_backend = backend
                self._open_in_progress = True
                self._condition.notify_all()
            try:
                identity = backend.open()
            finally:
                with self._condition:
                    self._open_in_progress = False
                    self._condition.notify_all()
            self._verify_identity(identity, recovery=True)
            for sensor in sensors:
                if stop_event.is_set():
                    raise ClosedError("session closed during recovery")
                try:
                    with self._io_lock:
                        reading = backend.poll(sensor)
                    self._validate_reading(reading, sensor)
                    if (
                        sensor is SensorType.ANGLES
                        and not self._snapshot_cache.reading_is_valid(reading)
                    ):
                        raise TransportError(
                            "connection recovery requires a complete, non-empty "
                            "angle reading"
                        )
                    staged.append(
                        (reading, self._clock.monotonic(), self._clock.time())
                    )
                except BaseException as exc:
                    error = normalize_error(exc)
                    if sensor is SensorType.ANGLES or not isinstance(
                        error, TimeoutError
                    ):
                        raise error
                    optional_errors.append(error)
        except BaseException as exc:
            error = normalize_error(exc)
            close_error: HandSDKError | None = None
            if backend is not None:
                try:
                    backend.close()
                except BaseException as close_exc:
                    close_error = normalize_error(close_exc)
                    error = TransportError(
                        "connection recovery failed and the replacement backend "
                        f"could not be closed: {close_error}",
                        cause=close_error,
                    )
            with self._condition:
                if close_error is None:
                    if self._candidate_backend is backend:
                        self._candidate_backend = None
                    if self._backend is backend:
                        self._backend = None
                elif (
                    backend is not None
                    and self._lifecycle is _Lifecycle.OPEN
                    and not stop_event.is_set()
                ):
                    if self._candidate_backend is backend:
                        self._candidate_backend = None
                    self._backend = backend
                elif (
                    backend is not None
                    and self._candidate_backend is not backend
                    and self._backend is not backend
                ):
                    # Preserve ownership so a later close() can retry cleanup.
                    self._candidate_backend = backend
                if self._lifecycle is _Lifecycle.OPEN and not stop_event.is_set():
                    self._connection_state = ConnectionState.ERROR
                    self._record_error_locked(error)
                    if self._config.auto_reconnect:
                        self._schedule_reconnect_locked()
                self._finish_recovery_locked(token, error)
            return

        with self._condition:
            if self._lifecycle is not _Lifecycle.OPEN or stop_event.is_set():
                self._finish_recovery_locked(
                    token, ClosedError("session closed during recovery")
                )
                should_close = True
            else:
                if self._candidate_backend is backend:
                    self._candidate_backend = None
                self._backend = backend
                self._connection_generation = prospective_generation
                self._last_identity = identity
                self._capabilities = specialize_model_capabilities(
                    self._base_capabilities,
                    identity,
                )
                self._snapshot_cache.reset(
                    self._capabilities,
                    generation=prospective_generation,
                )
                for reading, collected_monotonic, collected_wall in staged:
                    published = self._snapshot_cache.publish(
                        reading,
                        generation=prospective_generation,
                        collected_monotonic=collected_monotonic,
                        collected_wall=collected_wall,
                    )
                    self._last_successful_send_at = collected_wall
                    if published:
                        self._last_successful_receive_at = collected_wall
                self._connection_state = ConnectionState.CONNECTED
                self._consecutive_errors = 0
                if optional_errors:
                    self._recent_error = optional_errors[-1]
                self._reconnect_count += 1
                self._next_reconnect_at = None
                self._reconnect_delay = self._config.reconnect_initial_delay_s
                self._next_command_at = self._clock.monotonic()
                if self._polling:
                    now = self._clock.monotonic()
                    self._poll_deadlines = {
                        sensor: now for sensor in self._polling_plan.enabled_rates()
                    }
                self._finish_recovery_locked(token, None)
                should_close = False
        if should_close and backend is not None:
            try:
                backend.close()
            except BaseException as exc:
                error = normalize_error(exc)
                with self._condition:
                    if (
                        self._candidate_backend is not backend
                        and self._backend is not backend
                    ):
                        self._candidate_backend = backend
                    self._record_error_locked(error)
                    self._condition.notify_all()
            else:
                with self._condition:
                    if self._candidate_backend is backend:
                        self._candidate_backend = None
                    if self._backend is backend:
                        self._backend = None
                    self._condition.notify_all()


__all__ = ["_RecoveryRuntime"]
