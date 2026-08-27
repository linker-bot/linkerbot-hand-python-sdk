"""Shared physical-transport pool and atomic protocol batch arbitration."""

from __future__ import annotations

import threading
from collections.abc import Callable, Hashable
from dataclasses import dataclass, field
from typing import Protocol

from linkerbot_transport import (
    SendOutcome,
    SendReceipt,
    SharedTransportPool,
    TransmitArbiter,
    TransmitHealth,
    TransportHealth,
)
from linkerbot_transport.errors import (
    ClosedError,
    DeviceNotReadyError,
    TransportError,
)


class MessageTransport(Protocol):
    """Transport surface required by the Hand runtime."""

    def open(self) -> None: ...

    def close(self) -> None: ...

    def recover(self) -> None: ...

    def send(self, message: object) -> SendReceipt: ...

    def subscribe_route(
        self, route_key: Hashable, *, queue_capacity: int | None = None
    ) -> object: ...

    def subscribe_routes(
        self,
        route_keys: tuple[Hashable, ...],
        *,
        queue_capacity: int | None = None,
    ) -> object: ...

    def get_health(self) -> TransportHealth: ...


@dataclass(frozen=True, slots=True)
class MessageBatch:
    """Frames that must be dispatched as one uninterrupted protocol request."""

    messages: tuple[object, ...]
    request_interval_s: float


class BatchDispatchError(TransportError):
    """Transport failure retaining the count sent before a batch failed."""

    def __init__(self, message: str, *, sent_frames: int, cause: BaseException) -> None:
        super().__init__(message, cause=cause)
        self.sent_frames = sent_frames


@dataclass(slots=True)
class _ArbiterEntry:
    transport: MessageTransport
    arbiter: TransmitArbiter[MessageBatch]
    endpoints: set[Hashable]
    monotonic: Callable[[], float]
    wait_interval: Callable[[float], None]
    fifo_capacity: int
    join_timeout_s: float
    recovery_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    recovering: bool = False


class SharedArbiterRegistry:
    """Reference-count one TX arbiter for each physical transport key."""

    def __init__(self) -> None:
        self._entries: dict[Hashable, _ArbiterEntry] = {}
        self._lock = threading.RLock()

    def acquire(
        self,
        key: Hashable,
        transport: MessageTransport,
        *,
        endpoint_id: Hashable,
        fifo_capacity: int,
        join_timeout_s: float,
        monotonic: Callable[[], float],
        wait_interval: Callable[[float], None],
    ) -> int:
        """Acquire or create the physical channel's sole writer."""

        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                arbiter = self._make_arbiter(
                    transport,
                    fifo_capacity=fifo_capacity,
                    join_timeout_s=join_timeout_s,
                    monotonic=monotonic,
                    wait_interval=wait_interval,
                )
                arbiter.open(generation=transport.get_health().generation)
                entry = _ArbiterEntry(
                    transport=transport,
                    arbiter=arbiter,
                    endpoints=set(),
                    monotonic=monotonic,
                    wait_interval=wait_interval,
                    fifo_capacity=fifo_capacity,
                    join_timeout_s=join_timeout_s,
                )
                self._entries[key] = entry
            elif entry.transport is not transport:
                raise DeviceNotReadyError(
                    "transport pool and TX arbiter resolved different connections"
                )
            elif (
                entry.fifo_capacity != fifo_capacity
                or entry.join_timeout_s != join_timeout_s
            ):
                raise DeviceNotReadyError(
                    "shared TX arbiter configuration does not match the active channel"
                )
            elif entry.recovering:
                raise DeviceNotReadyError("shared TX arbiter is recovering")
            if endpoint_id in entry.endpoints:
                raise DeviceNotReadyError(
                    "device endpoint already has an active runtime"
                )
            entry.endpoints.add(endpoint_id)
            return entry.arbiter.get_health().generation

    def release(
        self,
        key: Hashable,
        transport: MessageTransport,
        endpoint_id: Hashable,
    ) -> None:
        """Release one writer lease, closing only after the last endpoint."""

        with self._lock:
            entry = self._entries.get(key)
            if (
                entry is None
                or entry.transport is not transport
                or endpoint_id not in entry.endpoints
            ):
                raise DeviceNotReadyError("TX arbiter lease is not active")
            if len(entry.endpoints) > 1:
                entry.endpoints.remove(endpoint_id)
                return
            entry.arbiter.close()
            del self._entries[key]

    def cancel_endpoint(
        self,
        key: Hashable,
        transport: MessageTransport,
        endpoint_id: Hashable,
        *,
        timeout_s: float,
    ) -> int:
        """Cancel one endpoint without holding the registry during its drain."""

        with self._lock:
            entry = self._require_entry(key)
            if entry.transport is not transport:
                raise DeviceNotReadyError("cannot cancel a different transport")
            if endpoint_id not in entry.endpoints:
                raise DeviceNotReadyError("device endpoint lease is not active")
            arbiter = entry.arbiter
        return arbiter.cancel_endpoint(
            endpoint_id,
            ClosedError("device endpoint closed before dispatch"),
            timeout_s=timeout_s,
        )

    def submit_latest(
        self,
        key: Hashable,
        batch: MessageBatch,
        *,
        endpoint_id: Hashable,
        family: Hashable,
        expected_generation: int,
        priority: int,
        deadline_at_s: float | None,
    ) -> SendReceipt:
        """Submit one replaceable batch through the shared writer."""

        with self._lock:
            entry = self._require_entry(key)
            if endpoint_id not in entry.endpoints:
                raise DeviceNotReadyError("device endpoint lease is not active")
            self._require_generation(entry, expected_generation)
            return entry.arbiter.submit_latest(
                batch,
                endpoint_id=endpoint_id,
                command_family=family,
                priority=priority,
                deadline_at_s=deadline_at_s,
            )

    def replace_latest_if_pending(
        self,
        key: Hashable,
        batch: MessageBatch,
        *,
        endpoint_id: Hashable,
        family: Hashable,
        expected_generation: int,
        priority: int,
        deadline_at_s: float | None,
    ) -> SendReceipt | None:
        """Replace a keyed batch only while its prior job remains unclaimed."""

        with self._lock:
            entry = self._require_entry(key)
            if endpoint_id not in entry.endpoints:
                raise DeviceNotReadyError("device endpoint lease is not active")
            self._require_generation(entry, expected_generation)
            return entry.arbiter.replace_latest_if_pending(
                batch,
                endpoint_id=endpoint_id,
                command_family=family,
                priority=priority,
                deadline_at_s=deadline_at_s,
            )

    def submit_fifo(
        self,
        key: Hashable,
        batch: MessageBatch,
        *,
        endpoint_id: Hashable,
        expected_generation: int,
        priority: int,
        deadline_at_s: float | None,
    ) -> SendReceipt:
        """Submit one non-replaceable batch through the shared writer."""

        with self._lock:
            entry = self._require_entry(key)
            if endpoint_id not in entry.endpoints:
                raise DeviceNotReadyError("device endpoint lease is not active")
            self._require_generation(entry, expected_generation)
            return entry.arbiter.submit_fifo(
                batch,
                endpoint_id=endpoint_id,
                priority=priority,
                deadline_at_s=deadline_at_s,
            )

    def recover(
        self,
        key: Hashable,
        transport: MessageTransport,
        *,
        observed_generation: int,
    ) -> int:
        """Recover once per observed physical generation and return the active one."""

        with self._lock:
            entry = self._require_entry(key)
            if entry.transport is not transport:
                raise DeviceNotReadyError("cannot recover a different transport")
            recovery_lock = entry.recovery_lock

        # Recovery may wait for a claimed send and reopen a system channel. Keep
        # that latency local to this physical transport instead of blocking every
        # registry entry.
        with recovery_lock:
            with self._lock:
                current = self._require_entry(key)
                if current is not entry or entry.transport is not transport:
                    raise DeviceNotReadyError("shared TX arbiter generation changed")
                transport_health = transport.get_health()
                arbiter_health = entry.arbiter.get_health()
                if (
                    transport_health.generation != observed_generation
                    and transport_health.connected
                    and arbiter_health.running
                    and arbiter_health.generation == transport_health.generation
                ):
                    return transport_health.generation
                entry.recovering = True
                old_arbiter = entry.arbiter
                fifo_capacity = entry.fifo_capacity
                join_timeout_s = entry.join_timeout_s
                monotonic = entry.monotonic
                wait_interval = entry.wait_interval

            try:
                # ``abort()`` is intentionally repeated after a prior drain timeout.
                # An arbiter can be non-accepting while its claimed dispatch thread is
                # still alive; replacing it at that point would create two TX owners.
                old_arbiter.abort(TransportError("transport generation ended"))
                if (
                    transport_health.generation == observed_generation
                    or not transport_health.connected
                ):
                    transport.recover()
                    transport_health = transport.get_health()
                if not transport_health.connected:
                    raise DeviceNotReadyError(
                        "transport recovery did not open the channel"
                    )

                arbiter = self._make_arbiter(
                    transport,
                    fifo_capacity=fifo_capacity,
                    join_timeout_s=join_timeout_s,
                    monotonic=monotonic,
                    wait_interval=wait_interval,
                )
                current_generation = transport_health.generation
                arbiter.open(generation=current_generation)
            except BaseException:
                with self._lock:
                    if self._entries.get(key) is entry:
                        entry.recovering = False
                raise

            with self._lock:
                current = self._require_entry(key)
                if current is not entry:
                    arbiter.close()
                    raise DeviceNotReadyError("shared TX arbiter generation changed")
                entry.arbiter = arbiter
                entry.recovering = False
            return current_generation

    def health(self, key: Hashable) -> TransmitHealth:
        """Return the shared writer's cache-only health."""

        if not self._lock.acquire(blocking=False):
            raise DeviceNotReadyError("shared TX arbiter is changing generation")
        try:
            entry = self._require_entry(key)
            if entry.recovering:
                raise DeviceNotReadyError("shared TX arbiter is recovering")
            return entry.arbiter.get_health()
        finally:
            self._lock.release()

    def _require_entry(self, key: Hashable) -> _ArbiterEntry:
        entry = self._entries.get(key)
        if entry is None:
            raise DeviceNotReadyError("shared TX arbiter is closed")
        return entry

    @staticmethod
    def _require_generation(entry: _ArbiterEntry, expected_generation: int) -> None:
        if entry.recovering:
            raise DeviceNotReadyError("shared TX arbiter is recovering")
        transport_health = entry.transport.get_health()
        arbiter_health = entry.arbiter.get_health()
        if (
            not transport_health.connected
            or not arbiter_health.running
            or transport_health.generation != expected_generation
            or arbiter_health.generation != expected_generation
        ):
            raise DeviceNotReadyError("transport is not ready for this generation")

    @staticmethod
    def _make_arbiter(
        transport: MessageTransport,
        *,
        fifo_capacity: int,
        join_timeout_s: float,
        monotonic: Callable[[], float],
        wait_interval: Callable[[float], None],
    ) -> TransmitArbiter[MessageBatch]:
        def dispatch(batch: MessageBatch) -> SendReceipt:
            sent_frames = 0
            try:
                for index, message in enumerate(batch.messages):
                    result = transport.send(message).result()
                    if result.outcome is not SendOutcome.DISPATCHED:
                        raise TransportError(
                            f"downstream batch frame ended as {result.outcome.value}"
                        )
                    sent_frames += 1
                    if batch.request_interval_s > 0 and index + 1 < len(batch.messages):
                        wait_interval(batch.request_interval_s)
            except Exception as error:
                raise BatchDispatchError(
                    "message batch did not dispatch completely",
                    sent_frames=sent_frames,
                    cause=error,
                ) from error
            return SendReceipt.completed(
                generation=transport.get_health().generation,
                monotonic=monotonic,
            )

        return TransmitArbiter(
            dispatch,
            fifo_capacity=fifo_capacity,
            join_timeout_s=join_timeout_s,
            monotonic=monotonic,
        )


TRANSPORT_POOL = SharedTransportPool[MessageTransport]()
ARBITERS = SharedArbiterRegistry()


__all__ = [
    "ARBITERS",
    "TRANSPORT_POOL",
    "BatchDispatchError",
    "MessageBatch",
    "MessageTransport",
]
