"""Identity-only automatic discovery integration tests."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

import can
import pytest

from linkerbot_hand import (
    AmbiguousHandError,
    DiscoveryChannel,
    DiscoveryConfig,
    DiscoveryError,
    DiscoveryStatus,
    HandSide,
    IncompleteIdentityError,
    NoHandFoundError,
    TransportKind,
    TransportSpec,
    ValidationError,
    discover_hands,
)
from linkerbot_hand.protocols.l30 import codec as l30_codec
from linkerbot_hand.protocols.l30.framing import (
    ACCESS_READ,
)
from linkerbot_hand.protocols.l30.framing import (
    build_can_id as build_l30_can_id,
)
from linkerbot_hand.protocols.l30.framing import (
    parse_can_id as parse_l30_can_id,
)
from linkerbot_transport import (
    CANRouteKey,
    SendReceipt,
    TransportLifecycle,
    TransportState,
)
from linkerbot_transport.canfd import CANFDMessage
from linkerbot_transport.testing import FakeTransport
from tests.hand.api_v1.runtime.classic_can_fake import (
    ClassicCANDeviceScript,
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

_FAST = DiscoveryConfig(
    response_timeout_s=0.003,
    send_timeout_s=0.1,
    inter_request_delay_s=0,
    l30_node_ids=(1,),
)


class _Source:
    def __init__(
        self,
        channel: DiscoveryChannel,
        factories: dict[TransportKind, Callable[[], object]],
    ) -> None:
        self.channel = channel
        self.factories = factories
        self.created: list[tuple[TransportKind, object]] = []

    def list_channels(self, config: DiscoveryConfig) -> tuple[DiscoveryChannel, ...]:
        return (self.channel,)

    def create_transport(
        self,
        channel: DiscoveryChannel,
        wire_protocol: TransportKind,
    ) -> object:
        assert channel == self.channel
        transport = self.factories[wire_protocol]()
        self.created.append((wire_protocol, transport))
        return transport


class _MultiClassicTransport(FakeTransport[can.Message]):
    def __init__(self, *scripts: ClassicCANDeviceScript) -> None:
        super().__init__(route_key_of=_route_key)
        self.scripts = scripts

    def send(self, message: can.Message) -> SendReceipt:
        receipt = self._send_without_responses(message)
        if receipt.exception() is None:
            for script in self.scripts:
                for response in script.respond(message):
                    self.emit(response)
        return receipt

    def _send_without_responses(self, message: can.Message) -> SendReceipt:
        return super().send(message)


class _ProductOnlyClassicTransport(_MultiClassicTransport):
    def send(self, message: can.Message) -> SendReceipt:
        receipt = self._send_without_responses(message)
        if receipt.exception() is None and bytes(message.data) == b"\xc0":
            for script in self.scripts:
                for response in script.respond(message):
                    self.emit(response)
        return receipt


class _O20CollisionTransport(FakeTransport[CANFDMessage]):
    def __init__(self, *scripts: O20DeviceScript) -> None:
        super().__init__(route_key_of=_route_key)
        self.scripts = scripts

    def send(self, message: CANFDMessage) -> SendReceipt:
        receipt = super().send(message)
        if receipt.exception() is None:
            for script in self.scripts:
                for response in script.respond(message):
                    self.emit(response)
        return receipt


class _NodeL30IdentityTransport(FakeTransport[CANFDMessage]):
    def __init__(self, *, node_id: int, side: HandSide) -> None:
        super().__init__(route_key_of=_route_key)
        self.node_id = node_id
        self.side = side
        self.sent_messages: list[CANFDMessage] = []

    def send(self, message: CANFDMessage) -> SendReceipt:
        receipt = super().send(message)
        if receipt.exception() is not None or not message.is_extended_id:
            return receipt
        frame = parse_l30_can_id(message.arbitration_id)
        if (
            frame.access != ACCESS_READ
            or frame.destination_id != self.node_id
            or frame.source_id != 0
            or frame.parent != l30_codec.PARENT_CONFIG
        ):
            return receipt
        self.sent_messages.append(message)
        if frame.subcommand == l30_codec.SUB_DEVICE_INFO:
            payload = self._device_info()
        elif frame.subcommand == l30_codec.SUB_PRODUCT_CODE:
            payload = self._product_code().encode("ascii")
        else:
            return receipt
        self.emit(
            CANFDMessage(
                arbitration_id=build_l30_can_id(
                    access=ACCESS_READ,
                    parent=frame.parent,
                    subcommand=frame.subcommand,
                    destination_id=0,
                    source_id=self.node_id,
                ),
                data=bytes((len(payload), 0, 0)) + payload,
                is_extended_id=True,
            )
        )
        return receipt

    def _device_info(self) -> bytes:
        side_code = 0 if self.side is HandSide.LEFT else 1
        return (
            b"\x13"
            + (321).to_bytes(4, "big")
            + bytes((1, 0, 6))
            + bytes((0, 0, 3))
            + bytes((1, 0, 2))
            + bytes((self.node_id, side_code))
            + b"BA"
        )

    def _product_code(self) -> str:
        side = "L" if self.side is HandSide.LEFT else "R"
        return f"LHT30-06-321-{side}-B-3-A"


def _route_key(message: can.Message | CANFDMessage) -> CANRouteKey:
    return CANRouteKey(message.arbitration_id, message.is_extended_id)


def _empty_transport() -> FakeTransport[can.Message]:
    return FakeTransport(route_key_of=_classic_route_key)


def _classic_route_key(message: can.Message) -> CANRouteKey:
    return CANRouteKey(message.arbitration_id, message.is_extended_id)


def _classic_channel(
    *,
    fd: bool = False,
    nominal_bitrate: int = 1_000_000,
) -> DiscoveryChannel:
    return DiscoveryChannel(
        provider="fake",
        name="can-test",
        kind=TransportKind.CAN_FD if fd else TransportKind.CAN,
        nominal_bitrate=nominal_bitrate,
        data_bitrate=5_000_000 if fd else None,
    )


def _fd_channel(*, data_bitrate: int = 5_000_000) -> DiscoveryChannel:
    return DiscoveryChannel(
        provider="fake",
        name="canfd-test",
        kind=TransportKind.CAN_FD,
        nominal_bitrate=1_000_000,
        data_bitrate=data_bitrate,
    )


@pytest.mark.parametrize(
    ("model", "side"),
    [
        ("L6", HandSide.LEFT),
        ("O6", HandSide.RIGHT),
        ("L20Lite", HandSide.LEFT),
        ("L20", HandSide.RIGHT),
    ],
)
def test_discovers_every_classic_profile_with_identity_only(
    model: str,
    side: HandSide,
) -> None:
    script = make_script(model, side)
    source = _Source(
        _classic_channel(),
        {TransportKind.CAN: lambda: ScriptedClassicCANTransport(script)},
    )

    report = discover_hands(_FAST, source=source)

    candidate = report.require_one()
    assert candidate.model == model
    assert candidate.side is side
    assert candidate.status is DiscoveryStatus.VERIFIED
    assert candidate.wire_protocol is TransportKind.CAN
    assert report.failures == ()
    assert script.sent_payloads[0] == b"\xc0"
    assert set(script.sent_payloads) <= {
        b"\xc0",
        b"\xc1",
        b"\xc1\x01",
        b"\xc2",
        b"\xc4",
    }
    assert all(
        cast(TransportLifecycle, transport).get_health().state is TransportState.CLOSED
        for _, transport in source.created
    )


def test_no_response_is_bounded_and_require_one_reports_no_hand() -> None:
    source = _Source(_classic_channel(), {TransportKind.CAN: _empty_transport})

    report = discover_hands(_FAST, source=source)

    assert report.candidates == ()
    assert report.failures == ()
    with pytest.raises(NoHandFoundError):
        report.require_one()


def test_known_incompatible_nominal_bitrate_is_not_probed() -> None:
    source = _Source(
        _classic_channel(nominal_bitrate=500_000),
        {},
    )

    report = discover_hands(_FAST, source=source)

    assert report.candidates == ()
    assert report.failures == ()
    assert source.created == []


def test_discovery_source_errors_use_the_stable_discovery_error() -> None:
    class BrokenSource:
        def list_channels(
            self,
            config: DiscoveryConfig,
        ) -> tuple[DiscoveryChannel, ...]:
            raise OSError("adapter enumeration failed")

        def create_transport(
            self,
            channel: DiscoveryChannel,
            wire_protocol: TransportKind,
        ) -> object:
            raise AssertionError("no transport should be created")

    with pytest.raises(DiscoveryError, match="could not list channels") as captured:
        discover_hands(_FAST, source=BrokenSource())

    assert isinstance(captured.value.cause, OSError)


def test_transport_creation_failure_is_reported_per_channel() -> None:
    def unavailable_transport() -> object:
        raise OSError("CAN adapter is unavailable")

    source = _Source(
        _classic_channel(),
        {TransportKind.CAN: unavailable_transport},
    )

    report = discover_hands(_FAST, source=source)

    assert report.candidates == ()
    assert len(report.failures) == 1
    assert report.failures[0].operation == "probe_classic_identity"
    assert report.failures[0].error_type == "OSError"


def test_multiple_classic_hands_are_returned_without_silent_selection() -> None:
    left = make_script("L6", HandSide.LEFT)
    right = make_script("O6", HandSide.RIGHT)
    source = _Source(
        _classic_channel(),
        {TransportKind.CAN: lambda: _MultiClassicTransport(left, right)},
    )

    report = discover_hands(_FAST, source=source)

    assert {(candidate.model, candidate.side) for candidate in report.candidates} == {
        ("L6", HandSide.LEFT),
        ("O6", HandSide.RIGHT),
    }
    with pytest.raises(AmbiguousHandError):
        report.require_one()


def test_same_classic_address_collision_is_partial_and_never_guessed() -> None:
    first = make_script("L6", HandSide.LEFT)
    second = make_script("O6", HandSide.LEFT)
    source = _Source(
        _classic_channel(),
        {TransportKind.CAN: lambda: _MultiClassicTransport(first, second)},
    )

    candidate = discover_hands(_FAST, source=source).require_one(allow_partial=True)

    assert candidate.status is DiscoveryStatus.PARTIAL
    assert candidate.side is HandSide.LEFT
    assert candidate.problems[0].code == "address_collision"
    with pytest.raises(IncompleteIdentityError):
        candidate.create_hand_device()


def test_fd_channel_stops_after_a_classic_hand_responds() -> None:
    script = make_script("L6", HandSide.LEFT)

    def unexpected_fd_transport() -> object:
        raise AssertionError("CAN FD probing must be skipped")

    source = _Source(
        _classic_channel(fd=True),
        {
            TransportKind.CAN: lambda: ScriptedClassicCANTransport(script),
            TransportKind.CAN_FD: unexpected_fd_transport,
        },
    )

    candidate = discover_hands(_FAST, source=source).require_one()

    assert candidate.channel.kind is TransportKind.CAN_FD
    assert candidate.wire_protocol is TransportKind.CAN
    assert tuple(kind for kind, _ in source.created) == (TransportKind.CAN,)


def test_classic_product_response_without_versions_is_partial() -> None:
    script = make_script("L6", HandSide.LEFT)
    source = _Source(
        _classic_channel(),
        {TransportKind.CAN: lambda: _ProductOnlyClassicTransport(script)},
    )

    report = discover_hands(_FAST, source=source)

    candidate = report.require_one(allow_partial=True)
    assert candidate.model == "L6"
    assert candidate.status is DiscoveryStatus.PARTIAL
    assert candidate.problems[0].code == "identity_timeout"
    with pytest.raises(IncompleteIdentityError):
        candidate.create_hand_device()


@pytest.mark.parametrize(
    "product_code",
    (
        "L20-010-001-L-Z-1-A",
        "LHT20-10-499-L-B-1-D",
        "L25-010-001-L-Z-1-A",
        "LHL25-010-001-L-Z-1-A",
        "LHG20-010-001-L-Z-1-A",
    ),
)
def test_discovery_maps_l20_factory_product_codes_to_public_l20(
    product_code: str,
) -> None:
    script = make_script("L20", HandSide.LEFT)
    script.product_code = product_code
    source = _Source(
        _classic_channel(),
        {TransportKind.CAN: lambda: ScriptedClassicCANTransport(script)},
    )

    candidate = discover_hands(_FAST, source=source).require_one()

    assert candidate.model == "L20"
    assert candidate.device_info is not None
    assert candidate.device_info.product_code == product_code
    assert candidate.create_hand_device().get_capabilities().model == "L20"


def test_malformed_classic_product_response_returns_partial_candidate() -> None:
    script = ClassicCANDeviceScript(
        model="L6",
        side=HandSide.LEFT,
        product_code="BROKEN",
        firmware=(2, 4, 0),
    )
    source = _Source(
        _classic_channel(),
        {TransportKind.CAN: lambda: ScriptedClassicCANTransport(script)},
    )

    candidate = discover_hands(_FAST, source=source).require_one(allow_partial=True)

    assert candidate.model is None
    assert candidate.status is DiscoveryStatus.PARTIAL
    assert candidate.problems[0].code == "malformed_identity"


def test_o20_device_info_is_verified_without_baseline_queries() -> None:
    script = O20DeviceScript(HandSide.RIGHT)
    source = _Source(
        _fd_channel(),
        {
            TransportKind.CAN: _empty_transport,
            TransportKind.CAN_FD: lambda: ScriptedO20Transport(script),
        },
    )

    candidate = discover_hands(_FAST, source=source).require_one()

    assert candidate.model == "O20"
    assert candidate.side is HandSide.RIGHT
    assert candidate.address.node_id == 1
    assert script.identity_queries == 1
    assert sum(script.read_counts.values()) == 1


def test_same_o20_device_id_collision_is_partial_and_never_guessed() -> None:
    first = O20DeviceScript(HandSide.LEFT, serial_number="SN-O20-A")
    second = O20DeviceScript(HandSide.LEFT, serial_number="SN-O20-B")
    source = _Source(
        _fd_channel(),
        {
            TransportKind.CAN: _empty_transport,
            TransportKind.CAN_FD: lambda: _O20CollisionTransport(first, second),
        },
    )

    report = discover_hands(_FAST, source=source)

    candidate = report.require_one(allow_partial=True)
    assert candidate.model == "O20"
    assert candidate.problems[0].code == "address_collision"
    with pytest.raises(IncompleteIdentityError):
        candidate.create_hand_device()


def test_l30_non_default_node_and_reported_physical_side_are_preserved() -> None:
    source = _Source(
        _fd_channel(),
        {
            TransportKind.CAN: _empty_transport,
            TransportKind.CAN_FD: lambda: _NodeL30IdentityTransport(
                node_id=3,
                side=HandSide.RIGHT,
            ),
        },
    )
    config = DiscoveryConfig(
        response_timeout_s=0.003,
        inter_request_delay_s=0,
        l30_node_ids=(1, 3, 7),
    )

    candidate = discover_hands(config, source=source).require_one()

    assert candidate.model == "L30"
    assert candidate.side is HandSide.NOT_APPLICABLE
    assert candidate.reported_side is HandSide.RIGHT
    assert candidate.address.node_id == 3
    assert candidate.create_hand_device().get_capabilities().model == "L30"


def test_l30_default_profile_is_verified_from_existing_protocol_script() -> None:
    script = L30DeviceScript(side_code=0)
    source = _Source(
        _fd_channel(),
        {
            TransportKind.CAN: _empty_transport,
            TransportKind.CAN_FD: lambda: ScriptedL30Transport(script),
        },
    )

    candidate = discover_hands(_FAST, source=source).require_one()

    assert candidate.model == "L30"
    assert candidate.reported_side is HandSide.LEFT
    assert script.identity_queries == 1
    assert len(script.sent_messages) == 2


def test_o30_identity_uses_reported_side() -> None:
    script = O30DeviceScript(reported_side=HandSide.RIGHT)
    source = _Source(
        _fd_channel(data_bitrate=1_000_000),
        {
            TransportKind.CAN: _empty_transport,
            TransportKind.CAN_FD: lambda: ScriptedO30Transport(script),
        },
    )

    report = discover_hands(_FAST, source=source)
    candidate = report.require_one()

    assert candidate.model == "O30"
    assert candidate.side is HandSide.RIGHT
    assert candidate.reported_side is HandSide.RIGHT
    assert candidate.status is DiscoveryStatus.VERIFIED
    assert candidate.identity.firmware_version is not None
    assert candidate.problems == ()


@pytest.mark.parametrize("data_bitrate", (1_000_000, 5_000_000, 7_000_000))
def test_o30_left_hand_discovered_over_0x002_0x402(data_bitrate: int) -> None:
    script = O30DeviceScript(
        reported_side=HandSide.LEFT,
        request_id=0x002,
        response_id=0x402,
    )
    source = _Source(
        _fd_channel(data_bitrate=data_bitrate),
        {
            TransportKind.CAN: _empty_transport,
            TransportKind.CAN_FD: lambda: ScriptedO30Transport(script),
        },
    )

    report = discover_hands(_FAST, source=source)
    candidate = report.require_one()

    assert candidate.model == "O30"
    assert candidate.side is HandSide.LEFT
    assert candidate.status is DiscoveryStatus.VERIFIED
    assert candidate.address.request_id == 0x002
    assert candidate.address.response_id == 0x402
    assert candidate.problems == ()


def test_o30_identity_without_any_side_evidence_is_partial() -> None:
    script = O30DeviceScript(reported_side=None)
    source = _Source(
        _fd_channel(data_bitrate=1_000_000),
        {
            TransportKind.CAN: _empty_transport,
            TransportKind.CAN_FD: lambda: ScriptedO30Transport(script),
        },
    )

    report = discover_hands(_FAST, source=source)
    candidate = report.require_one(allow_partial=True)

    assert candidate.model == "O30"
    assert candidate.side is None
    assert candidate.reported_side is None
    assert candidate.problems[0].code == "missing_side"
    with pytest.raises(IncompleteIdentityError):
        report.require_one()


@pytest.mark.parametrize("product_model", ("O30-20-001-R-B", "O30i-20-001-R-B"))
def test_o30_legacy_product_prefix_is_discovered(product_model: str) -> None:
    script = O30DeviceScript(product_model=product_model, reported_side=None)
    source = _Source(
        _fd_channel(data_bitrate=1_000_000),
        {
            TransportKind.CAN: _empty_transport,
            TransportKind.CAN_FD: lambda: ScriptedO30Transport(script),
        },
    )

    report = discover_hands(_FAST, source=source)
    candidate = report.require_one()

    assert candidate.model == "O30"
    assert candidate.side is HandSide.RIGHT
    assert candidate.reported_side is HandSide.RIGHT
    assert candidate.status is DiscoveryStatus.VERIFIED
    assert candidate.problems == ()
    hand = candidate.create_hand_device()
    assert hand.get_capabilities().side is HandSide.RIGHT
    assert script.identity_queries == 1
    assert len(script.sent_messages) == 15


@pytest.mark.parametrize(
    "factory",
    (
        lambda: DiscoveryConfig(channels=()),
        lambda: DiscoveryConfig(response_timeout_s=0),
        lambda: DiscoveryConfig(route_queue_capacity=True),
        lambda: DiscoveryConfig(l30_node_ids=(0,)),
        lambda: DiscoveryConfig(l30_node_ids=(1, 1)),
    ),
)
def test_discovery_config_rejects_invalid_bounds(
    factory: Callable[[], DiscoveryConfig],
) -> None:
    with pytest.raises(ValidationError):
        factory()


def test_transport_spec_rejects_classic_data_bitrate() -> None:
    with pytest.raises(ValidationError, match="cannot have a data bitrate"):
        TransportSpec(
            provider="fake",
            channel="can0",
            kind=TransportKind.CAN,
            nominal_bitrate=1_000_000,
            data_bitrate=5_000_000,
            factory=_empty_transport,
        )
