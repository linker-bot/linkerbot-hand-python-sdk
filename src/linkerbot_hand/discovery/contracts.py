"""Public, ROS-independent contracts for explicit hand discovery."""

from __future__ import annotations

import math
from collections.abc import Callable, Hashable
from dataclasses import dataclass, field, replace
from enum import Enum
from functools import partial
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ..api.config import DeviceConfig
from ..api.device_info import DeviceInfo, VersionField
from ..api.enums import HandSide, TransportKind
from ..api.errors import ValidationError
from ..capabilities.types import DeviceCapabilities
from ._naming import name_candidates
from .errors import (
    AmbiguousHandError,
    DiscoveryError,
    IncompleteIdentityError,
    NoHandFoundError,
)
from .options import MiniCANFDDiscoveryConfig, PythonCANDiscoveryConfig, protocol_order

if TYPE_CHECKING:
    from ..api.device import HandDevice


class DiscoveryStatus(str, Enum):
    """Whether all identity fields required to select an endpoint are known."""

    VERIFIED = "verified"
    PARTIAL = "partial"


@dataclass(frozen=True, slots=True)
class DiscoveryConfig:
    """Bounds and optional channel filter for one explicit discovery scan."""

    channels: tuple[str, ...] | None = None
    response_timeout_s: float = 0.12
    send_timeout_s: float = 0.1
    inter_request_delay_s: float = 0.002
    route_queue_capacity: int = 256
    l30_node_ids: tuple[int, ...] = tuple(range(1, 32))
    o30_request_ids: tuple[int, ...] = (0x001, 0x002)
    backends: tuple[str, ...] | None = None
    total_timeout_s: float = 30.0
    pcan: PythonCANDiscoveryConfig = field(default_factory=PythonCANDiscoveryConfig)
    slcan: PythonCANDiscoveryConfig = field(default_factory=PythonCANDiscoveryConfig)
    minicanfd: MiniCANFDDiscoveryConfig = field(
        default_factory=MiniCANFDDiscoveryConfig
    )

    def __post_init__(self) -> None:
        if self.backends is not None and (
            not isinstance(self.backends, tuple)
            or not self.backends
            or any(
                not isinstance(value, str)
                or value not in {"socketcan", "pcan", "slcan", "minicanfd"}
                for value in self.backends
            )
            or len(set(self.backends)) != len(self.backends)
        ):
            raise ValidationError(
                "backends must contain unique supported backend names"
            )
        for name in ("pcan", "slcan"):
            if not isinstance(getattr(self, name), PythonCANDiscoveryConfig):
                raise ValidationError(f"{name} must be PythonCANDiscoveryConfig")
        if not isinstance(self.minicanfd, MiniCANFDDiscoveryConfig):
            raise ValidationError("minicanfd must be MiniCANFDDiscoveryConfig")
        if self.channels is not None:
            if (
                not isinstance(self.channels, tuple)
                or not self.channels
                or any(
                    not isinstance(value, str) or not value for value in self.channels
                )
            ):
                raise ValidationError(
                    "channels must contain non-empty interface names or be None"
                )
            if len(set(self.channels)) != len(self.channels):
                raise ValidationError("channels must not contain duplicates")
        for name in ("response_timeout_s", "send_timeout_s", "total_timeout_s"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValidationError(f"{name} must be finite and positive")
        delay = self.inter_request_delay_s
        if (
            isinstance(delay, bool)
            or not isinstance(delay, (int, float))
            or not math.isfinite(delay)
            or delay < 0
        ):
            raise ValidationError(
                "inter_request_delay_s must be finite and non-negative"
            )
        if type(self.route_queue_capacity) is not int or self.route_queue_capacity <= 0:
            raise ValidationError("route_queue_capacity must be a positive int")
        if (
            not isinstance(self.l30_node_ids, tuple)
            or not self.l30_node_ids
            or any(
                type(value) is not int or not 1 <= value <= 31
                for value in self.l30_node_ids
            )
        ):
            raise ValidationError("l30_node_ids must contain node IDs within 1..31")
        if len(set(self.l30_node_ids)) != len(self.l30_node_ids):
            raise ValidationError("l30_node_ids must not contain duplicates")
        if (
            not isinstance(self.o30_request_ids, tuple)
            or not self.o30_request_ids
            or any(
                type(value) is not int or not 0 <= value <= 0x3FE
                for value in self.o30_request_ids
            )
        ):
            raise ValidationError(
                "o30_request_ids must contain IDs within 0x000..0x3FE"
            )
        if len(set(self.o30_request_ids)) != len(self.o30_request_ids):
            raise ValidationError("o30_request_ids must not contain duplicates")


@dataclass(frozen=True, slots=True)
class DiscoveryChannel:
    """One active adapter channel offered by a discovery source."""

    provider: str
    name: str
    kind: TransportKind
    nominal_bitrate: int | None = None
    data_bitrate: int | None = None
    supports_classic: bool = True
    supports_fd: bool | None = None
    probe_protocols: tuple[TransportKind, ...] | None = None
    transport_specs: tuple[TransportSpec, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        for name in ("provider", "name"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValidationError(f"{name} must be a non-empty str")
        if self.kind not in {TransportKind.CAN, TransportKind.CAN_FD}:
            raise ValidationError("discovery channel kind must be CAN or CAN FD")
        for name in ("nominal_bitrate", "data_bitrate"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value <= 0):
                raise ValidationError(f"{name} must be a positive int or None")
        if self.kind is TransportKind.CAN and self.data_bitrate is not None:
            raise ValidationError("classic CAN channels cannot have a data bitrate")
        if type(self.supports_classic) is not bool or (
            self.supports_fd is not None and type(self.supports_fd) is not bool
        ):
            raise ValidationError("channel capabilities must be boolean or unknown FD")
        order = self.probe_protocols
        if order is None:
            order = (
                (TransportKind.CAN, TransportKind.CAN_FD)
                if self.kind is TransportKind.CAN_FD
                else (TransportKind.CAN,)
            )
        protocol_order(order)
        if TransportKind.CAN in order and not self.supports_classic:
            raise ValidationError("classic probing requires classic support")
        if TransportKind.CAN_FD in order and (
            self.kind is not TransportKind.CAN_FD or self.supports_fd is False
        ):
            raise ValidationError(
                "FD probing requires an FD-capable or unconfirmed channel"
            )
        object.__setattr__(self, "probe_protocols", order)
        if not isinstance(self.transport_specs, tuple) or any(
            not isinstance(spec, TransportSpec) for spec in self.transport_specs
        ):
            raise ValidationError("transport_specs must contain TransportSpec values")
        if len({spec.kind for spec in self.transport_specs}) != len(
            self.transport_specs
        ):
            raise ValidationError("transport_specs must not repeat wire protocols")
        for spec in self.transport_specs:
            if spec.provider != self.provider or spec.channel != self.name:
                raise ValidationError(
                    "transport recipe must match its discovery channel"
                )


@runtime_checkable
class DiscoverySource(Protocol):
    """Injectable adapter enumeration and probe-transport provider."""

    def list_channels(
        self,
        config: DiscoveryConfig,
    ) -> tuple[DiscoveryChannel, ...]:
        """Return active channels without reconfiguring or opening them."""
        ...

    def create_transport(
        self,
        channel: DiscoveryChannel,
        wire_protocol: TransportKind,
    ) -> object:
        """Construct a closed transport for one probe wire protocol."""
        ...


@dataclass(frozen=True, slots=True)
class TransportSpec:
    """Closed transport recipe retained by a discovered candidate."""

    provider: str
    channel: str
    kind: TransportKind
    nominal_bitrate: int | None
    data_bitrate: int | None
    factory: Callable[[], object] = field(repr=False, compare=False)
    connection_key: Hashable | None = None

    def __post_init__(self) -> None:
        for name in ("provider", "channel"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValidationError(f"{name} must be a non-empty str")
        if self.kind not in {TransportKind.CAN, TransportKind.CAN_FD}:
            raise ValidationError("transport spec kind must be CAN or CAN FD")
        for name in ("nominal_bitrate", "data_bitrate"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value <= 0):
                raise ValidationError(f"{name} must be a positive int or None")
        if self.kind is TransportKind.CAN and self.data_bitrate is not None:
            raise ValidationError(
                "classic CAN transport specs cannot have a data bitrate"
            )
        if not callable(self.factory):
            raise ValidationError("transport spec factory must be callable")
        if self.connection_key is not None:
            try:
                hash(self.connection_key)
            except TypeError as error:
                raise ValidationError(
                    "connection_key must be immutable/hashable"
                ) from error

    def create_transport(self) -> object:
        """Create a new closed transport; this method performs no device I/O."""

        transport = self.factory()
        if transport is None:
            raise ValidationError("transport spec factory returned None")
        return transport


@dataclass(frozen=True, slots=True)
class DiscoveryAddress:
    """Machine-readable request/response address used for identity discovery."""

    scheme: str
    request_id: int
    response_id: int
    is_extended_id: bool
    node_id: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.scheme, str) or not self.scheme:
            raise ValidationError("address scheme must be a non-empty str")
        maximum = 0x1FFFFFFF if self.is_extended_id else 0x7FF
        for name in ("request_id", "response_id"):
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value <= maximum:
                raise ValidationError(f"{name} must fit the selected CAN ID format")
        if not isinstance(self.is_extended_id, bool):
            raise ValidationError("is_extended_id must be bool")
        if self.node_id is not None and (
            type(self.node_id) is not int or not 0 <= self.node_id <= 0xFF
        ):
            raise ValidationError("node_id must be a byte or None")


@dataclass(frozen=True, slots=True)
class DiscoveryProblem:
    """Stable reason why one responding endpoint is only partially identified."""

    code: str
    message: str

    def __post_init__(self) -> None:
        for name in ("code", "message"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValidationError(f"{name} must be a non-empty str")


@dataclass(frozen=True, slots=True)
class DiscoveryIdentity:
    """Identity evidence with an optional side for incomplete protocols."""

    model: str | None
    side: HandSide | None
    serial_number: str | None = None
    pcb_version: VersionField | None = None
    firmware_version: VersionField | None = None
    mechanical_version: VersionField | None = None
    product_code: str | None = None
    sensor_variant: str | None = None
    raw_identity: bytes | tuple[int, ...] | None = None
    fingerprint: Hashable | None = None

    def __post_init__(self) -> None:
        if self.model is not None and (
            not isinstance(self.model, str) or not self.model
        ):
            raise ValidationError("identity model must be a non-empty str or None")
        if self.side is not None and not isinstance(self.side, HandSide):
            raise ValidationError("identity side must be HandSide or None")
        for name in ("serial_number", "product_code", "sensor_variant"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value):
                raise ValidationError(f"{name} must be a non-empty str or None")
        for name in ("pcb_version", "firmware_version", "mechanical_version"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, VersionField):
                raise ValidationError(f"{name} must be VersionField or None")
        if self.raw_identity is not None and not isinstance(
            self.raw_identity, (bytes, tuple)
        ):
            raise ValidationError("raw_identity must be bytes, tuple, or None")
        if isinstance(self.raw_identity, tuple) and any(
            type(value) is not int or not 0 <= value <= 255
            for value in self.raw_identity
        ):
            raise ValidationError("raw_identity tuple must contain bytes")
        if self.fingerprint is not None:
            try:
                hash(self.fingerprint)
            except TypeError as error:
                raise ValidationError("fingerprint must be hashable or None") from error


@dataclass(frozen=True, slots=True)
class DiscoveryCandidate:
    """One responding endpoint and the evidence needed to open it explicitly."""

    channel: DiscoveryChannel
    wire_protocol: TransportKind
    address: DiscoveryAddress
    status: DiscoveryStatus
    identity: DiscoveryIdentity
    transport: TransportSpec
    reported_side: HandSide | None = None
    capabilities: DeviceCapabilities | None = None
    device_info: DeviceInfo | None = None
    problems: tuple[DiscoveryProblem, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.channel, DiscoveryChannel):
            raise ValidationError("channel must be DiscoveryChannel")
        if self.wire_protocol not in {TransportKind.CAN, TransportKind.CAN_FD}:
            raise ValidationError("wire_protocol must be CAN or CAN FD")
        if not isinstance(self.address, DiscoveryAddress):
            raise ValidationError("address must be DiscoveryAddress")
        if not isinstance(self.status, DiscoveryStatus):
            raise ValidationError("status must be DiscoveryStatus")
        if not isinstance(self.identity, DiscoveryIdentity):
            raise ValidationError("identity must be DiscoveryIdentity")
        if not isinstance(self.transport, TransportSpec):
            raise ValidationError("transport must be TransportSpec")
        if (
            self.wire_protocol is TransportKind.CAN_FD
            and self.channel.kind is not TransportKind.CAN_FD
        ):
            raise ValidationError("classic CAN channels cannot carry CAN FD candidates")
        if (
            self.transport.provider != self.channel.provider
            or self.transport.channel != self.channel.name
            or self.transport.kind is not self.wire_protocol
        ):
            raise ValidationError(
                "candidate transport must match its channel and wire protocol"
            )
        expected_data_bitrate = (
            self.channel.data_bitrate
            if self.wire_protocol is TransportKind.CAN_FD
            else None
        )
        if (
            self.transport.nominal_bitrate != self.channel.nominal_bitrate
            or self.transport.data_bitrate != expected_data_bitrate
        ):
            raise ValidationError("candidate transport bitrates must match its channel")
        if self.reported_side is not None and not isinstance(
            self.reported_side, HandSide
        ):
            raise ValidationError("reported_side must be HandSide or None")
        if self.capabilities is not None and not isinstance(
            self.capabilities, DeviceCapabilities
        ):
            raise ValidationError("capabilities must be DeviceCapabilities or None")
        if self.device_info is not None and not isinstance(
            self.device_info, DeviceInfo
        ):
            raise ValidationError("device_info must be DeviceInfo or None")
        if not isinstance(self.problems, tuple) or any(
            not isinstance(value, DiscoveryProblem) for value in self.problems
        ):
            raise ValidationError("problems must contain DiscoveryProblem values")
        if self.status is DiscoveryStatus.VERIFIED:
            if self.identity.model is None or self.identity.side is None:
                raise ValidationError("verified candidates require model and side")
            if self.capabilities is None or not self.capabilities.identity.verified:
                raise ValidationError(
                    "verified candidates require verified capabilities"
                )
            if self.device_info is None:
                raise ValidationError("verified candidates require device_info")
            if self.problems:
                raise ValidationError("verified candidates cannot contain problems")
        elif not self.problems:
            raise ValidationError("partial candidates require at least one problem")

    @property
    def model(self) -> str | None:
        """Return the canonical model when it could be recognized."""

        return self.identity.model

    @property
    def side(self) -> HandSide | None:
        """Return the verified endpoint side, or ``None`` when unresolved."""

        return self.identity.side

    def create_hand_device(
        self,
        *,
        side: HandSide | str | None = None,
        config: DeviceConfig | None = None,
    ) -> HandDevice:
        """Create a closed device, pinning any recorded fingerprint for open.

        Identity must match discovery before the first baseline and on retries.
        Missing-side partial candidates still require an explicit side.
        """

        from ..api._device_core import _default_runtime_factory
        from ..api.device import HandDevice

        model = self.model
        if model is None:
            raise IncompleteIdentityError(
                "candidate model is unknown",
                operation="discovery_create_hand_device",
            )
        if self.status is DiscoveryStatus.PARTIAL and {
            value.code for value in self.problems
        } != {"missing_side"}:
            raise IncompleteIdentityError(
                "candidate identity is incomplete",
                operation="discovery_create_hand_device",
                model=model,
            )
        resolved_side = _resolved_candidate_side(self, side)
        # Keep the expectation on the factory, not a one-shot runtime: a failed
        # first open discards its runtime and must retain the same constraint.
        runtime_factory = partial(
            _default_runtime_factory,
            expected_fingerprint=self.identity.fingerprint,
            identity_context=f"{self.channel.provider}:{self.channel.name} "
            f"({self.address})",
        )
        if model == "O30":
            from ..protocols.o30.framing import HOPAddressing

            if self.address.scheme != "o30_hop_endpoint" or self.address.is_extended_id:
                raise IncompleteIdentityError(
                    "O30 candidate has no standard HOP endpoint"
                )
            addressing = HOPAddressing(
                self.address.request_id, self.address.response_id
            )
            if config is not None and config.o30_request_id not in (
                None,
                addressing.request_id,
            ):
                raise ValidationError(
                    "o30_request_id disagrees with discovered endpoint"
                )
            return HandDevice(
                model=model,
                side=resolved_side.value,
                transport=self.transport.create_transport(),
                config=replace(
                    config or DeviceConfig(), o30_request_id=addressing.request_id
                ),
                runtime_factory=runtime_factory,
            )
        transport = self.transport.create_transport()
        if model == "L30":
            from ..protocols.l30 import L30ProtocolBackend

            node_id = self.address.node_id
            if node_id is None:
                raise IncompleteIdentityError(
                    "L30 candidate has no node ID",
                    operation="discovery_create_hand_device",
                    model=model,
                )
            return HandDevice(
                model=model,
                transport=transport,
                config=config,
                protocol_backend=L30ProtocolBackend(
                    HandSide.NOT_APPLICABLE,
                    node_id=node_id,
                ),
                runtime_factory=runtime_factory,
            )
        return HandDevice(
            model=model,
            side=resolved_side.value,
            transport=transport,
            config=config,
            runtime_factory=runtime_factory,
        )


@dataclass(frozen=True, slots=True)
class DiscoveryFailure:
    """Non-device failure encountered while enumerating or probing a channel."""

    operation: str
    code: str
    message: str
    error_type: str
    channel: DiscoveryChannel | None = None
    wire_protocol: TransportKind | None = None
    backend: str | None = None
    native_channel: str | None = None
    cleanup: Callable[[], None] | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        for name in ("operation", "code", "message", "error_type"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValidationError(f"{name} must be a non-empty str")
        if self.channel is not None and not isinstance(self.channel, DiscoveryChannel):
            raise ValidationError("channel must be DiscoveryChannel or None")
        if self.wire_protocol is not None and self.wire_protocol not in {
            TransportKind.CAN,
            TransportKind.CAN_FD,
        }:
            raise ValidationError("wire_protocol must be CAN, CAN FD, or None")


@dataclass(frozen=True, slots=True)
class DiscoveryEnumeration:
    """Channel enumeration with backend failures, before identity traffic."""

    channels: tuple[DiscoveryChannel, ...] = ()
    failures: tuple[DiscoveryFailure, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.channels, tuple) or any(
            not isinstance(c, DiscoveryChannel) for c in self.channels
        ):
            raise ValidationError("channels must contain DiscoveryChannel values")
        if not isinstance(self.failures, tuple) or any(
            not isinstance(f, DiscoveryFailure) for f in self.failures
        ):
            raise ValidationError("failures must contain DiscoveryFailure values")


@dataclass(frozen=True, slots=True)
class DiscoveryReport:
    """Discovery result; failed closes retain a cleanup callable in failures."""

    channels: tuple[DiscoveryChannel, ...]
    candidates: tuple[DiscoveryCandidate, ...]
    failures: tuple[DiscoveryFailure, ...]
    elapsed_s: float

    def __post_init__(self) -> None:
        for name, expected in (
            ("channels", DiscoveryChannel),
            ("candidates", DiscoveryCandidate),
            ("failures", DiscoveryFailure),
        ):
            values = getattr(self, name)
            if not isinstance(values, tuple) or any(
                not isinstance(value, expected) for value in values
            ):
                raise ValidationError(f"{name} contains invalid values")
        if (
            isinstance(self.elapsed_s, bool)
            or not isinstance(self.elapsed_s, (int, float))
            or not math.isfinite(self.elapsed_s)
            or self.elapsed_s < 0
        ):
            raise ValidationError("elapsed_s must be finite and non-negative")

    @property
    def verified_candidates(self) -> tuple[DiscoveryCandidate, ...]:
        """Return candidates whose complete identity was protocol-verified."""

        return tuple(
            candidate
            for candidate in self.candidates
            if candidate.status is DiscoveryStatus.VERIFIED
        )

    @property
    def partial_candidates(self) -> tuple[DiscoveryCandidate, ...]:
        """Return responding endpoints that require explicit resolution."""

        return tuple(
            candidate
            for candidate in self.candidates
            if candidate.status is DiscoveryStatus.PARTIAL
        )

    def named_candidates(self) -> dict[str, DiscoveryCandidate]:
        """Index verified candidates by scan-local model/physical-side aliases.

        A single L6 left hand is ``l6l``; multiple are ``l6l1``, ``l6l2``, etc.
        Groups sort by alias, then provider, channel, wire protocol and address.
        L30 uses ``reported_side`` without changing its construction contract.
        Missing sides raise IncompleteIdentityError; conflicting identities or
        duplicate endpoints raise ValidationError before any construction.

        This creates neither transports nor devices. Partial candidates and
        failures remain in the report; no verified candidates returns ``{}``.
        Aliases can change when devices or channels change and are not IDs.
        """

        return name_candidates(self.verified_candidates)

    def create_hand_devices(self) -> dict[str, HandDevice]:
        """Construct fresh closed devices using the same keys as named_candidates.

        Reuses each candidate's factory, preserving its transport recipe and
        endpoint and pinning any recorded fingerprint for open. No scan, device
        I/O, open or polling occurs. For per-device configuration, use
        named_candidates() and the individual factory.

        A factory failure raises DiscoveryError with alias/channel context and
        the original cause; no partial dictionary is returned. Diagnostics and
        probe cleanup remain the caller's responsibility before opening devices.
        """

        hands: dict[str, HandDevice] = {}
        for name, candidate in self.named_candidates().items():
            try:
                hands[name] = candidate.create_hand_device()
            except Exception as error:
                raise DiscoveryError(
                    f"failed to construct {name} on "
                    f"{candidate.channel.provider}:{candidate.channel.name}: {error}",
                    operation="discovery_create_hand_devices",
                    model=candidate.model,
                    cause=error,
                ) from error
        return hands

    def require_one(self, *, allow_partial: bool = False) -> DiscoveryCandidate:
        """Return one unique candidate or raise a stable discovery error."""

        if not isinstance(allow_partial, bool):
            raise ValidationError("allow_partial must be bool")
        if not self.candidates:
            raise NoHandFoundError("no hand responded to identity discovery")
        if len(self.candidates) != 1:
            raise AmbiguousHandError(
                f"discovery returned {len(self.candidates)} candidates"
            )
        candidate = self.candidates[0]
        if candidate.status is DiscoveryStatus.PARTIAL and not allow_partial:
            raise IncompleteIdentityError(
                "the only discovered candidate has incomplete identity",
                model=candidate.model,
            )
        return candidate


def _resolved_candidate_side(
    candidate: DiscoveryCandidate,
    requested: HandSide | str | None,
) -> HandSide:
    if isinstance(requested, str):
        try:
            requested = HandSide(requested.lower())
        except ValueError as error:
            raise ValidationError("side must be left or right") from error
    if requested is not None and requested not in {HandSide.LEFT, HandSide.RIGHT}:
        raise ValidationError("side must be left or right")
    discovered = candidate.side
    if discovered is HandSide.NOT_APPLICABLE:
        if requested is not None:
            raise ValidationError("side must not be supplied for L30")
        return discovered
    if discovered is not None:
        if requested is not None and requested is not discovered:
            raise ValidationError("requested side disagrees with discovered identity")
        return discovered
    if requested is None:
        raise IncompleteIdentityError(
            "candidate side is unknown; pass side explicitly",
            operation="discovery_create_hand_device",
            model=candidate.model,
        )
    return requested


__all__ = [
    "DiscoveryAddress",
    "DiscoveryCandidate",
    "DiscoveryChannel",
    "DiscoveryConfig",
    "DiscoveryEnumeration",
    "DiscoveryFailure",
    "DiscoveryIdentity",
    "DiscoveryProblem",
    "DiscoveryReport",
    "DiscoverySource",
    "DiscoveryStatus",
    "TransportSpec",
]
