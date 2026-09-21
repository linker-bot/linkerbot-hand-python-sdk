"""Configured response windows exercised through production CAN/CAN FD profiles."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator

import can
import pytest

from linkerbot_hand.api import (
    CommandStage,
    DeviceConfig,
    HandDevice,
    HandSide,
    PollingPlan,
    TimeoutError,
    ValidationError,
)
from linkerbot_hand.protocols.l30.framing import parse_can_id as parse_l30_id
from linkerbot_hand.protocols.o20.codec import parse_can_id as parse_o20_id
from linkerbot_transport.canfd import CANFDMessage
from linkerbot_transport.testing import FakeTransport

from .classic_can_fake import ScriptedClassicCANTransport, make_script, wait_until
from .l30_canfd_fake import L30DeviceScript, ScriptedL30Transport
from .o20_canfd_fake import O20DeviceScript, ScriptedO20Transport
from .o30_canfd_fake import O30DeviceScript, ScriptedO30Transport

_MODELS = ("L6", "O6", "L20Lite", "L20", "O20", "L30", "O30")


class DelayedReplies:
    """Delay only selected replies, preserving their transport generation."""

    def __init__(self, transport: FakeTransport) -> None:
        self.transport = transport
        self.matches: Callable[[can.Message | CANFDMessage], bool] = lambda _message: (
            False
        )
        self.delay_s = 0.15
        self._timers: list[threading.Timer] = []

    def emit(self, message: can.Message | CANFDMessage) -> None:
        dispatcher = self.transport.current_dispatcher
        if self.matches(message):
            timer = threading.Timer(self.delay_s, dispatcher.emit, args=(message,))
            timer.daemon = True
            self._timers.append(timer)
            timer.start()
        else:
            dispatcher.emit(message)

    def close(self) -> None:
        for timer in self._timers:
            timer.cancel()
            timer.join(timeout=1)


@pytest.fixture
def devices(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable]:
    opened: list[tuple[HandDevice, DelayedReplies]] = []

    def make(
        model: str = "L6",
        *,
        config: DeviceConfig | None = None,
        identity_layout: str = "deployed",
    ) -> tuple[HandDevice, DelayedReplies]:
        side = HandSide.RIGHT if model == "O30" else HandSide.LEFT
        transport: FakeTransport
        if model in _MODELS[:4]:
            transport = ScriptedClassicCANTransport(make_script(model, side))
        elif model == "O20":
            transport = ScriptedO20Transport(O20DeviceScript(side))
        elif model == "L30":
            transport = ScriptedL30Transport(L30DeviceScript())
        else:
            transport = ScriptedO30Transport(
                O30DeviceScript(identity_layout=identity_layout)
            )
        replies = DelayedReplies(transport)
        monkeypatch.setattr(transport, "emit", replies.emit)
        hand = HandDevice(
            model=model,
            side=None if model == "L30" else side,
            transport=transport,
            config=config,
        )
        opened.append((hand, replies))
        return hand, replies

    yield make
    for hand, replies in opened:
        # Stop all producers before tearing down the endpoint.
        hand.close()
        replies.close()


def _last_identity_reply(model: str, message: can.Message | CANFDMessage) -> bool:
    if model in _MODELS[:4]:
        return bytes(message.data[:1]) == b"\xc4"
    if model == "O20":
        _, register, write = parse_o20_id(message.arbitration_id)
        return register == 0 and not write
    if model == "L30":
        frame = parse_l30_id(message.arbitration_id)
        return frame.parent == 3 and frame.subcommand == 3
    return (message.data[0] & 0x7F) == 0x41 and message.data[1] == 0xD9


@pytest.mark.parametrize("model", _MODELS)
@pytest.mark.parametrize("operation", ("open", "recover", "get_device_info"))
def test_identity_window_applies_to_all_entrypoints_and_models(
    devices: Callable, model: str, operation: str
) -> None:
    hand, replies = devices(model, config=DeviceConfig(identity_timeout_s=0.4))
    if operation != "open":
        hand.open()
    replies.matches = lambda message: _last_identity_reply(model, message)

    if operation == "open":
        hand.open()
    else:
        getattr(hand, operation)(timeout=1)

    assert hand.get_health().connected
    assert hand.get_snapshot().device_info is not None
    assert hand.get_capabilities().identity.verified


@pytest.mark.parametrize("model", ("L6", "L30", "O30"))
def test_identity_override_can_shorten_the_protocol_default(
    devices: Callable, model: str
) -> None:
    hand, replies = devices(model, config=DeviceConfig(identity_timeout_s=0.03))
    hand.open()
    replies.matches = lambda message: _last_identity_reply(model, message)
    replies.delay_s = 0.08

    with pytest.raises(TimeoutError, match="protocol response"):
        hand.get_device_info(timeout=1)

    assert hand.get_health().connected


@pytest.mark.parametrize("identity_layout", ("deployed", "documented"))
def test_open_budget_covers_every_dynamic_identity_stage(
    devices: Callable, identity_layout: str
) -> None:
    config = DeviceConfig(
        identity_timeout_s=0.4,
        transaction_timeout_s=0.02,
        shutdown_timeout_s=0.2,
    )
    hand, replies = devices("O30", config=config, identity_layout=identity_layout)
    replies.matches = lambda message: (message.data[0] & 0x7F) == 0x41
    old_budget = (
        config.identity_timeout_s
        + (len(hand.get_capabilities().telemetry) + 1) * 0.02
        + config.shutdown_timeout_s
    )

    started = time.monotonic()
    hand.open()

    assert time.monotonic() - started > old_budget
    assert hand.get_health().connected


def test_automatic_reconnect_uses_identity_override(devices: Callable) -> None:
    hand, replies = devices(
        config=DeviceConfig(identity_timeout_s=0.4, auto_reconnect=True)
    )
    hand.open()
    replies.matches = lambda message: _last_identity_reply("L6", message)
    replies.transport.current_dispatcher.disconnect()

    wait_until(lambda: hand.get_health().connection_generation == 2, timeout_s=1.5)

    assert hand.get_health().connected


def test_incomplete_identity_still_fails_without_retries(devices: Callable) -> None:
    hand, replies = devices(config=DeviceConfig(identity_timeout_s=0.03))
    replies.matches = lambda message: _last_identity_reply("L6", message)
    replies.delay_s = 0.5

    with pytest.raises(TimeoutError, match="protocol response"):
        hand.open()

    assert not hand.get_health().connected
    transport = replies.transport
    assert isinstance(transport, ScriptedClassicCANTransport)
    assert transport.script.identity_queries == 1


def _angle_reply(model: str, message: can.Message | CANFDMessage) -> bool:
    if model in _MODELS[:4]:
        opcode = {"L6": 0x01, "O6": 0x01, "L20Lite": 0x04, "L20": 0x45}[model]
        return message.data[0] == opcode
    if model == "O20":
        _, register, write = parse_o20_id(message.arbitration_id)
        return register == 3 and not write
    if model == "L30":
        frame = parse_l30_id(message.arbitration_id)
        return frame.parent == 5 and frame.subcommand == 1
    return (message.data[0] & 0x7F) == 1


@pytest.mark.parametrize("model", _MODELS)
@pytest.mark.parametrize("operation", ("open", "recover", "get_angles", "poll"))
def test_ordinary_override_applies_to_baseline_getters_and_polling(
    devices: Callable, model: str, operation: str
) -> None:
    hand, replies = devices(
        model,
        config=DeviceConfig(identity_timeout_s=0.05, transaction_timeout_s=0.4),
    )
    if operation != "open":
        hand.open()
    start_sequence = hand.get_snapshot().angles.meta.sequence
    replies.matches = lambda message: _angle_reply(model, message)

    if operation == "open":
        hand.open()
    elif operation == "poll":
        hand.start_polling(PollingPlan(angles_hz=5))
        wait_until(
            lambda: hand.get_snapshot().angles.meta.sequence > start_sequence,
            timeout_s=1,
        )
        hand.stop_polling()
    else:
        getattr(hand, operation)(timeout=1)

    assert hand.get_snapshot().angles.meta.valid
    if operation == "recover":
        assert hand.get_snapshot().angles.meta.connection_generation == 2
    else:
        assert hand.get_snapshot().angles.meta.sequence > start_sequence
    assert hand.get_health().connected


@pytest.mark.parametrize("model", ("L6", "O6", "O30"))
def test_ordinary_override_applies_to_configuration_acknowledgement(
    devices: Callable, model: str
) -> None:
    hand, replies = devices(model, config=DeviceConfig(transaction_timeout_s=0.4))
    hand.open()
    replies.matches = (
        (lambda message: (message.data[0] & 0x7F) == 4)
        if model == "O30"
        else (lambda message: message.data[0] == 2)
    )

    started = time.monotonic()
    handle = hand.set_current_limits((20,) * len(hand.get_capabilities().joints))

    assert handle.wait(timeout=1) is CommandStage.ACKNOWLEDGED
    assert time.monotonic() - started >= replies.delay_s
    assert handle.error is None


@pytest.mark.parametrize("model", ("O20", "L30"))
def test_dispatch_only_commands_do_not_gain_a_response_wait(
    devices: Callable, model: str
) -> None:
    hand, _ = devices(model, config=DeviceConfig(transaction_timeout_s=1.0))
    hand.open()

    handle = hand.set_angles((0,) * len(hand.get_capabilities().joints))

    assert handle.wait(timeout=0.3) is CommandStage.DISPATCHED
    assert handle.error is None


def test_default_preserves_short_protocol_response_window(devices: Callable) -> None:
    hand, replies = devices()
    hand.open()
    assert DeviceConfig().transaction_timeout_s is None
    replies.matches = lambda message: _angle_reply("L6", message)
    replies.delay_s = 0.12

    with pytest.raises(TimeoutError, match="protocol response"):
        hand.get_angles(timeout=1)

    assert hand.get_health().connected


@pytest.mark.parametrize("override", (None, 0.4))
def test_tactile_final_stage_keeps_default_or_uses_override(
    devices: Callable, override: float | None
) -> None:
    hand, replies = devices(config=DeviceConfig(transaction_timeout_s=override))
    hand.open()
    # The scripted device emits rows in reverse order: B5/row 0 is the final
    # fragment of the last finger, after four response-gated stages succeeded.
    replies.matches = lambda message: bytes(message.data[:2]) == b"\xb5\x00"
    replies.delay_s = 0.12 if override is None else 0.2

    sample = hand.get_force("fn", timeout=2)

    assert sample.meta.valid
    assert len(sample.tactile) == 5


def test_explicit_ordinary_window_can_shorten_protocol_default(
    devices: Callable,
) -> None:
    hand, replies = devices("L20Lite", config=DeviceConfig(transaction_timeout_s=0.03))
    hand.open()
    replies.matches = lambda message: _angle_reply("L20Lite", message)
    replies.delay_s = 0.08

    with pytest.raises(TimeoutError, match="protocol response"):
        hand.get_angles(timeout=1)


@pytest.mark.parametrize("value", (True, False, 0, -1, float("nan"), float("inf"), "1"))
def test_ordinary_override_rejects_invalid_values(value: object) -> None:
    with pytest.raises(ValidationError, match="transaction_timeout_s"):
        DeviceConfig(transaction_timeout_s=value)  # ty: ignore[invalid-argument-type]
