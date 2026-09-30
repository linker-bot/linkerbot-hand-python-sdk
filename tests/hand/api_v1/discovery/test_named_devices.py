"""Scan-local naming and closed-device construction through the public report."""

from dataclasses import replace
from itertools import permutations
from unittest.mock import Mock

import pytest

from linkerbot_hand import (
    AmbiguousHandError,
    DeviceInfo,
    DiscoveryAddress,
    DiscoveryCandidate,
    DiscoveryChannel,
    DiscoveryError,
    DiscoveryFailure,
    DiscoveryIdentity,
    DiscoveryProblem,
    DiscoveryReport,
    DiscoveryStatus,
    HandSide,
    IncompleteIdentityError,
    NoHandFoundError,
    SampleMeta,
    TransportKind,
    TransportSpec,
    ValidationError,
    get_declared_capabilities,
)
from tests.hand.api_v1.test_hand_device_contract import SpyDependency


def _candidate(
    model="L6", side=HandSide.LEFT, channel="can0", *, provider="fake", factory=None
):
    declared = get_declared_capabilities(model, side=None if model == "L30" else side)
    caps = replace(declared, identity=replace(declared.identity, verified=True))
    wire = caps.transport.kind
    discovery_channel = DiscoveryChannel(
        provider,
        channel,
        wire,
        1_000_000,
        5_000_000 if wire is TransportKind.CAN_FD else None,
    )
    # Metadata-only fixtures. Real discovery/endpoint behavior is tested below
    # and in test_discovery.py / test_o30_addresses.py with protocol scripts.
    return DiscoveryCandidate(
        channel=discovery_channel,
        wire_protocol=wire,
        address=DiscoveryAddress(
            "o30_hop_endpoint" if model == "O30" else "test_endpoint",
            0x123,
            0x523,
            model in {"O20", "L30"},
            node_id=3 if model == "L30" else None,
        ),
        status=DiscoveryStatus.VERIFIED,
        identity=DiscoveryIdentity(model, caps.side, fingerprint=object()),
        transport=TransportSpec(
            provider,
            channel,
            wire,
            discovery_channel.nominal_bitrate,
            discovery_channel.data_bitrate,
            factory=factory if factory is not None else Mock(side_effect=SpyDependency),
            connection_key=object(),
        ),
        reported_side=side,
        capabilities=caps,
        device_info=DeviceInfo(
            model=model,
            serial_number=None,
            pcb_version=None,
            firmware_version=None,
            mechanical_version=None,
            product_code=None,
            sensor_variant=None,
            side=caps.side,
            raw_identity=None,
            meta=SampleMeta.unavailable(),
        ),
    )


def _report(*candidates, failures=()):
    return DiscoveryReport(
        channels=tuple(candidate.channel for candidate in candidates),
        candidates=candidates,
        failures=failures,
        elapsed_s=0.1,
    )


def test_mixed_hands_have_matching_candidate_and_device_keys_without_io(monkeypatch):
    from threading import Thread

    monkeypatch.setattr(Thread, "start", Mock(side_effect=AssertionError("thread")))
    factories = [Mock(side_effect=SpyDependency) for _ in range(4)]
    candidates = (
        _candidate("O6", HandSide.RIGHT, "can3", factory=factories[3]),
        _candidate("L6", HandSide.LEFT, "can0", factory=factories[0]),
        _candidate("O6", HandSide.LEFT, "can2", factory=factories[2]),
        _candidate("L6", HandSide.RIGHT, "can1", factory=factories[1]),
    )
    report = _report(*candidates)
    original_candidates = report.candidates
    named = report.named_candidates()
    assert list(named) == ["l6l", "l6r", "o6l", "o6r"]
    assert list(named.values()) == [candidates[i] for i in (1, 3, 2, 0)]
    assert all(factory.call_count == 0 for factory in factories)

    first = report.create_hand_devices()
    second = report.create_hand_devices()
    assert list(first) == list(second) == list(named)
    for name, hand in first.items():
        assert hand is not second[name]
        assert hand.get_capabilities().model == named[name].model
        assert hand.get_capabilities().side is named[name].side
        assert not hand.get_health().connected
        assert hand._transport.calls == []
        assert second[name]._transport is not hand._transport
        hand.close()
        second[name].close()
    assert all(factory.call_count == 2 for factory in factories)
    assert report.candidates is original_candidates
    named.clear()
    assert list(report.named_candidates()) == ["l6l", "l6r", "o6l", "o6r"]
    with pytest.raises(AmbiguousHandError):
        report.require_one()


@pytest.mark.parametrize(
    ("model", "left", "right"),
    [
        ("L6", "l6l", "l6r"),
        ("O6", "o6l", "o6r"),
        ("L20Lite", "l20litel", "l20liter"),
        ("L20", "l20l", "l20r"),
        ("O20", "o20l", "o20r"),
        ("L30", "l30l", "l30r"),
        ("O30", "o30l", "o30r"),
    ],
)
def test_all_models_use_physical_side_without_changing_constructor_side(
    model, left, right
):
    report = _report(
        _candidate(model, HandSide.RIGHT, "can1"),
        _candidate(model, HandSide.LEFT, "can0"),
    )
    assert list(report.named_candidates()) == [left, right]
    hands = report.create_hand_devices()
    for name, side in ((left, HandSide.LEFT), (right, HandSide.RIGHT)):
        expected = HandSide.NOT_APPLICABLE if model == "L30" else side
        assert hands[name].get_capabilities().side is expected
        hands[name].close()


def test_numbering_depends_on_endpoint_order_not_input_or_identity_object_order():
    first = _candidate(channel="can10")
    second = _candidate(channel="can2")
    third = _candidate(channel="can0", provider="other")
    right = _candidate(side=HandSide.RIGHT, channel="can3")
    expected = {"l6l1": first, "l6l2": second, "l6l3": third, "l6r": right}
    for order in permutations((third, right, second, first)):
        report = _report(*order)
        assert report.named_candidates() == expected
        assert list(report.named_candidates()) == list(expected)
        assert report.candidates == order
    assert _report(first).named_candidates() == {"l6l": first}


def test_same_channel_address_order_is_numeric_and_missing_node_is_deterministic():
    # This only exercises constructed report metadata, not supported bus topology.
    first = _candidate("L30")
    second = replace(first, address=replace(first.address, request_id=0x124))
    without_node = replace(first, address=replace(first.address, node_id=None))
    assert list(_report(second, first, without_node).named_candidates().values()) == [
        without_node,
        first,
        second,
    ]


def test_partial_candidates_and_failures_remain_visible_without_claiming_names():
    full = _candidate()
    partial = replace(
        _candidate(channel="can1"),
        status=DiscoveryStatus.PARTIAL,
        identity=DiscoveryIdentity("L6", None),
        problems=(DiscoveryProblem("missing_side", "no physical side"),),
    )
    cleanup = Mock()
    failure = DiscoveryFailure(
        "close", "transport", "close failed", "OSError", cleanup=cleanup
    )
    report = _report(partial, full, failures=(failure,))
    assert report.named_candidates() == {"l6l": full}
    assert list(report.create_hand_devices()) == ["l6l"]
    assert report.partial_candidates == (partial,)
    assert report.failures == (failure,)
    cleanup.assert_not_called()
    partial.transport.factory.assert_not_called()

    only_partial = _report(partial, failures=(failure,))
    assert only_partial.named_candidates() == only_partial.create_hand_devices() == {}
    assert only_partial.require_one(allow_partial=True) is partial
    with pytest.raises(IncompleteIdentityError):
        only_partial.require_one()


def test_empty_report_returns_empty_mappings_but_require_one_still_raises():
    report = _report()
    assert report.named_candidates() == report.create_hand_devices() == {}
    with pytest.raises(NoHandFoundError):
        report.require_one()


@pytest.mark.parametrize("side", [None, HandSide.NOT_APPLICABLE])
def test_l30_without_reported_physical_side_fails_before_any_factory(side):
    valid = _candidate()
    unknown = replace(_candidate("L30", channel="can1"), reported_side=side)
    report = _report(valid, unknown)
    for method in (report.named_candidates, report.create_hand_devices):
        with pytest.raises(IncompleteIdentityError, match="can1.*physical side"):
            method()
    valid.transport.factory.assert_not_called()
    unknown.transport.factory.assert_not_called()


@pytest.mark.parametrize(
    "conflict", ["model", "side", "capability", "reported", "info_reported"]
)
def test_conflicting_identity_is_rejected_before_construction(conflict):
    candidate = _candidate()
    if conflict == "model":
        candidate = replace(candidate, identity=replace(candidate.identity, model="O6"))
    elif conflict == "side":
        candidate = replace(
            candidate, device_info=replace(candidate.device_info, side=HandSide.RIGHT)
        )
    elif conflict == "capability":
        candidate = replace(
            candidate,
            capabilities=replace(
                candidate.capabilities,
                identity=replace(candidate.capabilities.identity, side=HandSide.RIGHT),
            ),
        )
    elif conflict == "reported":
        candidate = replace(candidate, reported_side=HandSide.RIGHT)
    else:
        candidate = replace(
            candidate,
            device_info=replace(candidate.device_info, reported_side=HandSide.RIGHT),
        )
    with pytest.raises(ValidationError, match="can0.*(inconsistent|conflicting)"):
        _report(candidate).create_hand_devices()
    candidate.transport.factory.assert_not_called()


@pytest.mark.parametrize("other_model", ["L6", "O6"])
def test_duplicate_endpoint_cannot_be_numbered_as_a_second_device(other_model):
    first = _candidate()
    second = _candidate(other_model)
    report = _report(first, second)
    with pytest.raises(ValidationError, match="duplicate discovery endpoint.*can0"):
        report.create_hand_devices()
    first.transport.factory.assert_not_called()
    second.transport.factory.assert_not_called()


def test_factory_failure_retains_alias_channel_and_original_cause():
    transport = SpyDependency()
    first_factory = Mock(return_value=transport)
    failure = OSError("adapter library unavailable")
    broken_factory = Mock(side_effect=failure)
    report = _report(
        _candidate(channel="can0", factory=first_factory),
        _candidate(channel="can1", factory=broken_factory),
    )
    with pytest.raises(DiscoveryError, match="l6l2.*can1") as caught:
        report.create_hand_devices()
    assert caught.value.cause is caught.value.__cause__ is failure
    assert caught.value.operation == "discovery_create_hand_devices"
    assert caught.value.model == "L6"
    assert transport.calls == []
    first_factory.assert_called_once_with()
    broken_factory.assert_called_once_with()
    assert list(report.named_candidates()) == ["l6l1", "l6l2"]


def test_real_discovery_of_all_models_builds_named_hands_without_more_traffic():
    from linkerbot_hand import discover_hands
    from linkerbot_transport import TransportState
    from tests.hand.api_v1.runtime.classic_can_fake import (
        ScriptedClassicCANTransport,
        make_script,
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

    from .test_discovery import _FAST, _empty_transport

    factories = {}
    channels = []
    expected = {}
    for model in ("L6", "O6", "L20Lite", "L20", "O20", "L30", "O30"):
        for side in (HandSide.LEFT, HandSide.RIGHT):
            name = f"{model.lower()}{'l' if side is HandSide.LEFT else 'r'}"
            expected[name] = model
            channel = _candidate(model, side, f"can{len(channels)}").channel
            channels.append(channel)
            if model == "O20":
                factory = lambda side=side: ScriptedO20Transport(O20DeviceScript(side))
            elif model == "L30":
                factory = lambda side=side: ScriptedL30Transport(
                    L30DeviceScript(side_code=0 if side is HandSide.LEFT else 1)
                )
            elif model == "O30":
                factory = lambda side=side: ScriptedO30Transport(
                    O30DeviceScript(reported_side=side)
                )
            else:
                factory = lambda model=model, side=side: ScriptedClassicCANTransport(
                    make_script(model, side)
                )
            factories[channel.name] = (channel.kind, factory)

    class Source:
        def __init__(self):
            self.created = []

        def list_channels(self, config):
            return tuple(reversed(channels))

        def create_transport(self, channel, wire_protocol):
            wire, factory = factories[channel.name]
            transport = factory() if wire is wire_protocol else _empty_transport()
            self.created.append(transport)
            return transport

    source = Source()
    report = discover_hands(_FAST, source=source)
    assert report.failures == report.partial_candidates == ()
    assert {name: c.model for name, c in report.named_candidates().items()} == expected
    probe_count = len(source.created)
    before = [transport.get_health().tx_frames for transport in source.created]

    hands = report.create_hand_devices()
    assert list(hands) == sorted(expected)
    assert len(source.created) == probe_count + len(expected)
    assert before == [
        transport.get_health().tx_frames for transport in source.created[:probe_count]
    ]
    for transport in source.created:
        assert transport.get_health().state is TransportState.CLOSED
    for transport in source.created[probe_count:]:
        assert transport.get_health().tx_frames == 0
    for name, hand in hands.items():
        assert hand.get_capabilities().model == expected[name]
        assert not hand.get_health().connected
        hand.close()
