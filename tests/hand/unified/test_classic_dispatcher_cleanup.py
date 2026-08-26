import importlib
import threading
import time
from collections.abc import Callable
from typing import cast

import can
import pytest

from linkerbot.comm import CANMessageDispatcher
from linkerbot.exceptions import TimeoutError
from linkerbot.hand._can_protocol import CANSubscriptionScope
from linkerbot.hand.l6 import L6
from linkerbot.hand.l20lite import L20lite
from linkerbot.hand.l25 import L25
from linkerbot.hand.o6 import O6


class SharedDispatcher:
    def __init__(self) -> None:
        self.can_interface = object()
        self.subscribers: list[Callable[[can.Message], None]] = []
        self.unsubscribe_calls = 0
        self.stop_calls = 0
        self.stop_failures = 0

    def send(self, msg: can.Message) -> None:
        del msg

    def subscribe(self, callback: Callable[[can.Message], None]) -> None:
        if callback not in self.subscribers:
            self.subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[can.Message], None]) -> None:
        if callback in self.subscribers:
            self.subscribers.remove(callback)
            self.unsubscribe_calls += 1

    def stop(self) -> None:
        self.stop_calls += 1
        if self.stop_failures:
            self.stop_failures -= 1
            raise RuntimeError("injected dispatcher stop failure")


def test_callback_copied_before_close_is_inert_after_scope_closes() -> None:
    dispatcher = SharedDispatcher()
    scope = CANSubscriptionScope(dispatcher)
    received: list[can.Message] = []
    scope.subscribe(received.append)
    copied_callback = dispatcher.subscribers[0]

    scope.close()
    copied_callback(can.Message(arbitration_id=0x28, data=[]))

    assert received == []
    assert dispatcher.subscribers == []


def test_scope_close_reports_callback_drain_timeout_and_can_be_retried() -> None:
    dispatcher = SharedDispatcher()
    scope = CANSubscriptionScope(dispatcher)
    callback_entered = threading.Event()
    release_callback = threading.Event()

    def blocking_callback(_msg: can.Message) -> None:
        callback_entered.set()
        release_callback.wait(1.0)

    scope.subscribe(blocking_callback)
    callback_thread = threading.Thread(
        target=dispatcher.subscribers[0],
        args=(can.Message(arbitration_id=0x28, data=[]),),
    )
    callback_thread.start()
    assert callback_entered.wait(0.2)

    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError, match="1 callbacks still active"):
            scope.close(timeout_s=0.02)
    finally:
        release_callback.set()
        callback_thread.join(timeout=0.2)

    assert time.monotonic() - started < 0.2
    assert not callback_thread.is_alive()
    assert dispatcher.subscribers == []
    scope.close(timeout_s=0.1)


def test_scope_close_retains_failed_and_unattempted_subscriptions_for_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher = SharedDispatcher()
    scope = CANSubscriptionScope(dispatcher)
    callbacks = [lambda _msg: None for _ in range(3)]
    for callback in callbacks:
        scope.subscribe(callback)
    guarded = list(dispatcher.subscribers)
    original_unsubscribe = dispatcher.unsubscribe
    attempts: list[Callable[[can.Message], None]] = []
    failed_once = False

    def flaky_unsubscribe(callback: Callable[[can.Message], None]) -> None:
        nonlocal failed_once
        attempts.append(callback)
        if callback is guarded[1] and not failed_once:
            failed_once = True
            raise RuntimeError("injected unsubscribe failure")
        original_unsubscribe(callback)

    monkeypatch.setattr(dispatcher, "unsubscribe", flaky_unsubscribe)

    with pytest.raises(RuntimeError, match="injected unsubscribe failure"):
        scope.close()

    assert dispatcher.subscribers == guarded[1:]
    assert [item[1] for item in scope._subscriptions] == guarded[1:]

    scope.close()

    assert attempts == [guarded[0], guarded[1], guarded[1], guarded[2]]
    assert dispatcher.subscribers == []
    assert scope._subscriptions == []


def test_concurrent_scope_close_uses_one_bounded_deadline() -> None:
    dispatcher = SharedDispatcher()
    scope = CANSubscriptionScope(dispatcher)
    callback_entered = threading.Event()
    release_callback = threading.Event()

    def blocking_callback(_msg: can.Message) -> None:
        callback_entered.set()
        release_callback.wait(timeout=1.0)

    scope.subscribe(blocking_callback)
    callback_thread = threading.Thread(
        target=dispatcher.subscribers[0],
        args=(can.Message(arbitration_id=0x28, data=[]),),
    )
    callback_thread.start()
    assert callback_entered.wait(timeout=0.2)

    first_errors: list[Exception] = []
    first_done = threading.Event()

    def first_close() -> None:
        try:
            scope.close(timeout_s=0.5)
        except Exception as error:
            first_errors.append(error)
        finally:
            first_done.set()

    close_thread = threading.Thread(target=first_close)
    close_thread.start()
    deadline = time.monotonic() + 0.2
    while dispatcher.subscribers and time.monotonic() < deadline:
        time.sleep(0.001)

    started = time.monotonic()
    with pytest.raises(TimeoutError, match="already in progress"):
        scope.close(timeout_s=0.02)
    assert time.monotonic() - started < 0.2
    assert not first_done.is_set()

    release_callback.set()
    callback_thread.join(timeout=0.2)
    close_thread.join(timeout=0.2)

    assert not callback_thread.is_alive()
    assert not close_thread.is_alive()
    assert first_errors == []


@pytest.mark.parametrize(
    ("hand_type", "subscription_count"),
    [
        (L6, 11),
        (O6, 12),
        (L20lite, 11),
        (L25, 11),
    ],
)
def test_close_unsubscribes_all_managers_from_shared_dispatcher(
    hand_type: type[L6] | type[O6] | type[L20lite] | type[L25],
    subscription_count: int,
) -> None:
    dispatcher = SharedDispatcher()
    hand = hand_type(
        side="left",
        interface_name="unused",
        dispatcher=cast(CANMessageDispatcher, dispatcher),
    )

    assert len(dispatcher.subscribers) == subscription_count

    hand._on_bus_error(RuntimeError("disconnected"))
    assert hand.is_closed()
    close_threads = [threading.Thread(target=hand.close) for _ in range(4)]
    for thread in close_threads:
        thread.start()
    for thread in close_threads:
        thread.join(timeout=1.0)
    assert all(not thread.is_alive() for thread in close_threads)

    hand.close()

    assert dispatcher.subscribers == []
    assert dispatcher.unsubscribe_calls == subscription_count
    assert dispatcher.stop_calls == 0


@pytest.mark.parametrize(
    ("hand_type", "subscription_count"),
    [
        (L6, 11),
        (O6, 12),
        (L20lite, 11),
        (L25, 11),
    ],
)
def test_closing_one_hand_preserves_other_hand_subscriptions(
    hand_type: type[L6] | type[O6] | type[L20lite] | type[L25],
    subscription_count: int,
) -> None:
    dispatcher = SharedDispatcher()
    first = hand_type(
        side="left",
        interface_name="unused",
        dispatcher=cast(CANMessageDispatcher, dispatcher),
    )
    second = hand_type(
        side="right",
        interface_name="unused",
        dispatcher=cast(CANMessageDispatcher, dispatcher),
    )

    assert len(dispatcher.subscribers) == subscription_count * 2

    first.close()
    assert len(dispatcher.subscribers) == subscription_count
    assert dispatcher.stop_calls == 0

    second.close()
    assert dispatcher.subscribers == []
    assert dispatcher.unsubscribe_calls == subscription_count * 2
    assert dispatcher.stop_calls == 0


@pytest.mark.parametrize("hand_type", [L6, O6, L20lite, L25])
def test_close_timeout_retains_poll_thread_for_retry(
    hand_type: type[L6] | type[O6] | type[L20lite] | type[L25],
    monkeypatch,
) -> None:
    dispatcher = SharedDispatcher()
    hand = hand_type(
        side="left",
        interface_name="unused",
        dispatcher=cast(CANMessageDispatcher, dispatcher),
    )
    module = importlib.import_module(hand_type.__module__)
    monkeypatch.setattr(module, "_THREAD_JOIN_TIMEOUT_S", 0.02)
    angle_source = next(
        source for source in module.SensorSource if source.value == "angle"
    )
    sender_entered = threading.Event()
    release_sender = threading.Event()

    def blocking_sender() -> None:
        sender_entered.set()
        release_sender.wait(1.0)

    hand._polling_senders["angle"] = blocking_sender
    hand.start_polling({angle_source: 0.01})
    assert sender_entered.wait(0.2)

    try:
        started = time.monotonic()
        with pytest.raises(TimeoutError, match="polling threads did not stop"):
            hand.close()

        assert time.monotonic() - started < 0.2
        assert not hand._resources_closed
        assert len(hand._polling_threads) == 1
        assert next(iter(hand._polling_threads.values())).is_alive()
        assert dispatcher.subscribers
    finally:
        release_sender.set()

    hand.close()
    assert hand._resources_closed
    assert hand._polling_threads == {}
    assert dispatcher.subscribers == []


@pytest.mark.parametrize("hand_type", [L6, O6, L20lite, L25])
def test_owned_dispatcher_close_failure_can_be_retried(
    hand_type: type[L6] | type[O6] | type[L20lite] | type[L25],
) -> None:
    dispatcher = SharedDispatcher()
    hand = hand_type(
        side="left",
        interface_name="unused",
        dispatcher=cast(CANMessageDispatcher, dispatcher),
    )
    hand._owns_dispatcher = True
    dispatcher.stop_failures = 1

    with pytest.raises(RuntimeError, match="injected dispatcher stop failure"):
        hand.close()

    assert not hand._resources_closed
    hand.close()
    assert hand._resources_closed
    assert dispatcher.stop_calls == 2
