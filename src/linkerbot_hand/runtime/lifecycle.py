"""Idempotent session open, close, and polling lifecycle operations."""

from __future__ import annotations

import threading
import time

from ..backend import HandBackend
from ..contracts import CommandStage, ConnectionState, PollingPlan
from ..errors import (
    ClosedError,
    DeviceNotReadyError,
    HandSDKError,
    TimeoutError,
    TransportError,
    UnsupportedOperationError,
    ValidationError,
    normalize_error,
)
from .state import _Lifecycle
from .worker import _WorkerRuntime


class _LifecycleRuntime(_WorkerRuntime):
    """Implement idempotent public lifecycle operations for a hand session."""

    def open(self) -> _LifecycleRuntime:
        """Open and verify the backend once; concurrent calls coalesce.

        Returns:
            This open runtime.

        Raises:
            DeviceNotReadyError: If resources from a prior lifecycle have not
                finished closing.
            HandSDKError: If backend creation, opening, or identity verification
                fails.
        """
        with self._condition:
            while self._lifecycle in {_Lifecycle.OPENING, _Lifecycle.CLOSING}:
                self._condition.wait()
            if self._lifecycle is _Lifecycle.OPEN:
                return self
            if self._open_in_progress:
                raise DeviceNotReadyError("the previous open attempt is still exiting")
            if self._worker is not None and self._worker.is_alive():
                raise DeviceNotReadyError(
                    "the previous session worker is still exiting"
                )
            if self._backend is not None or self._candidate_backend is not None:
                raise DeviceNotReadyError(
                    "the previous backend is not closed; call close() again"
                )
            self._lifecycle = _Lifecycle.OPENING
            self._connection_state = ConnectionState.CONNECTING
            self._open_in_progress = True
            self._condition.notify_all()

        backend: HandBackend | None = None
        worker: threading.Thread | None = None
        try:
            backend = self._backend_factory()
            with self._condition:
                self._candidate_backend = backend
                cancelled = self._lifecycle is not _Lifecycle.OPENING
                self._condition.notify_all()
            if cancelled:
                raise ClosedError("session closed while opening")
            identity = backend.open()
            self._verify_identity(identity, recovery=False)
            stop_event = threading.Event()
            wake_event = threading.Event()
            with self._condition:
                if self._lifecycle is not _Lifecycle.OPENING:
                    raise ClosedError("session closed while opening")
                self._backend = backend
                self._stop_event = stop_event
                self._wake_event = wake_event
                worker = threading.Thread(
                    target=self._worker_loop,
                    args=(stop_event, wake_event),
                    name=f"linkerbot-hand-{self._capabilities.model}",
                    daemon=True,
                )
                self._worker = worker
                # Publish and start atomically with respect to close().
                worker.start()
                self._candidate_backend = None
                self._connection_generation += 1
                self._last_identity = identity
                self._capabilities = self._base_capabilities.with_identity(identity)
                self._snapshot_cache.reset(
                    self._capabilities,
                    generation=self._connection_generation,
                )
                self._connection_state = ConnectionState.CONNECTED
                self._lifecycle = _Lifecycle.OPEN
                self._open_in_progress = False
                self._consecutive_errors = 0
                self._next_reconnect_at = None
                self._reconnect_delay = self._config.reconnect_initial_delay_s
                self._next_command_at = self._clock.monotonic()
                self._condition.notify_all()
            return self
        except BaseException as exc:
            error = normalize_error(exc)
            cleanup_error: HandSDKError | None = None
            if backend is not None:
                try:
                    backend.close()
                except BaseException as close_exc:
                    cleanup_error = normalize_error(close_exc)
                    error = TransportError(
                        "session open failed and the backend could not be closed: "
                        f"{cleanup_error}",
                        cause=cleanup_error,
                    )
            with self._condition:
                cancelled = self._lifecycle is not _Lifecycle.OPENING
                if self._candidate_backend is backend and cleanup_error is None:
                    self._candidate_backend = None
                if self._backend is backend and cleanup_error is None:
                    self._backend = None
                if self._worker is worker:
                    self._worker = None
                self._open_in_progress = False
                if cancelled:
                    error = ClosedError("session closed while opening", cause=error)
                    self._connection_state = ConnectionState.CLOSED
                else:
                    self._lifecycle = _Lifecycle.CLOSED
                    self._connection_state = ConnectionState.ERROR
                    self._record_error_locked(error)
                self._condition.notify_all()
            raise error from exc

    def close(self) -> None:
        """Stop I/O and release the backend idempotently.

        Closing signals polling and command work to stop, interrupts the backend
        as its blocking-I/O cancellation hook, and waits no longer than the
        configured join timeout for the worker.

        Raises:
            TimeoutError: If the worker or backend does not close within the
                configured bounded deadline.
            HandSDKError: If backend resource release fails.
        """
        deadline = time.monotonic() + self._config.join_timeout_s
        remaining = max(0.0, deadline - time.monotonic())
        if not self._close_lock.acquire(timeout=remaining):
            raise TimeoutError("timed out waiting for concurrent close() to finish")
        try:
            self._close_with_deadline(deadline)
        finally:
            self._close_lock.release()

    def _close_with_deadline(self, deadline: float) -> None:
        with self._condition:
            if (
                self._lifecycle is _Lifecycle.CLOSED
                and not self._open_in_progress
                and self._backend is None
                and self._candidate_backend is None
                and (self._worker is None or not self._worker.is_alive())
            ):
                self._worker = None
                self._connection_state = ConnectionState.CLOSED
                return

            self._lifecycle = _Lifecycle.CLOSING
            self._connection_state = ConnectionState.CLOSED
            self._polling = False
            self._poll_epoch += 1
            self._poll_deadlines.clear()
            self._stop_event.set()
            self._wake_event.set()
            close_error = ClosedError("session closed before command dispatch")
            self._scheduler.clear(close_error)
            if self._inflight_handle is not None:
                self._inflight_handle._transition(
                    CommandStage.FAILED, self._clock.time(), close_error
                )
            worker = self._worker
            backend = self._backend
            candidate = self._candidate_backend
            self._condition.notify_all()

        backend_error: HandSDKError | None = None
        attempted_backend_ids: set[int] = set()
        closed_backend_ids: set[int] = set()

        def interrupt(item: HandBackend | None) -> None:
            nonlocal backend_error
            if item is None or id(item) in attempted_backend_ids:
                return
            attempted_backend_ids.add(id(item))
            try:
                item.close()
            except BaseException as exc:
                if backend_error is None:
                    backend_error = normalize_error(exc)
            else:
                closed_backend_ids.add(id(item))

        # Candidate close deliberately cancels an in-progress backend.open().
        interrupt(candidate)
        interrupt(backend)

        with self._condition:
            while self._open_in_progress:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._lifecycle = _Lifecycle.CLOSED
                    self._connection_state = ConnectionState.CLOSED
                    self._condition.notify_all()
                    raise TimeoutError("timed out waiting for open() cancellation")
                self._condition.wait(remaining)
            worker = self._worker
            backend = self._backend
            candidate = self._candidate_backend

        interrupt(candidate)
        interrupt(backend)

        if worker is not None and worker is not threading.current_thread():
            remaining = max(0.0, deadline - time.monotonic())
            worker.join(remaining)

        # Catch a replacement published while recovery was being cancelled.
        with self._condition:
            backend = self._backend
            candidate = self._candidate_backend
        interrupt(candidate)
        interrupt(backend)
        if worker is not None and worker is not threading.current_thread():
            remaining = max(0.0, deadline - time.monotonic())
            worker.join(remaining)
        worker_alive = worker is not None and worker.is_alive()

        with self._condition:
            if self._backend is not None and id(self._backend) in closed_backend_ids:
                self._backend = None
            if (
                self._candidate_backend is not None
                and id(self._candidate_backend) in closed_backend_ids
            ):
                self._candidate_backend = None
            self._inflight_handle = None
            self._poll_inflight_epoch = None
            if self._pending_recovery_token is not None:
                self._recovery_results[self._pending_recovery_token] = ClosedError(
                    "session closed before recovery started"
                )
            self._pending_recovery_token = None
            if self._preparing_recovery_token is not None:
                self._recovery_results[self._preparing_recovery_token] = ClosedError(
                    "session closed before recovery started"
                )
                self._preparing_recovery_token = None
            if self._active_recovery_token is not None:
                self._recovery_results[self._active_recovery_token] = ClosedError(
                    "session closed during recovery"
                )
                self._active_recovery_token = None
            self._snapshot_cache.invalidate()
            self._lifecycle = _Lifecycle.CLOSED
            self._connection_state = ConnectionState.CLOSED
            if not worker_alive:
                self._worker = None
            if backend_error is not None:
                self._record_error_locked(backend_error)
            self._condition.notify_all()

        if worker_alive:
            raise TimeoutError(
                f"session worker did not stop within {self._config.join_timeout_s}s"
            )
        if backend_error is not None:
            raise backend_error

    def start_polling(self, plan: PollingPlan) -> None:
        """Enable SDK-owned polling according to an explicit plan.

        No sensor queries are scheduled before this method is called. A call
        with the active plan is idempotent; a different plan replaces it and
        restarts deadlines without creating another worker.

        Args:
            plan: Per-sensor polling frequencies; zero disables a channel.

        Raises:
            ClosedError: If the session is not open.
            ValidationError: If ``plan`` has the wrong type.
            UnsupportedOperationError: If an enabled sensor is unsupported.
        """
        if not isinstance(plan, PollingPlan):
            raise ValidationError("plan must be a PollingPlan")
        unsupported = tuple(
            sensor
            for sensor in plan.enabled_rates()
            if not self._capabilities.supports_sensor(sensor)
        )
        if unsupported:
            names = ", ".join(sensor.value for sensor in unsupported)
            raise UnsupportedOperationError(f"unsupported polling sensors: {names}")

        with self._condition:
            self._ensure_connected_locked()
            if self._polling and plan == self._polling_plan:
                return
            now = self._clock.monotonic()
            self._polling = True
            self._polling_plan = plan
            self._poll_epoch += 1
            self._poll_deadlines = {sensor: now for sensor in plan.enabled_rates()}
            self._wake_event.set()

    def stop_polling(self) -> None:
        """Disable polling and wait boundedly for a query already in progress.

        The method is idempotent. After it returns, the current polling epoch
        cannot publish another reading or schedule another sensor query.

        Raises:
            TimeoutError: If an already-running query does not finish within the
                configured join timeout.
        """
        deadline = time.monotonic() + self._config.join_timeout_s
        with self._condition:
            self._polling = False
            self._poll_epoch += 1
            stopped_epoch = self._poll_epoch
            self._poll_deadlines.clear()
            self._wake_event.set()
            while (
                self._poll_inflight_epoch is not None
                and self._poll_inflight_epoch < stopped_epoch
            ):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("timed out waiting for polling to stop")
                self._condition.wait(remaining)


__all__ = ["_LifecycleRuntime"]
