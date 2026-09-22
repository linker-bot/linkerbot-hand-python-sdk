"""LHT10 setting echoes must never become same-opcode live measurements."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from threading import Event
from time import monotonic

import pytest

from linkerbot_hand import (
    AckSupport,
    CommandSemantic,
    CommandStage,
    DataOrigin,
    ErrorCode,
    HandDevice,
    HandSide,
    ReadMode,
    TelemetrySemantic,
    UnsupportedOperationError,
    ValidationError,
    get_declared_capabilities,
)
from linkerbot_hand.protocols.l20lite.transactions import L20LiteProtocolBackend
from linkerbot_hand.runtime._transaction_runner import TransactionRunner

from .classic_can_fake import ScriptedClassicCANTransport, make_script, wait_until

CASES = (
    ("speeds", "configured_speeds", "speeds", "measured_speeds", (5, 6)),
    ("current_limits", "current_limits", "currents", "currents", (2, 3)),
)


def make_hand(side=HandSide.LEFT):
    script = make_script("L20Lite", side)
    for opcode in (2, 3, 5, 6):
        script.readings[opcode] = (3,) * 5
    transport = ScriptedClassicCANTransport(script)
    hand = HandDevice(model="L20Lite", side=side, transport=transport)
    return hand, script, transport


@pytest.mark.parametrize("setting,cached,measured,attribute,opcodes", CASES)
@pytest.mark.parametrize("side", (HandSide.LEFT, HandSide.RIGHT))
def test_echo_and_measurement_have_separate_values_sources_and_queries(
    setting, cached, measured, attribute, opcodes, side
):
    hand, script, _transport = make_hand(side)
    reported = tuple(range(180, 190))
    script.bad_command_echoes.update(
        {opcodes[0]: reported[:5], opcodes[1]: reported[5:]}
    )
    with closing(hand.open()):
        get_cache = getattr(hand, "get_" + cached)
        get_measured = getattr(hand, "get_" + measured)
        command = hand.get_capabilities().command(CommandSemantic(setting))
        channel = hand.get_capabilities().telemetry_channel(TelemetrySemantic(cached))
        assert command.ack_support is AckSupport.SUPPORTED
        assert not command.returns_current_state
        assert channel.read_mode is ReadMode.COMMAND_RESPONSE
        assert not channel.device_queried and not channel.device_measured
        assert channel.recommended_hz == channel.estimated_transaction_s == 0
        before = len(script.sent_payloads)
        with pytest.raises(UnsupportedOperationError):
            get_cache(timeout=1)
        assert len(script.sent_payloads) == before

        actual = get_measured(timeout=1)
        assert actual.raw == (3,) * 10
        handle = getattr(hand, "set_" + setting)((200,) * 10)
        assert handle.wait(1) is CommandStage.ACKNOWLEDGED
        assert handle.sent_frames == 2
        snapshot = hand.get_snapshot()
        assert getattr(snapshot, attribute).meta.sequence == actual.meta.sequence
        assert getattr(snapshot, attribute).raw == (3,) * 10
        before = len(script.sent_payloads)
        feedback = get_cache(timeout=1)
        assert feedback.raw == reported
        assert feedback.meta.origin is DataOrigin.DEVICE_CONFIG_RESPONSE
        assert feedback.meta.valid
        assert len(script.sent_payloads) == before
        assert script.settings[opcodes[0]] == (200,) * 5

        actual = get_measured(timeout=1)
        assert actual.raw == (3,) * 10
        assert actual.meta.origin is DataOrigin.DEVICE_RESPONSE
        assert script.sent_payloads[before:] == [bytes((opcode,)) for opcode in opcodes]
        hand.recover(timeout=1)
        with pytest.raises(UnsupportedOperationError):
            get_cache(timeout=1)


@pytest.mark.parametrize("setting,cached,measured,attribute,opcodes", CASES)
def test_delayed_echoes_complete_write_before_the_live_query(
    setting, cached, measured, attribute, opcodes
):
    hand, script, transport = make_hand()
    script.dropped_command_responses.update(opcodes)
    with closing(hand.open()):
        handle = getattr(hand, "set_" + setting)((200,) * 10)
        wait_until(lambda: handle.stage is CommandStage.DISPATCHED)
        # These are setting replies delayed until after the batch was sent.
        # Reversed groups must still form the setting vector, not a measurement.
        for opcode in reversed(opcodes):
            transport.emit(script.frame(bytes((opcode,)) + bytes((190,)) * 5))
        assert handle.wait(1) is CommandStage.ACKNOWLEDGED
        assert getattr(hand, "get_" + cached)(timeout=1).raw == (190,) * 10
        measured_sample = getattr(hand, "get_" + measured)(timeout=1)
        assert measured_sample.raw == (3,) * 10
        assert measured_sample.meta.origin is DataOrigin.DEVICE_RESPONSE


@pytest.mark.parametrize("setting,cached,measured,attribute,opcodes", CASES)
@pytest.mark.parametrize("failure", ("missing", "malformed", "duplicate"))
@pytest.mark.parametrize("failed_group", (0, 1))
def test_failed_setting_quarantines_late_groups_before_same_opcode_read(
    setting, cached, measured, attribute, opcodes, failure, failed_group, monkeypatch
):
    hand, script, transport = make_hand()
    with closing(hand.open()):
        setter = getattr(hand, "set_" + setting)
        assert setter((100,) * 10).wait(1) is CommandStage.ACKNOWLEDGED
        old_cache = getattr(hand, "get_" + cached)(timeout=1)
        if failure == "missing":
            script.dropped_command_responses.add(opcodes[failed_group])
        elif failure == "malformed":
            script.bad_command_echoes[opcodes[failed_group]] = (200,) * 4
        else:
            # Both writes are dispatched before we deliver a conflicting
            # duplicate for either group, followed by the remaining group.
            script.dropped_command_responses.update(opcodes)
            original_send = transport.send

            def send(message):
                data = bytes(message.data)
                receipt = original_send(message)
                if len(data) > 1 and data[0] == opcodes[-1]:
                    opcode = opcodes[failed_group]
                    transport.emit(script.frame(bytes((opcode,)) + bytes((200,)) * 5))
                    transport.emit(script.frame(bytes((opcode,)) + bytes((201,)) * 5))
                    other = opcodes[1 - failed_group]
                    transport.emit(script.frame(bytes((other,)) + bytes((200,)) * 5))
                return receipt

            monkeypatch.setattr(transport, "send", send)
        failed = setter((200,) * 10)
        assert failed.wait(1) is CommandStage.FAILED
        assert failed.error.code is (
            ErrorCode.TIMEOUT if failure == "missing" else ErrorCode.PROTOCOL
        )
        feedback = getattr(hand, "get_" + cached)(timeout=1)
        assert feedback.raw == old_cache.raw
        assert feedback.meta.sequence == old_cache.meta.sequence

        guard_entered = Event()
        guard_deadlines = []
        original_wait = TransactionRunner._wait_for_quarantine

        def wait_for_quarantine(runner, correlation_key, **kwargs):
            if correlation_key in runner._quarantined:
                guard_deadlines.append(
                    runner._quarantined[correlation_key].deadline_at_s
                )
                guard_entered.set()
            return original_wait(runner, correlation_key, **kwargs)

        monkeypatch.setattr(
            TransactionRunner, "_wait_for_quarantine", wait_for_quarantine
        )
        original_send_after_failure = transport.send

        def send_after_guard(message):
            if len(message.data) == 1 and message.data[0] in opcodes:
                assert guard_deadlines and monotonic() >= guard_deadlines[0]
            return original_send_after_failure(message)

        monkeypatch.setattr(transport, "send", send_after_guard)
        before = {opcode: script.read_counts[opcode] for opcode in opcodes}
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(getattr(hand, "get_" + measured), timeout=1)
            assert guard_entered.wait(1)
            assert all(
                script.read_counts[opcode] == before[opcode] for opcode in opcodes
            )
            # Valid, malformed and conflicting old echoes cannot remove this
            # quarantine early or poison the fresh collector.
            for opcode in opcodes:
                transport.emit(script.frame(bytes((opcode, 200))))
                transport.emit(script.frame(bytes((opcode,)) + bytes((200,)) * 5))
                transport.emit(script.frame(bytes((opcode,)) + bytes((201,)) * 5))
            actual = future.result(timeout=1)
        assert actual.raw == (3,) * 10
        assert actual.meta.origin is DataOrigin.DEVICE_RESPONSE
        assert hand.get_health().connected


@pytest.mark.parametrize("setting,cached,measured,attribute,opcodes", CASES)
def test_missing_first_echo_leaves_configuration_unavailable(
    setting, cached, measured, attribute, opcodes
):
    hand, script, _transport = make_hand()
    script.dropped_command_responses.add(opcodes[-1])
    with closing(hand.open()):
        assert (
            getattr(hand, "set_" + setting)((200,) * 10).wait(1) is CommandStage.FAILED
        )
        with pytest.raises(UnsupportedOperationError):
            getattr(hand, "get_" + cached)(timeout=1)
        assert not getattr(hand.get_snapshot(), cached).meta.valid
        assert getattr(hand, "get_" + measured)(timeout=1).raw == (3,) * 10


@pytest.mark.parametrize("setting,cached,measured,attribute,opcodes", CASES)
def test_repeated_read_write_cycles_do_not_cross_contaminate(
    setting, cached, measured, attribute, opcodes
):
    hand, script, _transport = make_hand()
    with closing(hand.open()):
        for value in range(10, 15):
            for opcode in opcodes:
                script.readings[opcode] = (value,) * 5
            assert (
                getattr(hand, "set_" + setting)((200,) * 10).wait(1)
                is CommandStage.ACKNOWLEDGED
            )
            assert getattr(hand, "get_" + cached)(timeout=1).raw == (200,) * 10
            assert getattr(hand, "get_" + measured)(timeout=1).raw == (value,) * 10


def test_failure_consumer_matches_only_its_opcodes_and_has_a_bounded_guard():
    caps = get_declared_capabilities("L20Lite", side="left")
    tx = L20LiteProtocolBackend(HandSide.LEFT).command_transaction(
        CommandSemantic.CURRENT_LIMITS, (200,) * 10, caps
    )
    script = make_script("L20Lite", HandSide.LEFT)
    assert tx.late_response_guard_s == 0.1
    for data in (b"\x02", b"\x03\x00", bytes((2,)) + bytes((200,)) * 5):
        progress = tx.late_response_consumer(script.frame(data))
        assert progress.matched and not progress.complete
        assert progress.result is None and not progress.publications
    assert not tx.late_response_consumer(script.frame(b"\x05\x00")).matched
    for replacement in (False, 1):
        with pytest.raises(ValidationError):
            replace(tx, late_response_consumer=replacement)
    with pytest.raises(ValidationError):
        replace(tx, late_response_guard_s=0)


def test_l20_keeps_its_existing_dispatch_only_setting_contract():
    from linkerbot_hand.protocols.l20.transactions import L20ProtocolBackend

    caps = get_declared_capabilities("L20", side="left")
    backend = L20ProtocolBackend(HandSide.LEFT)
    for semantic in (CommandSemantic.SPEEDS, CommandSemantic.CURRENT_LIMITS):
        assert not backend.command_transaction(
            semantic, (200,) * 16, caps
        ).expects_response
        assert caps.command(semantic).ack_support is not AckSupport.SUPPORTED
