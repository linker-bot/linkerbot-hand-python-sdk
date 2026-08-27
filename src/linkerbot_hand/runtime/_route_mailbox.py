"""Bounded receive-route mailbox isolated from the device actor loop."""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Hashable
from dataclasses import dataclass
from typing import Protocol, cast

from linkerbot_transport.errors import ClosedError as TransportClosedError
from linkerbot_transport.errors import TimeoutError as TransportTimeoutError

from ..api.errors import ProtocolError, TimeoutError
from ._clock import RuntimeClock
from ._transport_binding import TransportBinding


@dataclass(frozen=True, slots=True)
class InboundMessage:
    """Message and monotonic receive time copied from a transport route."""

    message: object
    received_at_s: float


class _RoutedMessage(Protocol):
    message: object
    received_at_s: float


class _RouteSubscription(Protocol):
    def receive(self, timeout_s: float | None = None) -> _RoutedMessage: ...

    def close(self) -> None: ...


class RouteMailbox:
    """Copy one exact transport route into a bounded actor-owned inbox."""

    def __init__(
        self,
        *,
        binding: TransportBinding,
        route_key: Hashable,
        route_capacity: int,
        mailbox_capacity: int,
        model: str,
        clock: RuntimeClock,
        wake: threading.Event,
    ) -> None:
        self._binding = binding
        self._route_key = route_key
        self._route_capacity = route_capacity
        self._mailbox_capacity = mailbox_capacity
        self._model = model
        self._clock = clock
        self._wake = wake
        self._control_lock = threading.Lock()
        self._data_lock = threading.Lock()
        self._route: _RouteSubscription | None = None
        self._thread: threading.Thread | None = None
        self._receiver_stop = threading.Event()
        self._actor_stop = threading.Event()
        self._inbound: deque[InboundMessage] = deque()
        self._failure: BaseException | None = None

    def reset(self) -> None:
        """Prepare an empty inbox after the prior receiver has stopped."""

        with self._control_lock:
            if self._thread is not None and self._thread.is_alive():
                raise ProtocolError("cannot reset a running route receiver")
            self._receiver_stop = threading.Event()
        with self._data_lock:
            self._inbound.clear()
            self._failure = None

    def start(self, *, actor_stop: threading.Event) -> None:
        """Subscribe and start the route-copy thread exactly once."""

        route = cast(
            _RouteSubscription,
            self._binding.subscribe_route(
                self._route_key,
                queue_capacity=self._route_capacity,
            ),
        )
        with self._control_lock:
            if self._thread is not None and self._thread.is_alive():
                route.close()
                raise ProtocolError("route receiver is already running")
            self._actor_stop = actor_stop
            self._route = route
            thread = threading.Thread(
                target=self._receive_loop,
                args=(route, self._receiver_stop, actor_stop),
                name=f"LinkerbotHand.{self._model}.route",
                daemon=False,
            )
            self._thread = thread
            thread.start()

    def close(self) -> None:
        """Stop route admission and wake the actor without waiting."""

        self._receiver_stop.set()
        with self._control_lock:
            route = self._route
            self._route = None
        if route is not None:
            route.close()
        self._wake.set()

    def join(self, *, deadline_s: float) -> None:
        """Join the receiver within the caller's absolute deadline."""

        with self._control_lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, deadline_s - self._clock.monotonic()))
            if thread.is_alive():
                raise TimeoutError("device route receiver did not stop in time")
        with self._control_lock:
            if self._thread is thread:
                self._thread = None

    def stop(self, *, deadline_s: float) -> None:
        """Close route admission and boundedly join its receiver."""

        self.close()
        self.join(deadline_s=deadline_s)

    def pop(self) -> InboundMessage | None:
        """Return the next routed message without blocking."""

        with self._data_lock:
            return None if not self._inbound else self._inbound.popleft()

    def error(self) -> BaseException | None:
        """Return the receiver's terminal error, if one occurred."""

        with self._data_lock:
            return self._failure

    def has_pending(self) -> bool:
        """Return whether a routed message is waiting without removing it."""

        with self._data_lock:
            return bool(self._inbound)

    def _receive_loop(
        self,
        route: _RouteSubscription,
        receiver_stop: threading.Event,
        actor_stop: threading.Event,
    ) -> None:
        while not receiver_stop.is_set() and not actor_stop.is_set():
            try:
                routed = route.receive(timeout_s=0.05)
            except TransportTimeoutError:
                continue
            except TransportClosedError as error:
                if receiver_stop.is_set() or actor_stop.is_set():
                    return
                self._set_error(error)
                return
            except Exception as error:  # noqa: BLE001 - receiver thread boundary
                self._set_error(error)
                return
            inbound = InboundMessage(routed.message, routed.received_at_s)
            with self._data_lock:
                if len(self._inbound) >= self._mailbox_capacity:
                    self._failure = ProtocolError(
                        "device actor receive mailbox overflowed"
                    )
                    self._wake.set()
                    return
                self._inbound.append(inbound)
            self._wake.set()

    def _set_error(self, error: BaseException) -> None:
        with self._data_lock:
            self._failure = error
        self._wake.set()


__all__ = ["InboundMessage", "RouteMailbox"]
