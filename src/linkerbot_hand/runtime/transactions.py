"""Bounded coalescing for active reads and equivalent polling transactions."""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable, Hashable
from dataclasses import dataclass, field

from ..api.errors import (
    HandError,
    ProtocolError,
    QueueFullError,
    TimeoutError,
    ValidationError,
)
from ._clock import RuntimeClock

DEVICE_INFO_KEY = "__device_info__"


class TransactionWaiter:
    """One bounded caller waiting for a sequence newer than its start point."""

    def __init__(
        self,
        *,
        key: Hashable,
        start_sequence: int,
        deadline_s: float,
        clock: RuntimeClock,
        cancel: Callable[[TransactionWaiter], None],
    ) -> None:
        self.key = key
        self.start_sequence = start_sequence
        self.deadline_s = deadline_s
        self._clock = clock
        self._cancel = cancel
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._result: object | None = None
        self._error: HandError | None = None
        self._completed = False

    def wait(self) -> object:
        """Wait only through the caller's absolute deadline."""

        while not self._event.is_set():
            remaining = max(0.0, self.deadline_s - self._clock.monotonic())
            if remaining == 0:
                self._cancel(self)
                raise TimeoutError("active read did not complete before its deadline")
            self._clock.wait(self._event, remaining)
        with self._lock:
            if self._error is not None:
                raise self._error
            return self._result

    def complete(self, result: object, *, sequence: int) -> None:
        """Complete only with data collected after waiter creation."""

        with self._lock:
            if self._completed:
                return
            if type(sequence) is not int or sequence <= self.start_sequence:
                self._error = ProtocolError(
                    "active read completed without a post-call sample"
                )
            else:
                self._result = result
            self._completed = True
            self._event.set()

    def fail(self, error: HandError) -> None:
        """Wake this caller with one stable runtime error."""

        with self._lock:
            if self._completed:
                return
            self._error = error
            self._completed = True
            self._event.set()


@dataclass(slots=True)
class _Entry:
    key: Hashable
    order: int
    waiters: list[TransactionWaiter] = field(default_factory=list)
    requested_by_poll: bool = False
    in_flight: bool = False


@dataclass(frozen=True, slots=True)
class TransactionClaim:
    """One coalesced transaction selected by the device actor."""

    key: Hashable
    requested_by_poll: bool


class TransactionCoordinator:
    """Coalesce equivalent poll/read work into a single bounded transaction."""

    def __init__(
        self,
        *,
        waiter_capacity: int,
        clock: RuntimeClock,
        wake: Callable[[], None],
    ) -> None:
        if type(waiter_capacity) is not int or waiter_capacity <= 0:
            raise ValidationError("waiter_capacity must be a positive int")
        if not callable(wake):
            raise ValidationError("wake must be callable")
        self._capacity = waiter_capacity
        self._clock = clock
        self._wake = wake
        self._lock = threading.Lock()
        self._entries: OrderedDict[Hashable, _Entry] = OrderedDict()
        self._waiter_count = 0
        self._next_order = 0
        self._accepting = False
        self._admission_error: HandError = TimeoutError("reads are not available")

    def activate(self) -> None:
        """Allow active reads after identity and baseline verification."""

        with self._lock:
            if self._entries:
                raise ProtocolError("cannot activate with retained transactions")
            self._accepting = True

    def reject_new(self, error: HandError) -> None:
        """Close read admission without racing a concurrent waiter append."""

        if not isinstance(error, HandError):
            raise ValidationError("error must be HandError")
        with self._lock:
            self._accepting = False
            self._admission_error = error

    def request(
        self,
        key: Hashable,
        *,
        start_sequence: int,
        deadline_s: float,
    ) -> TransactionWaiter:
        """Create or join one equivalent active transaction."""

        _validate_key(key)
        if type(start_sequence) is not int or start_sequence < 0:
            raise ValidationError("start_sequence must be a non-negative int")
        if deadline_s <= self._clock.monotonic():
            raise TimeoutError("active read deadline has already expired")
        with self._lock:
            if not self._accepting:
                raise self._admission_error
            if self._waiter_count >= self._capacity:
                raise QueueFullError("active read waiter capacity is full")
            entry = self._entry_locked(key)
            waiter = TransactionWaiter(
                key=key,
                start_sequence=start_sequence,
                deadline_s=deadline_s,
                clock=self._clock,
                cancel=self.cancel,
            )
            entry.waiters.append(waiter)
            self._waiter_count += 1
        self._wake()
        return waiter

    def request_poll(self, key: Hashable) -> bool:
        """Create or join one periodic transaction without adding a waiter."""

        _validate_key(key)
        with self._lock:
            if not self._accepting:
                return False
            entry = self._entry_locked(key)
            was_requested = entry.requested_by_poll
            entry.requested_by_poll = True
        self._wake()
        return not was_requested

    def take_next(self) -> TransactionClaim | None:
        """Claim the oldest pending transaction; at most one claim per key."""

        with self._lock:
            entry = next(
                (value for value in self._entries.values() if not value.in_flight),
                None,
            )
            if entry is None:
                return None
            entry.in_flight = True
            return TransactionClaim(entry.key, entry.requested_by_poll)

    def complete(self, key: Hashable, result: object, *, sequence: int) -> None:
        """Complete callers predating the sample and retain later callers."""

        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return
            completed = tuple(
                waiter for waiter in entry.waiters if waiter.start_sequence < sequence
            )
            remaining = [
                waiter for waiter in entry.waiters if waiter.start_sequence >= sequence
            ]
            self._waiter_count -= len(completed)
            if remaining:
                entry.waiters = remaining
                entry.requested_by_poll = False
                entry.in_flight = False
                self._next_order += 1
                entry.order = self._next_order
                self._entries.move_to_end(key)
            else:
                del self._entries[key]
        for waiter in completed:
            waiter.complete(result, sequence=sequence)

    def fail(self, key: Hashable, error: HandError) -> None:
        """Fail one coalesced transaction and all of its active callers."""

        if not isinstance(error, HandError):
            raise ValidationError("error must be HandError")
        with self._lock:
            entry = self._entries.pop(key, None)
            if entry is None:
                return
            waiters = tuple(entry.waiters)
            self._waiter_count -= len(waiters)
        for waiter in waiters:
            waiter.fail(error)

    def fail_all(self, error: HandError) -> None:
        """Discard all generation-bound work and wake every caller."""

        if not isinstance(error, HandError):
            raise ValidationError("error must be HandError")
        with self._lock:
            waiters = tuple(
                waiter for entry in self._entries.values() for waiter in entry.waiters
            )
            self._entries.clear()
            self._waiter_count = 0
        for waiter in waiters:
            waiter.fail(error)

    def cancel_polls(self) -> bool:
        """Remove poll-only work that the actor has not claimed yet."""

        changed = False
        with self._lock:
            for key, entry in tuple(self._entries.items()):
                if not entry.requested_by_poll:
                    continue
                entry.requested_by_poll = False
                changed = True
                if not entry.waiters and not entry.in_flight:
                    del self._entries[key]
        return changed

    def cancel_poll_if_unobserved(self, key: Hashable) -> bool:
        """Discard a claimed poll unless a fresh active reader joined it."""

        _validate_key(key)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return True
            if entry.waiters:
                return False
            del self._entries[key]
            return True

    def cancel(self, waiter: TransactionWaiter) -> None:
        """Remove one timed-out caller without cancelling shared in-flight work."""

        with self._lock:
            entry = self._entries.get(waiter.key)
            if entry is None or waiter not in entry.waiters:
                return
            entry.waiters.remove(waiter)
            self._waiter_count -= 1
            if (
                not entry.waiters
                and not entry.requested_by_poll
                and not entry.in_flight
            ):
                del self._entries[waiter.key]

    @property
    def pending_count(self) -> int:
        """Return logical transaction count, not the number of joined callers."""

        with self._lock:
            return len(self._entries)

    @property
    def waiter_count(self) -> int:
        """Return bounded active-reader count."""

        with self._lock:
            return self._waiter_count

    def _entry_locked(self, key: Hashable) -> _Entry:
        entry = self._entries.get(key)
        if entry is not None:
            return entry
        self._next_order += 1
        entry = _Entry(key=key, order=self._next_order)
        self._entries[key] = entry
        return entry


def _validate_key(key: object) -> None:
    try:
        hash(key)
    except TypeError as error:
        raise ValidationError("transaction key must be hashable") from error


__all__ = [
    "DEVICE_INFO_KEY",
    "TransactionClaim",
    "TransactionCoordinator",
    "TransactionWaiter",
]
