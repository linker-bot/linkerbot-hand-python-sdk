"""Hand bindings retry released lease cleanup without reopening transports."""

from unittest.mock import Mock

import pytest

from linkerbot_hand import HandDevice, LifecycleError, ValidationError
from linkerbot_hand.runtime._clock import SystemClock
from linkerbot_hand.runtime._transport_arbiter import ARBITERS, TRANSPORT_POOL
from linkerbot_hand.runtime._transport_binding import TransportBinding
from linkerbot_transport import TransportState
from linkerbot_transport.canfd import CANFDTransport
from linkerbot_transport.canfd import ctypes_backend as native
from linkerbot_transport.errors import TransportError
from linkerbot_transport.testing import FakeTransport

from .factories import make_hand


@pytest.mark.parametrize("pool_key", [[], {}, (["channel"],)])
def test_hand_rejects_unhashable_pool_key_without_pending_cleanup(
    monkeypatch, pool_key
):
    transport = FakeTransport()
    transport.pool_key = pool_key
    open_transport = Mock(wraps=transport.open)
    close_transport = Mock(wraps=transport.close)
    monkeypatch.setattr(transport, "open", open_transport)
    monkeypatch.setattr(transport, "close", close_transport)
    hand = HandDevice(model="O6", side="left", transport=transport)
    pool_keys = TRANSPORT_POOL.active_keys()

    for _ in range(2):
        with pytest.raises(
            ValidationError, match="transport pool key must be hashable"
        ):
            hand.open()
        hand.close()
        hand.close()
        assert hand._runtime is None
        assert hand.get_health().lifecycle_state.value == "closed"

    open_transport.assert_not_called()
    close_transport.assert_not_called()
    assert not transport.dispatchers
    assert TRANSPORT_POOL.active_keys() == pool_keys


def test_binding_close_retries_after_final_transport_release_failure():
    transport = FakeTransport()
    binding = TransportBinding(
        transport,
        endpoint_id="binding-close-retry",
        fifo_capacity=4,
        join_timeout_s=1.0,
        clock=SystemClock(),
    )
    binding.open()
    generation = transport.get_health().generation
    transport.current_dispatcher.stop_error = OSError("native close failed")
    try:
        with pytest.raises(TransportError, match="native close failed"):
            binding.close()
        assert TRANSPORT_POOL.lease_count(binding._key) == 0
        assert binding._lease is not None
        assert not binding._arbiter_acquired
        binding.close()
        assert binding._lease is None
        assert binding._key not in TRANSPORT_POOL.active_keys()
        assert transport.get_health().state is TransportState.CLOSED
        assert transport.get_health().generation == generation
        assert len(transport.dispatchers) == 1
    finally:
        binding.close()


class NativeFunction:
    def __init__(self, result=0):
        self.result = result
        self.calls = 0
        self.restype = None
        self.argtypes = None

    def __call__(self, *args):
        self.calls += 1
        return self.result


class FailingNativeLibrary:
    """Keep the real CANFD backend; fail only vendor initialization/close."""

    def __init__(self, name):
        self._name = name
        for method in (
            "LibCANbus_Init",
            "LibCANbus_Exit",
            "CAN_OpenDevice",
            "CAN_CloseDevice",
            "CAN_ReadDevInfo",
            "CANFD_Init",
            "CANFD_Receive",
        ):
            setattr(self, method, NativeFunction())
        self.CAN_ScanDevice = NativeFunction(1)
        self.CANFD_Transmit = NativeFunction(1)
        self.CANFD_Init.result = 1
        self.CAN_CloseDevice.result = 1


def test_hand_close_retries_native_cleanup_after_pool_acquire_failure(
    monkeypatch, tmp_path
):
    library = FailingNativeLibrary(str(tmp_path / "failed-open.so"))
    monkeypatch.setattr(native, "_load_library", lambda path: library)
    transport = CANFDTransport()
    hand = HandDevice(model="O6", side="left", transport=transport)
    key = (type(transport), transport.pool_key)
    library_key = native._library_key(library)
    try:
        with pytest.raises(LifecycleError, match="cleanup did not complete"):
            hand.open()
        assert hand._runtime is not None
        assert TRANSPORT_POOL.lease_count(key) == 0
        assert key in TRANSPORT_POOL.active_keys()
        assert native._open_channel_counts[library_key] == 1
        close_calls = library.CAN_CloseDevice.calls
        with pytest.raises(LifecycleError):
            hand.close()
        assert library.CAN_CloseDevice.calls > close_calls
        assert library.CAN_OpenDevice.calls == 1

        library.CAN_CloseDevice.result = 0
        hand.close()
        assert key not in TRANSPORT_POOL.active_keys()
        assert native._open_channel_counts.get(library_key, 0) == 0
        assert library.CAN_OpenDevice.calls == 1
        close_calls = library.CAN_CloseDevice.calls
        hand.close()
        assert library.CAN_CloseDevice.calls == close_calls
        assert hand.get_health().lifecycle_state.value == "closed"
    finally:
        library.CAN_CloseDevice.result = 0
        hand.close()
        TRANSPORT_POOL.close_idle(key)


def test_binding_retains_lease_when_arbiter_setup_and_cleanup_fail(monkeypatch):
    transport = FakeTransport()
    binding = TransportBinding(
        transport,
        endpoint_id="failed-arbiter-open",
        fifo_capacity=4,
        join_timeout_s=1.0,
        clock=SystemClock(),
    )
    original_acquire = ARBITERS.acquire

    def fail_arbiter(*args, **kwargs):
        transport.current_dispatcher.stop_error = OSError("native close failed")
        raise RuntimeError("arbiter setup failed")

    monkeypatch.setattr(ARBITERS, "acquire", fail_arbiter)
    try:
        with pytest.raises(TransportError, match="native close failed"):
            binding.open()
        assert TRANSPORT_POOL.lease_count(binding._key) == 0
        assert binding._lease is not None
        binding.close()
        assert binding._key not in TRANSPORT_POOL.active_keys()
        assert transport.get_health().state is TransportState.CLOSED
        assert len(transport.dispatchers) == 1
    finally:
        monkeypatch.setattr(ARBITERS, "acquire", original_acquire)
        binding.close()
        TRANSPORT_POOL.close_idle(binding._key)


@pytest.mark.parametrize("recovered_owner", [False, True])
def test_failed_acquire_cleanup_respects_other_bindings_and_descriptors(
    tmp_path, recovered_owner
):
    class PartialOpenTransport(FakeTransport):
        pool_key = str(tmp_path)
        fail_open = True
        fail_close = True
        close_calls = 0

        def open(self):
            super().open()
            if self.fail_open:
                raise TransportError("partial open failed")

        def close(self):
            self.close_calls += 1
            if self.fail_close:
                raise TransportError("cleanup failed")
            super().close()

    transport = PartialOpenTransport()
    descriptor = PartialOpenTransport()

    def bind(value, endpoint):
        return TransportBinding(
            value,
            endpoint_id=endpoint,
            fifo_capacity=4,
            join_timeout_s=1.0,
            clock=SystemClock(),
        )

    first = bind(transport, "first")
    second = bind(descriptor, "second")
    try:
        with pytest.raises(TransportError, match="partial open failed"):
            first.open()
        assert first._lease is None
        if recovered_owner:
            transport.fail_open = transport.fail_close = False
            second.open()
            assert second._lease.transport is transport
            close_calls = transport.close_calls
            first.close()
            assert transport.close_calls == close_calls
            assert TRANSPORT_POOL.lease_count(first._key) == 1
            assert transport.get_health().state is TransportState.OPEN
            second.close()
        else:
            # Recover fails on the retained object, not the new descriptor.
            with pytest.raises(TransportError, match="cleanup failed"):
                second.open()
            with pytest.raises(TransportError, match="cleanup failed"):
                second.close()
            transport.fail_close = False
            second.close()
            assert len(transport.dispatchers) == 1
            first.close()
        assert not descriptor.dispatchers
        assert transport.get_health().state is TransportState.CLOSED
        assert first._key not in TRANSPORT_POOL.active_keys()
    finally:
        transport.fail_close = False
        first.close()
        second.close()
        TRANSPORT_POOL.close_idle(first._key)


def test_failed_open_keeps_actor_cleanup_retryable_after_transport_is_closed(
    monkeypatch,
):
    hand, _state, transport = make_hand()
    create_runtime = hand._runtime_factory
    runtimes = []
    cleanup_allowed = False

    def factory(**options):
        runtime = create_runtime(**options)
        runtimes.append(runtime)
        stop_actor = runtime._actor.stop

        def fail_start():
            raise RuntimeError("actor initialization failed")

        def stop(*, timeout_s):
            if not cleanup_allowed:
                raise RuntimeError("actor cleanup failed")
            stop_actor(timeout_s=timeout_s)

        monkeypatch.setattr(runtime._actor, "start", fail_start)
        monkeypatch.setattr(runtime._actor, "stop", stop)
        return runtime

    monkeypatch.setattr(hand, "_runtime_factory", factory)
    try:
        with pytest.raises(LifecycleError, match="cleanup did not complete"):
            hand.open()
        assert hand._runtime is runtimes[0]
        assert transport.get_health().state is TransportState.CLOSED
        with pytest.raises(LifecycleError, match="did not close cleanly"):
            hand.close()
        cleanup_allowed = True
        hand.close()
        assert hand.get_health().lifecycle_state.value == "closed"
        assert not hand._runtime._actor.alive
        assert len(transport.dispatchers) == 1
    finally:
        cleanup_allowed = True
        hand.close()
