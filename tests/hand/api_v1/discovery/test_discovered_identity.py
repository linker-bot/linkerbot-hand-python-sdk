"""Discovery identity must survive construction, failed opens and reconnects."""

import threading
from dataclasses import replace

import pytest

from linkerbot_hand import (
    ClosedError,
    HandSide,
    IdentityMismatchError,
    TransportKind,
    discover_hands,
)
from linkerbot_hand.protocols.l30 import codec as l30_codec
from linkerbot_transport import TransportState
from tests.hand.api_v1.runtime.classic_can_fake import (
    ScriptedClassicCANTransport,
    make_script,
    wait_until,
)
from tests.hand.api_v1.runtime.l30_canfd_fake import (
    L30DeviceScript,
    ScriptedL30Transport,
)
from tests.hand.api_v1.runtime.o20_canfd_fake import (
    O20DeviceScript,
    ScriptedO20Transport,
)
from tests.hand.api_v1.runtime.o30_canfd_fake import (
    O30DeviceScript,
    ScriptedO30Transport,
)

from .test_discovery import (
    _FAST,
    _classic_channel,
    _empty_transport,
    _fd_channel,
    _Source,
)


@pytest.fixture(autouse=True)
def no_leaked_threads():
    before = set(threading.enumerate())
    yield
    wait_until(lambda: set(threading.enumerate()) <= before)


def _discover(model="L30", **o30_options):
    if model == "L30":
        script = L30DeviceScript(side_code=0)
        factory = lambda: ScriptedL30Transport(script)
    elif model == "O20":
        script = O20DeviceScript(HandSide.LEFT)
        factory = lambda: ScriptedO20Transport(script)
    elif model == "O30":
        script = O30DeviceScript(**o30_options)
        factory = lambda: ScriptedO30Transport(script)
    else:
        script = make_script(model, HandSide.LEFT)
        factory = lambda: ScriptedClassicCANTransport(script)
    channel = _fd_channel() if model in {"L30", "O20", "O30"} else _classic_channel()
    source = _Source(
        channel,
        {TransportKind.CAN: _empty_transport, channel.kind: factory},
    )
    report = discover_hands(_FAST, source=source)
    assert not report.failures
    return script, source, report


def _build(report, batch):
    if batch:
        hands = report.create_hand_devices()
        assert len(hands) == 1
        return next(iter(hands.values()))
    return report.require_one().create_hand_device()


def _assert_closed(hand, source):
    assert not hand.get_health().connected
    for _, transport in source.created:
        assert transport.get_health().state is TransportState.CLOSED
        assert transport.get_health().route_count == 0


@pytest.mark.parametrize("batch", [False, True], ids=["single", "batch"])
@pytest.mark.parametrize("model", ["L6", "O6", "L20Lite", "L20", "L30", "O20", "O30"])
def test_replacement_is_rejected_on_every_first_open_attempt(model, batch):
    script, source, report = _discover(model)
    candidate = report.require_one()
    before = [transport.get_health().tx_frames for _, transport in source.created]
    hand = _build(report, batch)
    assert before + [0] == [
        transport.get_health().tx_frames for _, transport in source.created
    ]
    assert not hand.get_capabilities().identity.verified
    if model in {"L6", "O6", "L20Lite", "L20"}:
        field = "product_code"
        parts = script.product_code.split("-")
        parts[2] = "999"
        replacement = "-".join(parts)
    elif model == "L30":
        field, replacement = "serial_number", 999
    elif model == "O20":
        field, replacement = "serial_number", "SN-O20-OTHER"
    else:
        field, replacement = "device_uid", "OTHER-UID-O30"
    original = getattr(script, field)
    setattr(script, field, replacement)
    try:
        # Failed first opens dispose their runtime. Rebuilding it must not
        # reset the expected identity or learn the replacement's fingerprint.
        for _ in range(2):
            with pytest.raises(IdentityMismatchError) as caught:
                hand.open()
            error = caught.value
            assert error.operation == "open"
            assert error.model == model
            assert error.generation == 0
            assert repr(candidate.identity.fingerprint) in str(error)
            assert str(replacement) in str(error)
            assert f"{candidate.channel.provider}:{candidate.channel.name}" in str(
                error
            )
            _assert_closed(hand, source)
            assert not hand.get_capabilities().identity.verified
            assert hand.get_snapshot().device_info is None
            with pytest.raises(ClosedError):
                hand.set_angles((0,) * len(hand.get_capabilities().joints))
        setattr(script, field, original)
        hand.open()
        assert hand.get_health().connected
        assert (
            hand.get_snapshot().device_info.serial_number
            == candidate.identity.serial_number
        )
    finally:
        hand.close()
    _assert_closed(hand, source)


@pytest.mark.parametrize("batch", [False, True], ids=["single", "batch"])
@pytest.mark.parametrize("before_build", [False, True])
def test_l30_side_swap_fails_before_baseline_and_new_scan_can_select_right(
    batch, before_build
):
    script, source, report = _discover()
    if before_build:
        script.side_code = 1
    hand = _build(report, batch)
    script.side_code = 1
    try:
        with pytest.raises(IdentityMismatchError, match="expected.*-L-.*got.*-R-"):
            hand.open()
        assert list(report.named_candidates()) == ["l30l"]
        assert hand.get_capabilities().side is HandSide.NOT_APPLICABLE
        assert not script.command_messages
        assert all(
            parent == l30_codec.PARENT_CONFIG for parent, _ in script.read_counts
        )
        _assert_closed(hand, source)
    finally:
        hand.close()

    refreshed = discover_hands(_FAST, source=source)
    right = refreshed.create_hand_devices()["l30r"]
    try:
        right.open()
        assert "-R-" in right.get_snapshot().device_info.product_code
        assert right.get_health().connected
    finally:
        right.close()
    _assert_closed(right, source)


@pytest.mark.parametrize("operation", ["reopen", "recover", "refresh"])
def test_discovered_identity_remains_pinned_after_successful_open(operation):
    script, source, report = _discover()
    hand = report.create_hand_devices()["l30l"]
    try:
        hand.open()
        if operation == "reopen":
            hand.close()
        script.side_code = 1
        with pytest.raises(IdentityMismatchError, match="canfd-test.*-L-.*-R-"):
            if operation == "reopen":
                hand.open()
            elif operation == "recover":
                hand.recover(timeout=1)
            else:
                hand.get_device_info(timeout=1)
        wait_until(lambda: not hand.get_health().connected)
        assert not script.command_messages
    finally:
        hand.close()
    _assert_closed(hand, source)


@pytest.mark.parametrize("layout", ["documented", "deployed"])
def test_o30_firmware_change_requires_a_new_discovery_report(layout):
    script, source, report = _discover("O30", identity_layout=layout)
    hand = report.create_hand_devices()["o30r"]
    script.application_version = "1.3.0"
    try:
        with pytest.raises(IdentityMismatchError, match="expected.*1.2.0.*got.*1.3.0"):
            hand.open()
    finally:
        hand.close()
    _assert_closed(hand, source)

    refreshed = discover_hands(_FAST, source=source).create_hand_devices()["o30r"]
    try:
        refreshed.open()
        assert refreshed.get_snapshot().device_info.firmware_version.text == "1.3.0"
    finally:
        refreshed.close()
    _assert_closed(refreshed, source)


def test_partial_o30_with_explicit_side_keeps_its_available_fingerprint():
    script, source, report = _discover("O30", reported_side=None)
    assert report.create_hand_devices() == {}
    candidate = report.require_one(allow_partial=True)
    hand = candidate.create_hand_device(side="left")
    original = script.device_uid
    script.device_uid = "OTHER-UID-O30"
    try:
        with pytest.raises(IdentityMismatchError):
            hand.open()
        _assert_closed(hand, source)
        script.device_uid = original
        hand.open()
        assert hand.get_snapshot().device_info.side is HandSide.LEFT
    finally:
        hand.close()
    _assert_closed(hand, source)


def test_manually_supplied_candidate_without_fingerprint_keeps_existing_behavior():
    script, source, report = _discover()
    candidate = report.require_one()
    candidate = replace(
        candidate, identity=replace(candidate.identity, fingerprint=None)
    )
    hand = candidate.create_hand_device()
    script.serial_number = 999
    try:
        hand.open()
        assert hand.get_snapshot().device_info.serial_number == "999"
    finally:
        hand.close()
    _assert_closed(hand, source)
