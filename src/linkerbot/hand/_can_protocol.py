"""Structural transport contracts shared by classic CAN hand managers."""

import threading
import time
from collections.abc import Callable
from typing import Protocol

import can

from linkerbot_transport.errors import TimeoutError


class CANDispatcherLike(Protocol):
    """Minimal classic CAN dispatcher contract used by sensor managers."""

    def send(self, msg: can.Message) -> None:
        """Queue one CAN message for transmission."""
        ...

    def subscribe(self, callback: Callable[[can.Message], None]) -> None:
        """Subscribe to received CAN messages."""
        ...


class CANSubscriptionDispatcherLike(CANDispatcherLike, Protocol):
    """Classic CAN dispatcher that also supports explicit unsubscription."""

    def unsubscribe(self, callback: Callable[[can.Message], None]) -> None:
        """Unsubscribe from received CAN messages."""
        ...


class CANSubscriptionScope:
    """Track manager subscriptions without taking ownership of a dispatcher."""

    def __init__(self, dispatcher: CANSubscriptionDispatcherLike) -> None:
        self._dispatcher = dispatcher
        self._condition = threading.Condition()
        self._cleanup_lock = threading.Lock()
        self._closed = False
        self._active_sends = 0
        self._active_callbacks = 0
        self._subscriptions: list[
            tuple[Callable[[can.Message], None], Callable[[can.Message], None]]
        ] = []

    def send(self, msg: can.Message) -> None:
        """Forward a frame to the shared dispatcher."""
        with self._condition:
            if self._closed:
                raise RuntimeError(
                    "Cannot send through a closed CAN subscription scope"
                )
            self._active_sends += 1
        try:
            self._dispatcher.send(msg)
        finally:
            with self._condition:
                self._active_sends -= 1
                self._condition.notify_all()

    def subscribe(self, callback: Callable[[can.Message], None]) -> None:
        """Register a callback that becomes inert as soon as the scope closes."""

        def guarded_callback(msg: can.Message) -> None:
            with self._condition:
                if self._closed:
                    return
                self._active_callbacks += 1
            try:
                callback(msg)
            finally:
                with self._condition:
                    self._active_callbacks -= 1
                    self._condition.notify_all()

        # Keep registration and scope closure ordered. Dispatcher.subscribe() only
        # takes its subscriber-list lock and does not invoke the callback inline.
        with self._condition:
            if self._closed:
                raise RuntimeError("Cannot subscribe through a closed CAN scope")
            self._dispatcher.subscribe(guarded_callback)
            self._subscriptions.append((callback, guarded_callback))

    def unsubscribe(self, callback: Callable[[can.Message], None]) -> None:
        """Remove a callback registered directly through this scope."""
        with self._cleanup_lock:
            with self._condition:
                matches = [item for item in self._subscriptions if item[0] == callback]
            for item in matches:
                self._dispatcher.unsubscribe(item[1])
                with self._condition:
                    if item in self._subscriptions:
                        self._subscriptions.remove(item)

    def close(self, timeout_s: float = 1.0) -> None:
        """Unsubscribe all callbacks and boundedly drain callbacks already running."""
        deadline = time.monotonic() + timeout_s
        remaining = max(0.0, deadline - time.monotonic())
        if not self._cleanup_lock.acquire(timeout=remaining):
            raise TimeoutError("CAN subscription cleanup is already in progress")
        try:
            with self._condition:
                self._closed = True
                subscriptions = list(self._subscriptions)

            for item in subscriptions:
                self._dispatcher.unsubscribe(item[1])
                with self._condition:
                    if item in self._subscriptions:
                        self._subscriptions.remove(item)

            with self._condition:
                while self._active_sends or self._active_callbacks:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(
                            "CAN subscription scope did not drain before timeout "
                            f"({self._active_sends} sends and "
                            f"{self._active_callbacks} callbacks still active)"
                        )
                    self._condition.wait(remaining)
        finally:
            self._cleanup_lock.release()
