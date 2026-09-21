"""Shared transport lease and one batch TX arbiter per physical channel."""

from __future__ import annotations

import threading
from collections.abc import Callable, Hashable
from typing import cast

from linkerbot_transport import (
    SendReceipt,
    TransportHealth,
    TransportLease,
)
from linkerbot_transport.errors import DeviceNotReadyError

from ..api.errors import LifecycleError, ValidationError
from ._clock import RuntimeClock
from ._transport_arbiter import (
    ARBITERS,
    TRANSPORT_POOL,
    MessageBatch,
    MessageTransport,
)


class TransportBinding:
    """No-I/O descriptor until ``open()``, then one shared physical lease."""

    def __init__(
        self,
        transport: object,
        *,
        endpoint_id: Hashable,
        fifo_capacity: int,
        join_timeout_s: float,
        clock: RuntimeClock,
    ) -> None:
        self._descriptor = _validate_transport(transport)
        if endpoint_id is None:
            raise ValidationError("endpoint_id must not be None")
        try:
            hash(endpoint_id)
        except TypeError as error:
            raise ValidationError("endpoint_id must be hashable") from error
        self._endpoint_id = endpoint_id
        pool_key = getattr(transport, "pool_key", None)
        self._key = (
            type(transport),
            ("instance", id(transport)) if pool_key is None else pool_key,
        )
        self._fifo_capacity = fifo_capacity
        self._join_timeout_s = join_timeout_s
        self._clock = clock
        self._interval_event = threading.Event()
        self._lease: TransportLease[MessageTransport] | None = None
        self._arbiter_acquired = False
        self._accepting = False
        self._transport_generation: int | None = None
        self._lock = threading.RLock()

    def open(self) -> None:
        """Acquire one physical connection and its shared TX arbiter."""

        with self._lock:
            if self._lease is not None:
                return
            lease = TRANSPORT_POOL.acquire(self._key, lambda: self._descriptor)
            transport = lease.transport
            try:
                generation = ARBITERS.acquire(
                    self._key,
                    transport,
                    endpoint_id=self._endpoint_id,
                    fifo_capacity=self._fifo_capacity,
                    join_timeout_s=self._join_timeout_s,
                    monotonic=self._clock.monotonic,
                    wait_interval=self._wait_interval,
                )
            except Exception:
                lease.close()
                raise
            self._lease = lease
            self._arbiter_acquired = True
            self._accepting = True
            self._transport_generation = generation

    def stop_admission_and_drain(self, *, timeout_s: float | None = None) -> None:
        """Reject new endpoint batches and boundedly drain any claimed send."""

        with self._lock:
            lease = self._lease
            if lease is None:
                return
            self._accepting = False
            if self._arbiter_acquired:
                ARBITERS.cancel_endpoint(
                    self._key,
                    lease.transport,
                    self._endpoint_id,
                    timeout_s=(
                        self._join_timeout_s
                        if timeout_s is None
                        else min(self._join_timeout_s, timeout_s)
                    ),
                )

    def close(self) -> None:
        """Release arbiter first and physical transport last."""

        with self._lock:
            lease = self._lease
            if lease is None:
                return
            self._accepting = False
            transport = lease.transport
            if self._arbiter_acquired:
                ARBITERS.cancel_endpoint(
                    self._key,
                    transport,
                    self._endpoint_id,
                    timeout_s=self._join_timeout_s,
                )
                ARBITERS.release(self._key, transport, self._endpoint_id)
                self._arbiter_acquired = False
            lease.close()
            self._lease = None
            self._transport_generation = None

    def recover(self) -> None:
        """Replace the transport generation and discard all pending batches."""

        with self._lock:
            transport = self._require_transport_locked()
            self._accepting = False
            generation = self._transport_generation
            if generation is None:
                raise DeviceNotReadyError("transport generation is unavailable")
            recovered_generation = ARBITERS.recover(
                self._key,
                transport,
                observed_generation=generation,
            )
            self._transport_generation = recovered_generation
            self._accepting = True

    def subscribe_routes(
        self,
        route_keys: tuple[Hashable, ...],
        *,
        queue_capacity: int,
        on_available: Callable[[], None] | None = None,
    ):
        """Register one generation-bound inbox for exact receive routes."""

        with self._lock:
            return self._require_transport_locked().subscribe_routes(
                route_keys,
                queue_capacity=queue_capacity,
                on_available=on_available,
            )

    def submit(
        self,
        messages: tuple[object, ...],
        *,
        request_interval_s: float,
        endpoint_id: Hashable,
        latest_family: Hashable | None,
        priority: int,
        deadline_at_s: float | None,
    ) -> SendReceipt:
        """Submit one atomic protocol request batch to the shared writer."""

        if not isinstance(messages, tuple) or not messages:
            raise ValidationError("messages must be a non-empty tuple")
        if endpoint_id != self._endpoint_id:
            raise ValidationError("submitted endpoint_id does not match binding")
        batch = MessageBatch(messages, request_interval_s)
        with self._lock:
            generation = self._require_submission_locked()
            if latest_family is not None:
                receipt = ARBITERS.submit_latest(
                    self._key,
                    batch,
                    endpoint_id=endpoint_id,
                    family=latest_family,
                    expected_generation=generation,
                    priority=priority,
                    deadline_at_s=deadline_at_s,
                )
            else:
                receipt = ARBITERS.submit_fifo(
                    self._key,
                    batch,
                    endpoint_id=endpoint_id,
                    expected_generation=generation,
                    priority=priority,
                    deadline_at_s=deadline_at_s,
                )
        ARBITERS.drain(
            self._key,
            endpoint_id=endpoint_id,
            expected_generation=generation,
        )
        return receipt

    def replace_latest_if_pending(
        self,
        messages: tuple[object, ...],
        *,
        request_interval_s: float,
        endpoint_id: Hashable,
        latest_family: Hashable,
        priority: int,
        deadline_at_s: float | None,
    ) -> SendReceipt | None:
        """Replace an unclaimed batch without queueing behind an in-flight one."""

        if not isinstance(messages, tuple) or not messages:
            raise ValidationError("messages must be a non-empty tuple")
        if endpoint_id != self._endpoint_id:
            raise ValidationError("submitted endpoint_id does not match binding")
        batch = MessageBatch(messages, request_interval_s)
        with self._lock:
            generation = self._require_submission_locked()
            receipt = ARBITERS.replace_latest_if_pending(
                self._key,
                batch,
                endpoint_id=endpoint_id,
                family=latest_family,
                expected_generation=generation,
                priority=priority,
                deadline_at_s=deadline_at_s,
            )
        if receipt is not None:
            ARBITERS.drain(
                self._key,
                endpoint_id=endpoint_id,
                expected_generation=generation,
            )
        return receipt

    def get_health(self) -> TransportHealth:
        """Return the transport's cached zero-I/O health snapshot."""

        lease = self._lease
        if lease is None:
            raise DeviceNotReadyError("transport binding is closed")
        return lease.transport.get_health()

    def get_arbiter_health(self):
        """Return shared TX queue counters without touching the device."""

        lease = self._lease
        if lease is None:
            raise DeviceNotReadyError("transport binding is closed")
        return ARBITERS.health(self._key)

    def _wait_interval(self, timeout_s: float) -> None:
        self._clock.wait(self._interval_event, timeout_s)

    def _require_transport_locked(self) -> MessageTransport:
        if self._lease is None:
            raise DeviceNotReadyError("transport binding is closed")
        return self._lease.transport

    def _require_submission_locked(self) -> int:
        self._require_transport_locked()
        if not self._accepting:
            raise DeviceNotReadyError("device endpoint is not accepting sends")
        generation = self._transport_generation
        if generation is None:
            self._accepting = False
            raise DeviceNotReadyError("device endpoint transport generation ended")
        return generation


def _validate_transport(transport: object) -> MessageTransport:
    if transport is None:
        raise ValidationError("transport must not be None")
    for name in (
        "open",
        "close",
        "recover",
        "send",
        "subscribe_routes",
        "get_health",
    ):
        if not callable(getattr(transport, name, None)):
            raise LifecycleError(f"transport must provide callable {name}()")
    return cast(MessageTransport, transport)


__all__ = ["TransportBinding"]
