"""L20-family optional reads must not repeatedly stall a working connection."""

from __future__ import annotations

import pytest

from linkerbot_hand import TelemetrySemantic
from linkerbot_hand.api import (
    CommandStage,
    HandDevice,
    HandSide,
    PollingPlan,
    ProtocolError,
    TimeoutError,
    UnsupportedFirmwareError,
)

from .classic_can_fake import ScriptedClassicCANTransport, make_script, wait_until


@pytest.fixture(params=("L20", "L20Lite"))
def device(request):
    script = make_script(request.param, HandSide.LEFT)
    # No minimum firmware version is involved in runtime support detection.
    script.firmware = (0, 0, 1)
    transport = ScriptedClassicCANTransport(script)
    hand = HandDevice(model=script.model, side="left", transport=transport)
    hand.open()
    try:
        yield hand, transport
    finally:
        hand.close()


def _opcode(model: str, semantic: TelemetrySemantic) -> int:
    # Drop only the final fragment/finger: a partial reply is not a sample.
    return {
        "L20": {
            TelemetrySemantic.MEASURED_SPEEDS: 0x4D,
            TelemetrySemantic.TEMPERATURES: 0x65,
            TelemetrySemantic.FORCE: 0xB5,
        },
        "L20Lite": {
            TelemetrySemantic.MEASURED_SPEEDS: 0x06,
            TelemetrySemantic.TEMPERATURES: 0x34,
            TelemetrySemantic.FORCE: 0xB5,
        },
    }[model][semantic]


@pytest.mark.parametrize(
    ("semantic", "getter_name"),
    (
        (TelemetrySemantic.MEASURED_SPEEDS, "get_speeds"),
        (TelemetrySemantic.TEMPERATURES, "get_temperatures"),
        (TelemetrySemantic.FORCE, "get_force"),
    ),
)
def test_missing_fragment_disables_only_the_unsupported_read(
    device, semantic, getter_name, caplog
) -> None:
    hand, transport = device
    opcode = _opcode(transport.script.model, semantic)
    transport.script.unsupported_queries.add(opcode)
    getter = getattr(hand, getter_name)
    if semantic is TelemetrySemantic.FORCE:
        getter = lambda *, timeout: hand.get_force("fn", timeout=timeout)
    generation = hand.get_health().connection_generation

    for _ in range(2):
        with pytest.raises(TimeoutError):
            getter(timeout=1)
    with pytest.raises(UnsupportedFirmwareError, match=semantic.value):
        getter(timeout=1)

    payloads = tuple(transport.script.sent_payloads)
    with pytest.raises(UnsupportedFirmwareError, match=getter_name):
        getter(timeout=1)
    assert tuple(transport.script.sent_payloads) == payloads
    capabilities = hand.get_capabilities()
    assert capabilities.identity.firmware == "0.0.1"
    assert capabilities.firmware_unsupported_telemetry == (semantic,)
    assert capabilities.telemetry_channel(semantic) is not None
    assert not capabilities.telemetry_supported(semantic)
    assert hand.get_health().connected
    assert hand.get_health().connection_generation == generation
    assert sum("disabling this query" in r.message for r in caplog.records) == 1

    target = hand.get_angles(timeout=1).raw
    assert hand.set_angles(target).wait(timeout=1) is CommandStage.DISPATCHED
    assert hand.get_angles(timeout=1).meta.valid

    # A new connection probes again instead of retaining the old diagnosis.
    transport.script.unsupported_queries.clear()
    hand.recover(timeout=1)
    assert not hand.get_capabilities().firmware_unsupported_telemetry
    assert getter(timeout=1).meta.valid


@pytest.mark.parametrize(
    "semantic",
    (
        TelemetrySemantic.MEASURED_SPEEDS,
        TelemetrySemantic.TEMPERATURES,
    ),
)
def test_empty_vector_reply_degrades_without_publishing_partial_data(
    device, semantic
) -> None:
    hand, transport = device
    opcode = _opcode(transport.script.model, semantic)
    transport.script.malformed_query_responses[opcode] = bytes((opcode,))
    getter = (
        hand.get_speeds
        if semantic is TelemetrySemantic.MEASURED_SPEEDS
        else hand.get_temperatures
    )
    for _ in range(2):
        with pytest.raises(ProtocolError):
            getter(timeout=1)
    with pytest.raises(UnsupportedFirmwareError, match=semantic.value):
        getter(timeout=1)
    sample = getattr(hand.get_snapshot(), semantic.value)
    assert sample is not None and not sample.meta.valid
    assert sample.meta.sequence == 0
    assert hand.get_health().connected


def test_polling_stops_unsupported_temperature_frames_and_keeps_angles(device) -> None:
    hand, transport = device
    opcode = _opcode(transport.script.model, TelemetrySemantic.TEMPERATURES)
    transport.script.unsupported_queries.add(opcode)
    hand.start_polling(PollingPlan(angles_hz=30, temperature_hz=30))
    wait_until(
        lambda: (
            not hand.get_capabilities().telemetry_supported(
                TelemetrySemantic.TEMPERATURES
            )
        ),
        timeout_s=2,
    )
    sequence = hand.get_snapshot().angles.meta.sequence
    wait_until(lambda: hand.get_snapshot().angles.meta.sequence >= sequence + 3)
    assert transport.script.read_counts[opcode] == 3
    health = hand.get_health()
    channel = next(c for c in health.channels if c.channel == "temperatures")
    assert channel.requested_hz == 0
    assert channel.timeouts == 3
    assert health.connected
    hand.stop_polling()


def test_previously_successful_optional_read_stays_supported_after_timeouts(
    device,
) -> None:
    hand, transport = device
    assert hand.get_speeds(timeout=1).meta.valid
    opcode = _opcode(transport.script.model, TelemetrySemantic.MEASURED_SPEEDS)
    transport.script.unsupported_queries.add(opcode)
    for _ in range(3):
        with pytest.raises(TimeoutError):
            hand.get_speeds(timeout=1)
    assert not hand.get_capabilities().firmware_unsupported_telemetry
    transport.script.unsupported_queries.clear()
    assert hand.get_speeds(timeout=1).meta.valid
