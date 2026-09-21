"""Finite recovery retries never replay writes or hide structural failures."""

from __future__ import annotations

import threading
from dataclasses import replace

import pytest

from linkerbot_hand import (
    CommandStage,
    DeviceConfig,
    HandError,
    IdentityMismatchError,
    PollingPlan,
    ProtocolError,
    TimeoutError,
    ValidationError,
)
from linkerbot_hand.runtime._transaction_runner import TransactionRunner
from linkerbot_transport.errors import TimeoutError as TransportTimeoutError

from .factories import make_hand, make_shared_hands
from .fakes import wait_until


@pytest.mark.parametrize("limit", (1, 3))
def test_persistent_loss_has_finite_attempts_and_no_write_replay(monkeypatch, limit):
    hand, state, transport = make_hand(
        config=DeviceConfig(
            identity_timeout_s=0.02,
            recovery_retry_delay_s=0.02,
            recovery_max_attempts=limit,
        )
    )
    hand.open()
    hand.set_angles((20,) * 6).wait(1)
    commands = list(state.command_log)
    backend = transport.backends["fake-hand"]
    original = backend.responses_for
    attempts = []

    def respond(message):
        if message.kind == "identity_request":
            attempts.append(message)
            return ()
        return original(message)

    monkeypatch.setattr(backend, "responses_for", respond)
    try:
        with pytest.raises(
            TimeoutError, match=f"after {limit}/{limit} initialization attempts"
        ):
            hand.recover(timeout=1)
        assert len(attempts) == limit
        assert not hand.get_health().connected
        assert hand.get_health().connection_generation == 1
        assert state.command_log == commands
        assert len(transport.dispatchers) == 2
    finally:
        hand.close()


@pytest.mark.parametrize("failure", ("identity", "malformed", "send_timeout"))
def test_non_response_failures_are_not_retried(monkeypatch, failure):
    hand, state, transport = make_hand()
    hand.open()
    backend = transport.backends["fake-hand"]
    original = backend.identity_transaction
    attempts = []

    def malformed(_message):
        raise ProtocolError("bad identity structure")

    def build(capabilities):
        attempts.append(1)
        transaction = original(capabilities)
        if failure == "malformed":
            return replace(transaction, response_consumer=malformed)
        return transaction

    monkeypatch.setattr(backend, "identity_transaction", build)
    if failure == "identity":
        state.fingerprint = "replacement"
    elif failure == "send_timeout":

        def fail_send(_message):
            raise TransportTimeoutError("injected send failure")

        monkeypatch.setattr(transport, "send", fail_send)
    try:
        expected = IdentityMismatchError if failure == "identity" else HandError
        with pytest.raises(expected):
            hand.recover(timeout=1)
        assert len(attempts) == 1
        assert not hand.get_health().connected
        assert hand.get_health().connection_generation == 1
    finally:
        hand.close()


def test_close_interrupts_retry_quiet_wait(monkeypatch):
    hand, _state, transport = make_hand(
        config=DeviceConfig(identity_timeout_s=0.02, recovery_retry_delay_s=1)
    )
    hand.open()
    monkeypatch.setattr(transport.backends["fake-hand"], "responses_for", lambda _: ())
    entered = threading.Event()
    original = TransactionRunner.wait_for_recovery_quiet

    def wait_quiet(runner, quiet_s):
        entered.set()
        original(runner, quiet_s)

    monkeypatch.setattr(TransactionRunner, "wait_for_recovery_quiet", wait_quiet)
    errors = []

    def recover():
        try:
            hand.recover(timeout=2)
        except HandError as error:
            errors.append(error)

    thread = threading.Thread(target=recover)
    thread.start()
    try:
        assert entered.wait(1)
        hand.close()
        thread.join(1)
        assert not thread.is_alive()
        assert len(errors) == 1
        assert not hand.get_health().connected
        assert hand.get_health().connection_generation == 1
    finally:
        hand.close()
        thread.join(2)


def test_retry_quiet_wait_cannot_extend_the_recovery_budget(monkeypatch):
    hand, _state, transport = make_hand(
        config=DeviceConfig(
            identity_timeout_s=0.02,
            recovery_retry_delay_s=1,
            recovery_timeout_s=0.06,
        )
    )
    hand.open()
    attempts = []

    def drop(message):
        attempts.append(message)
        return ()

    monkeypatch.setattr(transport.backends["fake-hand"], "responses_for", drop)
    try:
        with pytest.raises(TimeoutError, match="recovery execution budget"):
            hand.recover(timeout=0.5)
        assert len(attempts) == 1
        assert not hand.get_health().connected
    finally:
        hand.close()


def test_retries_do_not_reset_a_shared_peer_or_replay_old_targets(monkeypatch):
    config = DeviceConfig(
        auto_reconnect=True,
        identity_timeout_s=0.02,
        recovery_retry_delay_s=0.2,
        reconnect_initial_delay_s=0.01,
    )
    hand, state, peer, _peer_state, transport = make_shared_hands(config=config)
    hand.open()
    peer.open()
    hand.start_polling(PollingPlan(angles_hz=5))
    backend = transport.backends["l6-left"]
    original = backend.responses_for
    attempts = []

    def drop_first(message):
        if message.kind == "identity_request":
            attempts.append(message)
            if len(attempts) == 1:
                return ()
        return original(message)

    monkeypatch.setattr(backend, "responses_for", drop_first)
    errors = []

    def recover():
        try:
            hand.recover(timeout=2)
        except HandError as error:
            errors.append(error)

    thread = threading.Thread(target=recover)
    thread.start()
    try:
        wait_until(lambda: peer.get_health().connection_generation == 2)
        assert peer.set_angles((10,) * 6).wait(1) is CommandStage.DISPATCHED
        thread.join(2)
        assert not thread.is_alive()
        assert not errors
        assert len(attempts) == 2
        assert len(transport.dispatchers) == 2
        assert not state.command_log
        assert hand.get_health().connected and peer.get_health().connected
        assert hand.get_health().reconnect_count == 1
        assert any(
            item.channel == "angles" and item.requested_hz == 5
            for item in hand.get_health().channels
        )
    finally:
        thread.join(2)
        hand.close()
        peer.close()


@pytest.mark.parametrize("value", (0, -1, True, 1.5, None))
def test_attempt_limit_requires_a_positive_integer(value):
    with pytest.raises(ValidationError, match="recovery_max_attempts"):
        DeviceConfig(recovery_max_attempts=value)


@pytest.mark.parametrize("value", (0, -1, True, float("nan"), float("inf")))
def test_retry_delay_requires_a_finite_positive_number(value):
    with pytest.raises(ValidationError, match="recovery_retry_delay_s"):
        DeviceConfig(recovery_retry_delay_s=value)
