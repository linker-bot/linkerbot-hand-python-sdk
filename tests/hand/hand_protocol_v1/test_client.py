from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable

import pytest

from linkerbot.comm.canfd import CANFDMessage
from linkerbot.exceptions import StateError, TimeoutError, ValidationError
from linkerbot.hand.hand_protocol_v1 import (
    HandProtocolDeviceError,
    HandProtocolError,
    HandProtocolV1,
)
from tests.hand.hand_protocol_v1.fakes import FakeDispatcher

pytestmark = [pytest.mark.o30i, pytest.mark.canfd]


def _response(data: bytes, *, arbitration_id: int = 0x401) -> CANFDMessage:
    return CANFDMessage(
        arbitration_id=arbitration_id,
        data=data,
        is_extended_id=False,
        frame_type=0x04,
    )


def test_read_collects_single_response_and_ignores_padding() -> None:
    def responder(message: CANFDMessage) -> Iterable[CANFDMessage]:
        assert message.data == bytes.fromhex("01 14 05")
        yield _response(bytes.fromhex("01 14 05 01 02 03 04 05") + bytes(40))

    client = HandProtocolV1(FakeDispatcher(responder))

    assert client.read(main_index=1, sub_index=0x14, length=5) == bytes(range(1, 6))


def test_read_assembles_large_object_from_si_fragments() -> None:
    expected = bytes(range(72))

    def responder(message: CANFDMessage) -> Iterable[CANFDMessage]:
        assert message.data == bytes.fromhex("20 00 48")
        yield _response(bytes((0x20, 0x00, 0x3D)) + expected[:61])
        yield _response(bytes((0x20, 0x3D, 0x0B)) + expected[61:])

    client = HandProtocolV1(FakeDispatcher(responder))

    assert client.read(main_index=0x20, length=72) == expected


def test_read_can_assemble_non_overlapping_fragments_out_of_order() -> None:
    expected = bytes(range(72))

    def responder(message: CANFDMessage) -> Iterable[CANFDMessage]:
        yield _response(bytes((0x20, 0x3D, 0x0B)) + expected[61:])
        yield _response(bytes((0x20, 0x00, 0x3D)) + expected[:61])

    client = HandProtocolV1(FakeDispatcher(responder))

    assert client.read(main_index=0x20, length=72) == expected


def test_device_error_response_raises_typed_error() -> None:
    dispatcher = FakeDispatcher(
        lambda message: [_response(bytes.fromhex("4F 28 01 02"))]
    )
    client = HandProtocolV1(dispatcher)

    with pytest.raises(HandProtocolDeviceError) as captured:
        client.read(main_index=0x20, sub_index=0x28, length=10)

    assert captured.value.error_index == 0x28
    assert captured.value.error_code == 0x02


def test_reading_error_object_does_not_treat_it_as_error_response() -> None:
    dispatcher = FakeDispatcher(
        lambda message: [_response(bytes.fromhex("4F 00 01 07"))]
    )
    client = HandProtocolV1(dispatcher)

    assert client.read(main_index=0x4F, length=1) == b"\x07"


def test_write_waits_for_echo_response() -> None:
    def responder(message: CANFDMessage) -> Iterable[CANFDMessage]:
        assert message.data == bytes.fromhex("01 14 05 80 80 80 80 80")
        yield _response(bytes.fromhex("01 14 05 80 80 80 80 80"))

    dispatcher = FakeDispatcher(responder)
    client = HandProtocolV1(dispatcher)

    assert client.write(main_index=1, sub_index=0x14, payload=b"\x80" * 5) == (
        b"\x80" * 5
    )


def test_frames_with_wrong_id_or_extended_flag_do_not_satisfy_request() -> None:
    def responder(message: CANFDMessage) -> Iterable[CANFDMessage]:
        yield _response(bytes.fromhex("01 00 01 AA"), arbitration_id=0x402)
        yield CANFDMessage(
            arbitration_id=0x401,
            data=bytes.fromhex("01 00 01 BB"),
            is_extended_id=True,
        )
        yield _response(bytes.fromhex("01 00 01 CC"))

    client = HandProtocolV1(FakeDispatcher(responder))

    assert client.read(main_index=1, length=1) == b"\xcc"


def test_legacy_broadcast_dispatcher_still_filters_endpoint() -> None:
    class LegacyDispatcher:
        def __init__(self) -> None:
            self.callback: Callable[[CANFDMessage], None] | None = None

        def subscribe(self, callback: Callable[[CANFDMessage], None]) -> None:
            self.callback = callback

        def unsubscribe(self, callback: Callable[[CANFDMessage], None]) -> None:
            if self.callback == callback:
                self.callback = None

        def send(self, message: CANFDMessage) -> None:
            assert self.callback is not None
            self.callback(_response(bytes.fromhex("01 00 01 AA"), arbitration_id=0x402))
            self.callback(
                CANFDMessage(
                    arbitration_id=0x401,
                    data=bytes.fromhex("01 00 01 BB"),
                    is_extended_id=True,
                )
            )
            self.callback(_response(bytes.fromhex("01 00 01 CC")))

        def stop(self) -> None:
            pass

    client = HandProtocolV1(LegacyDispatcher())

    assert client.read(main_index=1, length=1) == b"\xcc"


def test_read_ignores_response_with_wrong_rts_bit() -> None:
    def responder(message: CANFDMessage) -> Iterable[CANFDMessage]:
        yield _response(bytes.fromhex("81 00 01 AA"))
        yield _response(bytes.fromhex("01 00 01 BB"))

    client = HandProtocolV1(FakeDispatcher(responder))

    assert client.read(main_index=1, length=1, rts=False) == b"\xbb"


def test_target_read_ignores_actual_value_response() -> None:
    def responder(message: CANFDMessage) -> Iterable[CANFDMessage]:
        yield _response(bytes.fromhex("01 00 01 AA"))
        yield _response(bytes.fromhex("81 00 01 BB"))

    client = HandProtocolV1(FakeDispatcher(responder))

    assert client.read(main_index=1, length=1, rts=True) == b"\xbb"


def test_malformed_matching_frame_fails_pending_request() -> None:
    dispatcher = FakeDispatcher(lambda message: [_response(b"\x01\x00")])
    client = HandProtocolV1(dispatcher)

    with pytest.raises(HandProtocolError, match="header"):
        client.read(main_index=1, length=1)


def test_timeout_and_timeout_validation() -> None:
    client = HandProtocolV1(FakeDispatcher())

    with pytest.raises(TimeoutError):
        client.read(main_index=1, length=1, timeout_ms=5)
    with pytest.raises(ValidationError, match="positive"):
        client.read(main_index=1, length=1, timeout_ms=0)
    with pytest.raises(ValidationError, match="int or float"):
        client.read(main_index=1, length=1, timeout_ms=True)
    with pytest.raises(ValidationError, match="finite"):
        client.read(main_index=1, length=1, timeout_ms=float("nan"))
    with pytest.raises(ValidationError, match="finite"):
        client.read(main_index=1, length=1, timeout_ms=float("inf"))
    with pytest.raises(ValidationError, match="finite"):
        client.read(main_index=1, length=1, timeout_ms=10**400)


def test_close_unsubscribes_exact_callback_and_is_idempotent() -> None:
    dispatcher = FakeDispatcher()
    client = HandProtocolV1(dispatcher)
    assert len(dispatcher.filtered_subscribers) == 1

    client.close()
    client.close()

    assert dispatcher.filtered_subscribers == []
    with pytest.raises(StateError):
        client.read(main_index=1, length=1)


@pytest.mark.parametrize("timeout_s", [True, 0, float("nan"), float("inf"), 10**400])
def test_close_rejects_invalid_timeout_without_closing(
    timeout_s: int | float,
) -> None:
    client = HandProtocolV1(FakeDispatcher())

    with pytest.raises(ValidationError):
        client.close(timeout_s=timeout_s)

    assert not client._closed
    client.close()


def test_close_wakes_pending_request() -> None:
    dispatcher = FakeDispatcher()
    client = HandProtocolV1(dispatcher)
    errors: list[Exception] = []

    def read() -> None:
        try:
            client.read(main_index=1, length=1, timeout_ms=1000)
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=read)
    thread.start()
    deadline = time.monotonic() + 0.2
    while not dispatcher.sent and time.monotonic() < deadline:
        time.sleep(0.001)
    client.close()
    thread.join(timeout=1)

    assert len(errors) == 1
    assert isinstance(errors[0], StateError)


def test_close_after_waiter_registration_prevents_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher = FakeDispatcher()
    client = HandProtocolV1(dispatcher)
    send_message = client._send_message
    waiter_registered = threading.Event()
    release_request = threading.Event()
    close_returned = threading.Event()
    errors: list[Exception] = []

    def pause_before_send(message: CANFDMessage) -> None:
        waiter_registered.set()
        release_request.wait()
        send_message(message)

    def read() -> None:
        try:
            client.read(main_index=1, length=1, timeout_ms=1000)
        except Exception as error:
            errors.append(error)

    monkeypatch.setattr(client, "_send_message", pause_before_send)
    request_thread = threading.Thread(target=read)
    request_thread.start()
    assert waiter_registered.wait(timeout=1)
    with client._state_lock:
        assert client._pending is not None

    close_thread = threading.Thread(
        target=lambda: (client.close(), close_returned.set())
    )
    close_thread.start()
    completed_before_release = close_returned.wait(timeout=1)
    release_request.set()
    close_thread.join(timeout=1)
    request_thread.join(timeout=1)

    assert completed_before_release
    assert not close_thread.is_alive()
    assert not request_thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], StateError)
    assert dispatcher.sent == []


def test_conflicting_duplicate_fragment_fails_transaction() -> None:
    def responder(message: CANFDMessage) -> Iterable[CANFDMessage]:
        yield _response(bytes.fromhex("20 00 01 AA"))
        yield _response(bytes.fromhex("20 00 01 BB"))

    client = HandProtocolV1(FakeDispatcher(responder))

    with pytest.raises(HandProtocolError, match="conflicting duplicate"):
        client.read(main_index=0x20, length=2)


def test_concurrent_requests_are_serialized_without_transaction_ids() -> None:
    dispatcher = FakeDispatcher()
    client = HandProtocolV1(dispatcher)
    results: list[bytes] = []

    def read(sub_index: int) -> None:
        results.append(
            client.read(
                main_index=1,
                sub_index=sub_index,
                length=1,
                timeout_ms=1000,
            )
        )

    first = threading.Thread(target=read, args=(0,))
    second = threading.Thread(target=read, args=(1,))
    first.start()
    deadline = time.monotonic() + 1
    while len(dispatcher.sent) < 1 and time.monotonic() < deadline:
        time.sleep(0.001)
    second.start()
    time.sleep(0.01)

    # The second request cannot be sent until the first response completes.
    assert len(dispatcher.sent) == 1
    dispatcher.inject(_response(bytes.fromhex("01 00 01 AA")))
    deadline = time.monotonic() + 1
    while len(dispatcher.sent) < 2 and time.monotonic() < deadline:
        time.sleep(0.001)
    dispatcher.inject(_response(bytes.fromhex("01 01 01 BB")))
    first.join(timeout=1)
    second.join(timeout=1)

    assert results == [b"\xaa", b"\xbb"]
    assert [message.data for message in dispatcher.sent] == [
        bytes.fromhex("01 00 01"),
        bytes.fromhex("01 01 01"),
    ]


def test_close_timeout_during_send_can_be_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher = FakeDispatcher()
    send_entered = threading.Event()
    release_send = threading.Event()
    original_send = dispatcher.send

    def blocking_send(message: CANFDMessage) -> None:
        send_entered.set()
        release_send.wait(timeout=1.0)
        original_send(message)

    monkeypatch.setattr(dispatcher, "send", blocking_send)
    client = HandProtocolV1(dispatcher)
    errors: list[Exception] = []

    def read() -> None:
        try:
            client.read(main_index=1, length=1, timeout_ms=1000)
        except Exception as error:
            errors.append(error)

    request_thread = threading.Thread(target=read)
    request_thread.start()
    assert send_entered.wait(timeout=1.0)

    try:
        started = time.monotonic()
        with pytest.raises(TimeoutError, match="1 sends and 0 callbacks"):
            client.close(timeout_s=0.02)
        assert time.monotonic() - started < 0.2
        assert not client._cleanup_complete
    finally:
        release_send.set()

    request_thread.join(timeout=1.0)
    assert not request_thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], StateError)

    client.close(timeout_s=0.2)
    assert client._cleanup_complete


def test_close_retries_unsubscribe_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher = FakeDispatcher()
    client = HandProtocolV1(dispatcher)
    original_unsubscribe = dispatcher.unsubscribe
    calls = 0

    def flaky_unsubscribe(callback) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("injected unsubscribe failure")
        original_unsubscribe(callback)

    monkeypatch.setattr(dispatcher, "unsubscribe", flaky_unsubscribe)

    with pytest.raises(RuntimeError, match="injected unsubscribe failure"):
        client.close()
    assert client._subscribed
    assert not client._cleanup_complete

    client.close()
    assert calls == 2
    assert not client._subscribed
    assert client._cleanup_complete
    assert dispatcher.filtered_subscribers == []


def test_close_drains_callback_and_copied_callback_is_inert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher = FakeDispatcher()
    client = HandProtocolV1(dispatcher)
    callback_entered = threading.Event()
    release_callback = threading.Event()
    route_calls = 0

    def blocking_route(_message: CANFDMessage) -> None:
        nonlocal route_calls
        route_calls += 1
        callback_entered.set()
        release_callback.wait(timeout=1.0)

    monkeypatch.setattr(client, "_route_message", blocking_route)
    copied_callback = dispatcher.filtered_subscribers[0][1]
    message = _response(bytes.fromhex("01 00 01 AA"))
    callback_thread = threading.Thread(target=copied_callback, args=(message,))
    callback_thread.start()
    assert callback_entered.wait(timeout=1.0)

    try:
        with pytest.raises(TimeoutError, match="0 sends and 1 callbacks"):
            client.close(timeout_s=0.02)
    finally:
        release_callback.set()

    callback_thread.join(timeout=1.0)
    assert not callback_thread.is_alive()
    client.close(timeout_s=0.2)

    copied_callback(message)
    assert route_calls == 1


def test_concurrent_close_waits_for_active_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher = FakeDispatcher()
    client = HandProtocolV1(dispatcher)
    callback_entered = threading.Event()
    release_callback = threading.Event()

    def blocking_route(_message: CANFDMessage) -> None:
        callback_entered.set()
        release_callback.wait(timeout=1.0)

    monkeypatch.setattr(client, "_route_message", blocking_route)
    callback = dispatcher.filtered_subscribers[0][1]
    callback_thread = threading.Thread(
        target=callback,
        args=(_response(bytes.fromhex("01 00 01 AA")),),
    )
    callback_thread.start()
    assert callback_entered.wait(timeout=1.0)

    errors: list[Exception] = []
    first_done = threading.Event()
    second_done = threading.Event()

    def close(done: threading.Event) -> None:
        try:
            client.close(timeout_s=0.5)
        except Exception as error:
            errors.append(error)
        finally:
            done.set()

    first = threading.Thread(target=close, args=(first_done,))
    second = threading.Thread(target=close, args=(second_done,))
    first.start()
    deadline = time.monotonic() + 1.0
    while dispatcher.filtered_subscribers and time.monotonic() < deadline:
        time.sleep(0.001)
    second.start()
    assert not first_done.wait(timeout=0.02)
    assert not second_done.wait(timeout=0.02)

    release_callback.set()
    callback_thread.join(timeout=1.0)
    first.join(timeout=1.0)
    second.join(timeout=1.0)

    assert not callback_thread.is_alive()
    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert client._cleanup_complete
