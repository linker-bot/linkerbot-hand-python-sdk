"""LHT10 category queries use one frame and isolate each optional feature."""

from contextlib import closing
from time import monotonic

import can
import pytest

from linkerbot_hand import (
    HandDevice,
    HandSide,
    PollingPlan,
    ProtocolError,
    TelemetrySemantic,
    TimeoutError,
    UnsupportedFirmwareError,
    UnsupportedOperationError,
    get_declared_capabilities,
)
from linkerbot_hand.capabilities.polling import resolve_poll_rates
from linkerbot_hand.protocols.l20lite.transactions import L20LiteProtocolBackend

from .classic_can_fake import ScriptedClassicCANTransport, make_script, wait_until

CATEGORIES = (("fns", 0x20), ("fts", 0x21), ("fds", 0x22), ("px", 0x23))


@pytest.fixture(params=(HandSide.LEFT, HandSide.RIGHT))
def device(request):
    script = make_script("L20Lite", request.param)
    transport = ScriptedClassicCANTransport(script)
    hand = HandDevice(model="L20Lite", side=request.param, transport=transport)
    with closing(hand.open()):
        yield hand, script


@pytest.mark.parametrize("name,opcode", CATEGORIES)
def test_single_category_has_exactly_one_request_and_five_raw_values(
    device, name, opcode
):
    hand, script = device
    count = len(script.sent_payloads)
    sample = hand.get_force(name)
    assert script.sent_payloads[count:] == [bytes((opcode,))]
    assert sample.fn is None and sample.status("fn") == "not_read"
    assert tuple(field.name for field in sample.fields) == (name,)
    assert sample.field(name).raw == tuple(
        (value,) for value in script.readings[opcode]
    )
    assert sample.field(name).units == ("count",) * 5
    assert sample.field(name).encodings == ("u8",) * 5
    assert sample.field(name).sensor_names == (
        "thumb",
        "index",
        "middle",
        "ring",
        "pinky",
    )
    with pytest.raises(UnsupportedOperationError):
        hand.get_force("ft")
    assert len(script.sent_payloads) == count + 1


def test_default_reads_only_matrix_and_normal_summary(device):
    hand, script = device
    count = len(script.sent_payloads)
    sample = hand.get_force()
    assert sample.fn is not None and sample.fns is not None
    assert sample.fn.meta.collected_at <= sample.fns.meta.collected_at
    assert script.sent_payloads[count:] == [
        *(bytes((opcode, 0xC6)) for opcode in range(0xB1, 0xB6)),
        b"\x20",
    ]
    assert sample.fts is sample.fds is sample.px is None


def test_direction_sentinel_and_reserved_codes_are_preserved_but_invalid(device):
    hand, script = device
    script.readings[0x22] = (0, 127, 128, 254, 255)
    field = hand.get_force("fds").fds
    assert field.raw == ((0,), (127,), (128,), (254,), (255,))
    assert field.valid_values == ((True,), (True,), (False,), (False,), (False,))
    assert "359" in field.scale_note and "360" in field.scale_note


@pytest.mark.parametrize("name,opcode", CATEGORIES)
@pytest.mark.parametrize("length", [1, 5, 7, 8])
def test_wrong_length_and_unrelated_response_never_produce_a_sample(
    name, opcode, length
):
    caps = get_declared_capabilities("L20Lite", side="left")
    transaction = L20LiteProtocolBackend(HandSide.LEFT).read_transaction(
        TelemetrySemantic(f"force_{name}"),
        caps,
    )
    wrong_opcode = can.Message(
        arbitration_id=0x28, is_extended_id=False, data=bytes((0x24, 1, 2, 3, 4, 5))
    )
    assert not transaction.consume(wrong_opcode).matched
    response = can.Message(
        arbitration_id=0x28,
        is_extended_id=False,
        data=bytes((opcode,)) + bytes(length - 1),
    )
    with pytest.raises(ProtocolError, match="6 bytes"):
        transaction.consume(response)


def test_unsupported_scalar_stops_itself_only_and_reconnect_reprobes(device):
    hand, script = device
    script.unsupported_queries.add(0x20)
    hand.start_polling(
        PollingPlan(force_fn_hz=5, force_fns_hz=20, force_px_hz=10, angles_hz=20)
    )
    wait_until(
        lambda: hand.get_capabilities().force_field("fns").firmware_unsupported,
        timeout_s=2,
    )
    assert script.read_counts[0x20] == 3
    assert hand.get_health().connected
    assert hand.get_snapshot().force.status("fns") == "unsupported"
    old_sequence = hand.get_snapshot().force.px.meta.sequence
    wait_until(lambda: hand.get_snapshot().force.px.meta.sequence > old_sequence)
    assert script.read_counts[0x20] == 3
    assert hand.get_snapshot().force.fn is not None
    with pytest.raises(UnsupportedFirmwareError):
        hand.get_force("fns")
    # Defaults tolerate the already identified absent scalar.
    sample = hand.get_force()
    assert sample.fn is not None and sample.status("fns") == "unsupported"
    hand.stop_polling()
    script.unsupported_queries.clear()
    hand.recover(timeout=1)
    assert hand.get_capabilities().force_field("fns").supported
    assert hand.get_force("fns").fns.meta.valid


def test_successful_scalar_timeout_preserves_original_complete_sample(device):
    hand, script = device
    first = hand.get_force("px").px
    script.unsupported_queries.add(0x23)
    for _ in range(3):
        with pytest.raises(TimeoutError):
            hand.get_force("px", timeout=0.3)
    assert hand.get_capabilities().force_field("px").supported
    last = hand.get_snapshot().force.px
    assert last.raw is first.raw
    assert last.meta.collected_at == first.meta.collected_at
    assert last.meta.sequence == first.meta.sequence
    assert last.meta.stale and hand.get_health().connected


def test_total_timeout_does_not_restart_for_next_category(device):
    hand, script = device
    # Each unsupported category takes a 100 ms protocol timeout. The call must
    # finish at its total 50 ms deadline, without dispatching the second query.
    script.unsupported_queries.update((0x20, 0x23))
    count = len(script.sent_payloads)
    started = monotonic()
    with pytest.raises(TimeoutError):
        hand.get_force("fns", "px", timeout=0.05)
    assert monotonic() - started < 0.15
    assert script.sent_payloads[count:] == [b"\x20"]


def test_l20lite_rates_are_independent_and_never_use_per_finger_scalar_codes(device):
    hand, script = device
    rates = resolve_poll_rates(
        PollingPlan(force_hz=20, force_px_hz=5), hand.get_capabilities()
    )
    assert rates == {
        TelemetrySemantic.FORCE: 20,
        TelemetrySemantic.FORCE_FNS: 20,
        TelemetrySemantic.FORCE_PX: 5,
    }
    hand.start_polling(
        PollingPlan(force_fns_hz=30, force_fts_hz=20, force_fds_hz=10, force_px_hz=5)
    )
    wait_until(lambda: script.read_counts[0x23] >= 3)
    hand.stop_polling()
    assert (
        script.read_counts[0x20]
        > script.read_counts[0x21]
        > script.read_counts[0x22]
        > script.read_counts[0x23]
    )
    assert not any(
        payload[0] in (0x28, 0x29, 0x30, 0x31, 0x32, *range(0xB1, 0xB6))
        for payload in script.sent_payloads
    )
    assert not script.command_payloads
