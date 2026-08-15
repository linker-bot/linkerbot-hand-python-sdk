from __future__ import annotations

import importlib
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest

from linkerbot.comm.canfd import CANFDMessage
from linkerbot.exceptions import TimeoutError
from linkerbot.hand.l30 import L30
from linkerbot.hand.l30.events import SensorSource as L30SensorSource
from linkerbot.hand.o20 import O20
from linkerbot.hand.o20.events import SensorSource as O20SensorSource
from linkerbot.hand.o30i import O30i
from linkerbot.hand.o30i.events import SensorSource as O30iSensorSource


class LifecycleDispatcher:
    def __init__(self) -> None:
        self.sent: list[CANFDMessage] = []
        self.subscribers: list[Callable[[CANFDMessage], None]] = []
        self.filtered_subscribers: list[
            tuple[Callable[[CANFDMessage], bool], Callable[[CANFDMessage], None]]
        ] = []
        self.stop_calls = 0
        self.stop_failures = 0
        self.filtered_subscribe_calls = 0
        self.unsubscribe_calls = 0
        self.unsubscribe_failures = 0
        self.block_sends = False
        self.send_entered = threading.Event()
        self._send_release = threading.Event()

    def send(self, message: CANFDMessage) -> None:
        self.sent.append(message)
        if self.block_sends:
            self.send_entered.set()
            self._send_release.wait(2.0)

    def subscribe(self, callback: Callable[[CANFDMessage], None]) -> None:
        self.subscribers.append(callback)

    def subscribe_filter(
        self,
        predicate: Callable[[CANFDMessage], bool],
        callback: Callable[[CANFDMessage], None],
    ) -> None:
        self.filtered_subscribe_calls += 1
        self.filtered_subscribers.append((predicate, callback))

    def unsubscribe(self, callback: Callable[[CANFDMessage], None]) -> None:
        self.unsubscribe_calls += 1
        if self.unsubscribe_failures:
            self.unsubscribe_failures -= 1
            raise RuntimeError("injected dispatcher unsubscribe failure")
        if callback in self.subscribers:
            self.subscribers.remove(callback)
        self.filtered_subscribers = [
            (predicate, existing)
            for predicate, existing in self.filtered_subscribers
            if existing != callback
        ]

    def stop(self) -> None:
        self.stop_calls += 1
        if self.stop_failures:
            self.stop_failures -= 1
            raise RuntimeError("injected dispatcher stop failure")
        self._send_release.set()

    def release_sends(self) -> None:
        self._send_release.set()


_LIFECYCLE_CASES = (
    (L30, "linkerbot.hand.l30.l30", L30SensorSource.ANGLE, "angle"),
    (O20, "linkerbot.hand.o20.o20", O20SensorSource.ANGLE, O20SensorSource.ANGLE),
    (
        O30i,
        "linkerbot.hand.o30i.o30i",
        O30iSensorSource.ANGLE,
        O30iSensorSource.ANGLE,
    ),
)

_CONSTRUCTION_FAILURE_CASES = (
    (L30, "linkerbot.hand.l30.l30"),
    (O20, "linkerbot.hand.o20.o20"),
    (O30i, "linkerbot.hand.o30i.o30i"),
)


def _make_hand(hand_type: type[Any]) -> tuple[Any, LifecycleDispatcher]:
    dispatcher = LifecycleDispatcher()
    hand = hand_type(dispatcher=dispatcher)
    return hand, dispatcher


@pytest.mark.parametrize(("hand_type", "module_name"), _CONSTRUCTION_FAILURE_CASES)
def test_manager_construction_failure_unsubscribes_shared_dispatcher(
    hand_type: type[Any],
    module_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher = LifecycleDispatcher()
    original_error = RuntimeError("injected manager construction failure")
    module = importlib.import_module(module_name)

    def fail_manager(*_args: Any, **_kwargs: Any) -> None:
        raise original_error

    monkeypatch.setattr(module, "AngleManager", fail_manager)

    with pytest.raises(RuntimeError) as exc_info:
        hand_type(dispatcher=dispatcher)

    assert exc_info.value is original_error
    assert dispatcher.filtered_subscribe_calls == 1
    assert dispatcher.unsubscribe_calls == 1
    assert dispatcher.filtered_subscribers == []
    assert dispatcher.stop_calls == 0


@pytest.mark.parametrize(("hand_type", "module_name"), _CONSTRUCTION_FAILURE_CASES)
def test_manager_construction_failure_stops_owned_dispatcher_once(
    hand_type: type[Any],
    module_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher = LifecycleDispatcher()
    original_error = RuntimeError("injected manager construction failure")
    module = importlib.import_module(module_name)

    def fail_manager(*_args: Any, **_kwargs: Any) -> None:
        raise original_error

    monkeypatch.setattr(module, "CANFDMessageDispatcher", lambda **_kwargs: dispatcher)
    monkeypatch.setattr(module, "AngleManager", fail_manager)

    with pytest.raises(RuntimeError) as exc_info:
        hand_type()

    assert exc_info.value is original_error
    assert dispatcher.filtered_subscribe_calls == 1
    assert dispatcher.unsubscribe_calls == 1
    assert dispatcher.filtered_subscribers == []
    assert dispatcher.stop_calls == 1


@pytest.mark.parametrize(("hand_type", "module_name"), _CONSTRUCTION_FAILURE_CASES)
def test_manager_construction_failure_preserves_original_cleanup_error(
    hand_type: type[Any],
    module_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher = LifecycleDispatcher()
    dispatcher.unsubscribe_failures = 1
    dispatcher.stop_failures = 1
    original_error = RuntimeError("injected manager construction failure")
    module = importlib.import_module(module_name)

    def fail_manager(*_args: Any, **_kwargs: Any) -> None:
        raise original_error

    monkeypatch.setattr(module, "CANFDMessageDispatcher", lambda **_kwargs: dispatcher)
    monkeypatch.setattr(module, "AngleManager", fail_manager)

    with pytest.raises(RuntimeError) as exc_info:
        hand_type()

    assert exc_info.value is original_error
    assert dispatcher.unsubscribe_calls == 2
    assert dispatcher.filtered_subscribers == []
    assert dispatcher.stop_calls == 2


@pytest.mark.parametrize(("hand_type", "module_name"), _CONSTRUCTION_FAILURE_CASES)
def test_partial_construction_cleanup_failure_remains_retryable(
    hand_type: type[Any],
    module_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher = LifecycleDispatcher()
    dispatcher.unsubscribe_failures = 1
    original_error = RuntimeError("injected manager construction failure")
    module = importlib.import_module(module_name)

    def fail_manager(*_args: Any, **_kwargs: Any) -> None:
        raise original_error

    monkeypatch.setattr(module, "AngleManager", fail_manager)

    with pytest.raises(RuntimeError) as exc_info:
        hand_type(dispatcher=dispatcher)

    assert exc_info.value is original_error
    assert dispatcher.unsubscribe_calls == 2
    assert dispatcher.filtered_subscribers == []
    assert dispatcher.stop_calls == 0


@pytest.mark.parametrize(("hand_type", "module_name"), _CONSTRUCTION_FAILURE_CASES)
def test_early_bus_error_cleans_published_dispatcher_once(
    hand_type: type[Any],
    module_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher = LifecycleDispatcher()
    module = importlib.import_module(module_name)

    def build_dispatcher(**options: Any) -> LifecycleDispatcher:
        options["on_bus_error"](RuntimeError("injected early bus error"))
        return dispatcher

    monkeypatch.setattr(module, "CANFDMessageDispatcher", build_dispatcher)

    hand = hand_type()

    assert hand.is_closed()
    assert hand._resources_closed
    assert dispatcher.filtered_subscribe_calls == 1
    assert dispatcher.unsubscribe_calls == 1
    assert dispatcher.filtered_subscribers == []
    assert dispatcher.stop_calls == 1

    hand.close()
    assert dispatcher.stop_calls == 1


@pytest.mark.parametrize(
    ("hand_type", "_module_name", "owns_dispatcher"),
    [
        (hand_type, module_name, owns_dispatcher)
        for hand_type, module_name in _CONSTRUCTION_FAILURE_CASES
        for owns_dispatcher in (False, True)
    ],
)
def test_busy_close_lock_defers_bus_error_cleanup(
    hand_type: type[Any],
    _module_name: str,
    owns_dispatcher: bool,
) -> None:
    hand, dispatcher = _make_hand(hand_type)
    hand._owns_dispatcher = owns_dispatcher
    callback_returned = threading.Event()

    def report_bus_error() -> None:
        hand._on_bus_error(RuntimeError("injected bus error"))
        callback_returned.set()

    hand._close_lock.acquire()
    try:
        callback = threading.Thread(target=report_bus_error)
        callback.start()
        assert callback_returned.wait(0.2)
        callback.join(timeout=0.2)
        assert not callback.is_alive()
        assert not hand._resources_closed
        assert dispatcher.filtered_subscribers
        cleanup_thread = hand._bus_error_cleanup_thread
        assert cleanup_thread is not None and cleanup_thread.is_alive()
    finally:
        hand._close_lock.release()

    cleanup_thread.join(timeout=1.0)

    assert not cleanup_thread.is_alive()
    assert hand.is_closed()
    assert hand._resources_closed
    assert dispatcher.filtered_subscribers == []
    assert dispatcher.stop_calls == int(owns_dispatcher)


@pytest.mark.parametrize(
    ("hand_type", "module_name", "source", "sender_key"), _LIFECYCLE_CASES
)
def test_close_timeout_retains_poll_thread_for_retry(
    hand_type: type[Any],
    module_name: str,
    source: Any,
    sender_key: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hand, dispatcher = _make_hand(hand_type)
    monkeypatch.setattr(
        importlib.import_module(module_name), "_THREAD_JOIN_TIMEOUT_S", 0.02
    )
    sender_entered = threading.Event()
    release_sender = threading.Event()

    def blocking_sender() -> None:
        sender_entered.set()
        release_sender.wait(1.0)

    hand._polling_senders[sender_key] = blocking_sender
    hand.start_polling({source: 0.01})
    assert sender_entered.wait(0.2)
    hand._bus_error = RuntimeError("skip device I/O during cleanup test")

    try:
        started = time.monotonic()
        with pytest.raises(TimeoutError, match="polling threads did not stop"):
            hand.close()

        assert time.monotonic() - started < 0.2
        assert not hand._resources_closed
        assert len(hand._polling_threads) == 1
        assert next(iter(hand._polling_threads.values())).is_alive()
        assert dispatcher.filtered_subscribers
    finally:
        release_sender.set()

    hand.close()
    assert hand._resources_closed
    assert hand._polling_threads == {}
    assert dispatcher.filtered_subscribers == []


@pytest.mark.parametrize(
    ("hand_type", "module_name", "source", "_sender_key"), _LIFECYCLE_CASES
)
def test_owned_dispatcher_stop_unblocks_active_poll_send(
    hand_type: type[Any],
    module_name: str,
    source: Any,
    _sender_key: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hand, dispatcher = _make_hand(hand_type)
    hand._owns_dispatcher = True
    dispatcher.block_sends = True
    monkeypatch.setattr(
        importlib.import_module(module_name), "_THREAD_JOIN_TIMEOUT_S", 0.02
    )
    client = getattr(hand, "_client", None) or hand.protocol
    original_client_close = client.close

    def close_client() -> None:
        original_client_close(timeout_s=0.02)

    monkeypatch.setattr(client, "close", close_client)
    hand.start_polling({source: 0.01})
    assert dispatcher.send_entered.wait(0.2)
    hand._bus_error = RuntimeError("skip device I/O during cleanup test")

    started = time.monotonic()
    with pytest.raises(TimeoutError, match="polling threads did not stop"):
        hand.close()

    assert time.monotonic() - started < 0.3
    assert hand._resources_closed
    assert hand._polling_threads == {}
    assert dispatcher.filtered_subscribers == []
    assert dispatcher.stop_calls == 1

    hand.close()
    assert dispatcher.stop_calls == 1


@pytest.mark.parametrize(
    ("hand_type", "module_name", "source", "_sender_key"), _LIFECYCLE_CASES
)
def test_shared_dispatcher_active_poll_send_is_retryable_without_stop(
    hand_type: type[Any],
    module_name: str,
    source: Any,
    _sender_key: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hand, dispatcher = _make_hand(hand_type)
    dispatcher.block_sends = True
    monkeypatch.setattr(
        importlib.import_module(module_name), "_THREAD_JOIN_TIMEOUT_S", 0.02
    )
    hand.start_polling({source: 0.01})
    assert dispatcher.send_entered.wait(0.2)
    hand._bus_error = RuntimeError("skip device I/O during cleanup test")

    started = time.monotonic()
    with pytest.raises(TimeoutError, match="polling threads did not stop"):
        hand.close()

    assert time.monotonic() - started < 0.2
    assert not hand._resources_closed
    assert dispatcher.stop_calls == 0
    assert dispatcher.filtered_subscribers

    dispatcher.release_sends()
    for thread in hand._polling_threads.values():
        thread.join(timeout=0.3)
    hand.close()

    assert hand._resources_closed
    assert hand._polling_threads == {}
    assert dispatcher.filtered_subscribers == []
    assert dispatcher.stop_calls == 0


@pytest.mark.parametrize(
    ("hand_type", "_module_name", "_source", "_sender_key"), _LIFECYCLE_CASES
)
def test_owned_dispatcher_close_failure_can_be_retried(
    hand_type: type[Any],
    _module_name: str,
    _source: Any,
    _sender_key: Any,
) -> None:
    hand, dispatcher = _make_hand(hand_type)
    hand._bus_error = RuntimeError("skip device I/O during cleanup test")
    hand._owns_dispatcher = True
    dispatcher.stop_failures = 1

    with pytest.raises(RuntimeError, match="injected dispatcher stop failure"):
        hand.close()

    assert not hand._resources_closed
    hand.close()
    assert hand._resources_closed
    assert dispatcher.stop_calls == 2


@pytest.mark.parametrize(
    ("hand_type", "_module_name", "_source", "_sender_key"), _LIFECYCLE_CASES
)
def test_concurrent_close_releases_managers_once(
    hand_type: type[Any],
    _module_name: str,
    _source: Any,
    _sender_key: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hand, _dispatcher = _make_hand(hand_type)
    hand._bus_error = RuntimeError("skip device I/O during cleanup test")
    close_entered = threading.Event()
    release_close = threading.Event()
    close_calls = 0
    original_close = hand.angle.close

    def blocking_close() -> None:
        nonlocal close_calls
        close_calls += 1
        close_entered.set()
        assert release_close.wait(1.0)
        original_close()

    monkeypatch.setattr(hand.angle, "close", blocking_close)
    errors: list[BaseException] = []

    def close_hand() -> None:
        try:
            hand.close()
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=close_hand) for _ in range(4)]
    for thread in threads:
        thread.start()
    assert close_entered.wait(0.2)
    release_close.set()
    for thread in threads:
        thread.join(timeout=1.0)

    assert errors == []
    assert all(not thread.is_alive() for thread in threads)
    assert close_calls == 1
    assert hand._resources_closed
