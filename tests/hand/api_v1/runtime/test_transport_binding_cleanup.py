"""Hand bindings retry released lease cleanup without reopening transports."""

import pytest

from linkerbot_hand.runtime._clock import SystemClock
from linkerbot_hand.runtime._transport_arbiter import TRANSPORT_POOL
from linkerbot_hand.runtime._transport_binding import TransportBinding
from linkerbot_transport import TransportState
from linkerbot_transport.errors import TransportError
from linkerbot_transport.testing import FakeTransport


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
