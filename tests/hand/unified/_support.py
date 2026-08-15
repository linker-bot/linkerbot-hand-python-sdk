"""Test-only helpers for deterministic unified hand API acceptance tests."""

from __future__ import annotations

import threading
from dataclasses import dataclass

from linkerbot_hand import (
    DeviceIdentity,
    DispatchResult,
    HandDevice,
    HandSide,
    SessionConfig,
    get_model_capabilities,
)
from linkerbot_hand.testing import (
    FakeBackend,
    FakeBackendFactory,
    FakeBackendState,
    ManualClock,
)


@dataclass(slots=True)
class HandRig:
    device: HandDevice
    factory: FakeBackendFactory
    state: FakeBackendState
    clock: ManualClock


def make_rig(
    model: str = "L6",
    *,
    side: HandSide = HandSide.RIGHT,
    config: SessionConfig | None = None,
    clock: ManualClock | None = None,
) -> HandRig:
    capabilities = get_model_capabilities(model, side=side)
    test_clock = clock or ManualClock(wall_time=1_000.0)
    factory = FakeBackendFactory(capabilities)
    device = HandDevice(
        capabilities,
        factory,
        config=config,
        clock=test_clock,
    )
    return HandRig(device, factory, factory.state, test_clock)


class CountingFactory:
    """Factory whose invocation is observable independently of backend I/O."""

    def __init__(self, state: FakeBackendState) -> None:
        self.state = state
        self.call_count = 0

    def __call__(self) -> FakeBackend:
        self.call_count += 1
        return FakeBackend(self.state)


class BlockingOpenBackend(FakeBackend):
    """Pause a recovery generation while its backend is opening."""

    def __init__(
        self,
        state: FakeBackendState,
        entered: threading.Event,
        release: threading.Event,
    ) -> None:
        super().__init__(state)
        self._entered = entered
        self._release = release

    def open(self) -> DeviceIdentity:
        self._entered.set()
        if not self._release.wait(1.0):
            raise TimeoutError("test did not release blocked open")
        return super().open()


class BlockingRecoveryFactory:
    """Open normally once, then gate each replacement backend generation."""

    def __init__(
        self,
        state: FakeBackendState,
        entered: threading.Event,
        release: threading.Event,
    ) -> None:
        self.state = state
        self._entered = entered
        self._release = release
        self._lock = threading.Lock()
        self.call_count = 0

    def __call__(self) -> FakeBackend:
        with self._lock:
            self.call_count += 1
            call_count = self.call_count
        if call_count == 1:
            return FakeBackend(self.state)
        return BlockingOpenBackend(self.state, self._entered, self._release)


class BlockingDispatchBackend(FakeBackend):
    """Hold the worker before the transport has accepted a command."""

    def __init__(
        self,
        state: FakeBackendState,
        entered: threading.Event,
        release: threading.Event,
    ) -> None:
        super().__init__(state)
        self._entered = entered
        self._release = release

    def dispatch(self, command, *, claim_dispatch, mark_dispatched):
        self._entered.set()
        if not self._release.wait(1.0):
            raise TimeoutError("test did not release blocked dispatch")
        return super().dispatch(
            command,
            claim_dispatch=claim_dispatch,
            mark_dispatched=mark_dispatched,
        )


class InterruptibleDispatchBackend(FakeBackend):
    """Block before transport send until recover closes this backend."""

    def __init__(self, state: FakeBackendState, entered: threading.Event) -> None:
        super().__init__(state)
        self._entered = entered
        self._closed = threading.Event()

    def close(self) -> None:
        self._closed.set()
        super().close()

    def dispatch(self, command, *, claim_dispatch, mark_dispatched):
        self._entered.set()
        if not self._closed.wait(1.0):
            raise TimeoutError("recover did not close blocked backend")
        return super().dispatch(
            command,
            claim_dispatch=claim_dispatch,
            mark_dispatched=mark_dispatched,
        )


class InterruptibleRecoveryFactory:
    """Create one interruptible generation followed by ordinary fakes."""

    def __init__(self, state: FakeBackendState, entered: threading.Event) -> None:
        self.state = state
        self._entered = entered
        self._lock = threading.Lock()
        self.call_count = 0

    def __call__(self) -> FakeBackend:
        with self._lock:
            self.call_count += 1
            call_count = self.call_count
        if call_count == 1:
            return InterruptibleDispatchBackend(self.state, self._entered)
        return FakeBackend(self.state)


class PendingRecoveryCloseBackend(FakeBackend):
    """Expose the pending-recovery window between two concurrent closes."""

    def __init__(
        self,
        state: FakeBackendState,
        *,
        dispatch_entered: threading.Event,
        first_close_entered: threading.Event,
        second_close_entered: threading.Event,
        release_first_close: threading.Event,
    ) -> None:
        super().__init__(state)
        self._dispatch_entered = dispatch_entered
        self._first_close_entered = first_close_entered
        self._second_close_entered = second_close_entered
        self._release_first_close = release_first_close
        self._dispatch_release = threading.Event()
        self._close_lock = threading.Lock()
        self.close_calls = 0

    def dispatch(self, command, *, claim_dispatch, mark_dispatched):
        self._dispatch_entered.set()
        if not self._dispatch_release.wait(1.0):
            raise TimeoutError("concurrent close did not interrupt dispatch")
        return super().dispatch(
            command,
            claim_dispatch=claim_dispatch,
            mark_dispatched=mark_dispatched,
        )

    def close(self) -> None:
        with self._close_lock:
            self.close_calls += 1
            close_call = self.close_calls
        if close_call == 1:
            self._first_close_entered.set()
            if not self._release_first_close.wait(1.0):
                raise TimeoutError("test did not release recover's backend close")
            super().close()
            return
        self._second_close_entered.set()
        super().close()
        self._dispatch_release.set()


class RejectingAckBackend(FakeBackend):
    """Transport accepts the write, then reports an explicit device rejection."""

    def dispatch(self, command, *, claim_dispatch, mark_dispatched):
        if not claim_dispatch():
            return DispatchResult()
        with self.state.lock:
            self._ensure_ready_locked()
            self.state.dispatched_commands.append(command)
        mark_dispatched()
        return DispatchResult(acknowledged=False)


class MissingAckBackend(FakeBackend):
    """Transport accepts the write but receives no required device ACK."""

    def dispatch(self, command, *, claim_dispatch, mark_dispatched):
        if not claim_dispatch():
            return DispatchResult()
        with self.state.lock:
            self._ensure_ready_locked()
            self.state.dispatched_commands.append(command)
        mark_dispatched()
        return DispatchResult(acknowledged=None)


def fake_state(model: str, *, acknowledge_commands: bool = False) -> FakeBackendState:
    capabilities = get_model_capabilities(model, side=HandSide.RIGHT)
    return FakeBackendState(
        identity=DeviceIdentity(
            model=capabilities.model,
            firmware="fake-1.0",
            side=HandSide.RIGHT,
            serial_number="FAKE-0001",
        ),
        acknowledge_commands=acknowledge_commands,
    )
