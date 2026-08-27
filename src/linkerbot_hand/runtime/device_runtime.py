"""Concrete actor-style runtime behind the single public ``HandDevice``."""

from __future__ import annotations

import threading
from typing import cast

from ..api.commands import AckSupport, CommandHandle
from ..api.config import DeviceConfig, PollingPlan
from ..api.device_info import DeviceInfo
from ..api.enums import ReadMode
from ..api.errors import (
    ClosedError,
    DeviceNotReadyError,
    LifecycleError,
    TimeoutError,
    UnsupportedOperationError,
    ValidationError,
)
from ..api.health import HealthStatus
from ..api.snapshot import HandSnapshot
from ..capabilities.types import (
    CommandSemantic,
    DeviceCapabilities,
    TelemetrySemantic,
)
from ..protocols.base import ProtocolBackend
from ..protocols.registry import create_protocol_backend
from ._clock import resolve_clock
from ._device_actor import DeviceActor
from ._errors import as_hand_error
from ._operation_ticket import OperationTicket
from ._runtime_state import RuntimeState
from ._transport_binding import TransportBinding
from .command_scheduler import CommandIngress
from .metrics import RuntimeMetrics
from .poll_scheduler import PollScheduler
from .snapshot_store import SnapshotStore
from .transactions import DEVICE_INFO_KEY, TransactionCoordinator


class DeviceRuntime:
    """Own one device lifecycle and expose only bounded/cache-safe operations."""

    def __init__(
        self,
        *,
        transport: object,
        capabilities: DeviceCapabilities,
        config: DeviceConfig,
        initial_snapshot: HandSnapshot,
        initial_health: HealthStatus,
        clock: object | None,
        protocol_backend: object | None,
    ) -> None:
        if not isinstance(capabilities, DeviceCapabilities):
            raise ValidationError("capabilities must be DeviceCapabilities")
        if not isinstance(config, DeviceConfig):
            raise ValidationError("config must be DeviceConfig")
        self._clock = resolve_clock(clock)
        self._backend = _resolve_backend(protocol_backend, capabilities)
        self._config = config
        self._state = RuntimeState(capabilities)
        self._wake = threading.Event()
        self._commands = CommandIngress(
            discrete_capacity=config.discrete_queue_capacity,
            monotonic=self._clock.monotonic,
            wake=self._wake.set,
        )
        self._transactions = TransactionCoordinator(
            waiter_capacity=config.mailbox_capacity,
            clock=self._clock,
            wake=self._wake.set,
        )
        self._polling = PollScheduler(capabilities)
        self._snapshot_store = SnapshotStore(capabilities, initial_snapshot)
        self._metrics = RuntimeMetrics(initial_health)
        self._binding = TransportBinding(
            transport,
            endpoint_id=self._backend.endpoint_id,
            fifo_capacity=config.mailbox_capacity,
            join_timeout_s=config.shutdown_timeout_s,
            clock=self._clock,
        )
        self._actor = DeviceActor(
            config=config,
            backend=self._backend,
            binding=self._binding,
            state=self._state,
            commands=self._commands,
            transactions=self._transactions,
            polling=self._polling,
            snapshot_store=self._snapshot_store,
            metrics=self._metrics,
            clock=self._clock,
            wake=self._wake,
        )
        self._operation_lock = threading.RLock()
        self._opened = False
        self._close_incomplete = False

    def open(self) -> None:
        """Open transport, verify identity, and read baseline exactly once."""

        with self._operation_lock:
            if self._opened:
                if self._state.ready():
                    return
                raise DeviceNotReadyError(
                    "device runtime is open but faulted; call recover() or close()",
                    operation="open",
                    model=self._state.declared.model,
                    generation=self._state.generation(),
                )
            if self._close_incomplete:
                raise LifecycleError(
                    "retry close before reopening the device runtime",
                    operation="open",
                    model=self._state.declared.model,
                )
            self._metrics.opening()
            try:
                self._binding.open()
                self._opened = True
                ticket = self._actor.start()
                self._wait_ticket(ticket, self._open_timeout_s(), "open")
            except Exception as error:
                self._cleanup_failed_open()
                classified = as_hand_error(
                    error,
                    operation="open",
                    model=self._state.declared.model,
                    generation=self._state.generation(),
                )
                raise classified from error

    def close(self) -> None:
        """Stop admission, invalidate cache, and boundedly release all workers."""

        with self._operation_lock:
            if not self._opened and not self._close_incomplete:
                return
            generation = self._state.generation()
            closed = ClosedError(
                "device runtime is closing",
                operation="close",
                model=self._state.declared.model,
                generation=generation,
            )
            self._metrics.closing()
            self._state.mark_not_ready()
            self._commands.reject_new(closed)
            self._commands.fail_pending(closed)
            self._transactions.reject_new(closed)
            self._transactions.fail_all(closed)
            self._polling.stop()
            error: BaseException | None = None
            try:
                self._binding.stop_admission_and_drain()
            except Exception as drain_error:  # noqa: BLE001 - shutdown boundary
                error = drain_error
            try:
                self._actor.stop(timeout_s=self._config.shutdown_timeout_s)
            except Exception as actor_error:  # noqa: BLE001 - shutdown boundary
                if error is None:
                    error = actor_error
            if generation > 0:
                self._snapshot_store.invalidate(
                    generation=generation,
                    captured_at=self._clock.time(),
                )
            try:
                self._binding.close()
            except Exception as binding_error:  # noqa: BLE001 - shutdown boundary
                if error is None:
                    error = binding_error
            if error is not None:
                self._close_incomplete = True
                classified = LifecycleError(
                    "device runtime did not close cleanly",
                    operation="close",
                    model=self._state.declared.model,
                    generation=generation,
                    cause=error,
                )
                self._metrics.faulted(classified)
                raise classified from error
            self._opened = False
            self._close_incomplete = False
            self._metrics.closed()

    def start_polling(self, plan: PollingPlan) -> None:
        """Install or atomically replace the sole periodic query plan."""

        with self._operation_lock:
            operation = "start_polling"
            if not self._opened:
                raise ClosedError(
                    "start_polling requires an open device",
                    operation=operation,
                    model=self._state.declared.model,
                )
            error = DeviceNotReadyError(
                "start_polling requires a verified connection",
                operation=operation,
                model=self._state.declared.model,
                generation=self._state.generation(),
            )
            changed = self._state.run_if_admitting(
                lambda: self._polling.replace(plan, now=self._clock.monotonic()),
                error=error,
            )
            if changed:
                self._wake.set()

    def stop_polling(self) -> None:
        """Cancel future polls and boundedly drain any poll already in flight."""

        with self._operation_lock:
            changed = self._polling.stop()
            canceled = self._transactions.cancel_polls()
            if changed or canceled:
                self._wake.set()
            if not self._polling.wait_idle(
                clock=self._clock,
                timeout_s=self._config.shutdown_timeout_s,
            ):
                raise TimeoutError(
                    "polling transaction did not stop before shutdown deadline",
                    operation="stop_polling",
                    model=self._state.declared.model,
                    generation=self._state.generation(),
                )

    def submit(
        self,
        semantic: CommandSemantic,
        values: tuple[int | float, ...],
    ) -> CommandHandle:
        """Accept a validated command into a latest slot or bounded FIFO."""

        self._require_ready(f"set_{semantic.value}")
        capabilities = self._state.capabilities()
        command = capabilities.command(semantic)
        if command is None:
            raise UnsupportedOperationError(
                f"{capabilities.model} does not support {semantic.value}",
                operation=f"set_{semantic.value}",
                model=capabilities.model,
            )
        return self._commands.submit(
            semantic,
            values,
            ack_support=command.ack_support,
        )

    def read(self, semantic: TelemetrySemantic, timeout: float) -> object:
        """Return a post-call fresh sample, coalescing an equivalent transaction."""

        self._require_ready(f"get_{semantic.value}")
        telemetry = self._state.capabilities().telemetry_channel(semantic)
        if telemetry is None:
            raise UnsupportedOperationError(
                f"{semantic.value} telemetry is unsupported"
            )
        if not telemetry.device_queried:
            sample = self._snapshot_store.sample(
                semantic,
                captured_at=self._clock.time(),
                monotonic_now=self._clock.monotonic(),
            )
            if (
                telemetry.read_mode is ReadMode.COMMAND_CACHE
                and sample is not None
                and sample.meta.valid  # ty: ignore[unresolved-attribute]
            ):
                return sample
            raise UnsupportedOperationError(
                f"{semantic.value} has no active query transaction",
                operation=f"get_{semantic.value}",
                model=self._state.declared.model,
            )
        start_sequence = self._snapshot_store.sequence(semantic)
        waiter = self._transactions.request(
            semantic,
            start_sequence=start_sequence,
            deadline_s=self._clock.monotonic() + timeout,
        )
        return waiter.wait()

    def read_device_info(self, timeout: float) -> DeviceInfo:
        """Actively refresh identity and reject a replacement device."""

        self._require_ready("get_device_info")
        waiter = self._transactions.request(
            DEVICE_INFO_KEY,
            start_sequence=self._snapshot_store.device_info_sequence,
            deadline_s=self._clock.monotonic() + timeout,
        )
        result = waiter.wait()
        if not isinstance(result, DeviceInfo):
            raise LifecycleError("identity transaction returned an invalid type")
        return result

    def operate(self, operation: str) -> CommandHandle:
        """Accept one capability-checked discrete operation."""

        self._require_ready(operation)
        capabilities = self._state.capabilities()
        if not getattr(capabilities.operations, operation, False):
            raise UnsupportedOperationError(
                f"{capabilities.model} does not support {operation}",
                operation=operation,
                model=capabilities.model,
            )
        ack_support = (
            AckSupport.SUPPORTED
            if operation in capabilities.operations.acknowledged_operations
            else AckSupport.UNSUPPORTED
        )
        return self._commands.submit_operation(
            operation,
            ack_support=ack_support,
        )

    def recover(self, timeout: float) -> None:
        """Recover safely without retaining or replaying prior commands."""

        with self._operation_lock:
            if not self._opened or not self._actor.alive:
                raise ClosedError(
                    "recover requires an open device",
                    operation="recover",
                    model=self._state.declared.model,
                )
            ticket = self._actor.request_recovery()
        self._wait_ticket(ticket, timeout, "recover")

    def get_capabilities(self) -> DeviceCapabilities:
        """Return declared or verified capabilities without I/O."""

        return self._state.capabilities()

    def get_snapshot(self) -> HandSnapshot:
        """Return immutable cached state without transport or protocol access."""

        return self._snapshot_store.view(
            captured_at=self._clock.time(),
            monotonic_now=self._clock.monotonic(),
        )

    def get_health(self) -> HealthStatus:
        """Return cached runtime/transport metrics without device traffic."""

        transport_health = None
        arbiter_health = None
        if self._opened:
            try:
                transport_health = self._binding.get_health()
            except Exception:  # noqa: BLE001 - concurrent close has no health I/O
                transport_health = None
            try:
                arbiter_health = self._binding.get_arbiter_health()
            except Exception:  # noqa: BLE001 - recovery must not block health
                arbiter_health = None
        return self._metrics.health(
            now=self._clock.monotonic(),
            captured_at=self._clock.time(),
            capabilities=self._state.capabilities(),
            commands=self._commands,
            polling=self._polling,
            snapshot_store=self._snapshot_store,
            transport=transport_health,
            arbiter=arbiter_health,
        )

    def _require_ready(self, operation: str) -> None:
        if not self._opened:
            raise ClosedError(
                f"{operation} requires an open device",
                operation=operation,
                model=self._state.declared.model,
            )
        if not self._state.ready():
            raise DeviceNotReadyError(
                f"{operation} requires a verified connection",
                operation=operation,
                model=self._state.declared.model,
                generation=self._state.generation(),
            )

    def _wait_ticket(
        self, ticket: OperationTicket, timeout_s: float, operation: str
    ) -> None:
        deadline = self._clock.monotonic() + timeout_s
        while not ticket.event.is_set():
            remaining = deadline - self._clock.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"{operation} did not complete before timeout",
                    operation=operation,
                    model=self._state.declared.model,
                    generation=self._state.generation(),
                )
            self._clock.wait(ticket.event, remaining)
        if ticket.error is not None:
            raise ticket.error

    def _open_timeout_s(self) -> float:
        return (
            self._config.identity_timeout_s
            + (len(self._state.declared.telemetry) + 1)
            * self._config.transaction_timeout_s
            + self._config.shutdown_timeout_s
        )

    def _cleanup_failed_open(self) -> None:
        try:
            self._actor.stop(timeout_s=self._config.shutdown_timeout_s)
        finally:
            self._binding.close()
            self._opened = False


def _resolve_backend(
    backend: object | None,
    capabilities: DeviceCapabilities,
) -> ProtocolBackend:
    if backend is None:
        backend = create_protocol_backend(capabilities)
    for name in (
        "identity_transaction",
        "baseline_semantics",
        "command_transaction",
        "read_transaction",
        "operation_transaction",
        "decode_report",
    ):
        if not callable(getattr(backend, name, None)):
            raise LifecycleError(f"protocol backend must provide callable {name}()")
    candidate = cast(ProtocolBackend, backend)
    try:
        hash(candidate.endpoint_id)
    except (AttributeError, TypeError) as error:
        raise LifecycleError("protocol backend endpoint_id must be hashable") from error
    try:
        route_keys = candidate.route_keys
    except AttributeError as error:
        raise LifecycleError("protocol backend must provide route_keys") from error
    if not isinstance(route_keys, tuple) or not route_keys:
        raise LifecycleError("protocol backend route_keys must be a non-empty tuple")
    try:
        unique_route_keys = set(route_keys)
    except TypeError as error:
        raise LifecycleError("protocol backend route_keys must be hashable") from error
    if len(unique_route_keys) != len(route_keys):
        raise LifecycleError("protocol backend route_keys must be unique")
    return candidate


__all__ = ["DeviceRuntime"]
