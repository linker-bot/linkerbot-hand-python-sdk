"""Reusable HandDevice factories for actor-runtime contract tests."""

from __future__ import annotations

import threading
from collections.abc import Callable

from linkerbot_hand.api import DeviceConfig, HandDevice, HandSide
from linkerbot_hand.capabilities.registry import get_declared_capabilities
from linkerbot_hand.capabilities.types import DeviceCapabilities
from linkerbot_hand.runtime.device_runtime import DeviceRuntime

from .fakes import FakeProtocolBackend, FakeProtocolState, ScriptedFakeTransport


def make_hand(
    *,
    config: DeviceConfig | None = None,
    capabilities: DeviceCapabilities | None = None,
    route_keys: tuple[str, ...] | None = None,
) -> tuple[HandDevice, FakeProtocolState, ScriptedFakeTransport]:
    """Build one unopened fake-backed hand without touching transport I/O."""

    if capabilities is None:
        capabilities = get_declared_capabilities("l6", side="left")
    state = FakeProtocolState(capabilities)
    backend = FakeProtocolBackend(state, route_keys=route_keys)
    transport = ScriptedFakeTransport(backend)
    side = (
        None
        if capabilities.side is HandSide.NOT_APPLICABLE
        else capabilities.side.value
    )
    hand = HandDevice(
        model=capabilities.model,
        side=side,
        transport=transport,
        config=config,
        protocol_backend=backend,
    )
    return hand, state, transport


def make_shared_hands(
    *,
    config: DeviceConfig | None = None,
) -> tuple[
    HandDevice,
    FakeProtocolState,
    HandDevice,
    FakeProtocolState,
    ScriptedFakeTransport,
]:
    """Build unopened L6 and O6 endpoints sharing one physical transport."""

    l6_state = FakeProtocolState(get_declared_capabilities("l6", side="left"))
    o6_state = FakeProtocolState(get_declared_capabilities("o6", side="right"))
    l6_backend = FakeProtocolBackend(
        l6_state,
        endpoint_id="l6-left",
        route_key="l6-left-rx",
    )
    o6_backend = FakeProtocolBackend(
        o6_state,
        endpoint_id="o6-right",
        route_key="o6-right-rx",
    )
    transport = ScriptedFakeTransport(l6_backend, o6_backend)
    l6 = HandDevice(
        model="l6",
        side="left",
        transport=transport,
        config=config,
        protocol_backend=l6_backend,
    )
    o6 = HandDevice(
        model="o6",
        side="right",
        transport=transport,
        config=config,
        protocol_backend=o6_backend,
    )
    return l6, l6_state, o6, o6_state, transport


def make_duplicate_endpoint_hands() -> tuple[
    HandDevice,
    HandDevice,
    FakeProtocolState,
    ScriptedFakeTransport,
]:
    """Build two runtimes that incorrectly target the same physical endpoint."""

    state = FakeProtocolState(get_declared_capabilities("l6", side="left"))
    backend = FakeProtocolBackend(state)
    transport = ScriptedFakeTransport(backend)
    options = {
        "model": "l6",
        "side": "left",
        "transport": transport,
        "protocol_backend": backend,
    }
    first = HandDevice(**options)  # ty: ignore[invalid-argument-type]
    duplicate = HandDevice(**options)  # ty: ignore[invalid-argument-type]
    return first, duplicate, state, transport


def opened_runtime(hand: HandDevice) -> DeviceRuntime:
    """Return the concrete runtime from an already-open test device."""

    runtime = hand._runtime
    if not isinstance(runtime, DeviceRuntime):
        raise TypeError("hand must be open before accessing its test runtime")
    return runtime


def hold_next_idle_deadline_check(
    hand: HandDevice,
) -> tuple[threading.Event, threading.Event, Callable[[], None]]:
    """Pause one actor deadline read to reproduce a pre-wait wake race."""

    runtime = opened_runtime(hand)
    entered = threading.Event()
    release = threading.Event()
    polling = runtime._polling
    original = polling.next_deadline

    def blocked_next_deadline() -> float | None:
        result = original()
        if not entered.is_set():
            entered.set()
            if not release.wait(timeout=1):
                raise RuntimeError("test did not release idle deadline check")
        return result

    polling.next_deadline = blocked_next_deadline  # ty: ignore[invalid-assignment]
    runtime._wake.set()

    def restore() -> None:
        polling.next_deadline = original  # ty: ignore[invalid-assignment]

    return entered, release, restore


__all__ = [
    "hold_next_idle_deadline_check",
    "make_duplicate_endpoint_hands",
    "make_hand",
    "make_shared_hands",
    "opened_runtime",
]
