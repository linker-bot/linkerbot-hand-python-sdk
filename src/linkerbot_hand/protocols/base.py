"""Pure contracts between model codecs and the generic device runtime."""

from __future__ import annotations

import math
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..api.device_info import DeviceInfo
from ..api.enums import DataOrigin
from ..api.errors import ValidationError
from ..api.readings import (
    JointFaultStatus,
    L6FaultStatus,
    L20LiteFaultStatus,
    O6FaultStatus,
    RawNumber,
    TactileMatrix,
    TactileSensorSample,
)
from ..capabilities.types import (
    CommandSemantic,
    DeviceCapabilities,
    TelemetrySemantic,
)


def _require_hashable(value: object, name: str) -> None:
    try:
        hash(value)
    except TypeError as error:
        raise ValidationError(f"{name} must be hashable") from error


def _finite_non_negative(value: object, name: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValidationError(f"{name} must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class SamplePayload:
    """Decoded protocol data before runtime-owned sample metadata is added."""

    semantic: TelemetrySemantic
    raw: tuple[RawNumber, ...] = ()
    tactile: tuple[TactileMatrix, ...] = ()
    sensors: tuple[TactileSensorSample, ...] = ()
    fault_statuses: tuple[
        JointFaultStatus | L20LiteFaultStatus | L6FaultStatus | O6FaultStatus, ...
    ] = ()
    origin: DataOrigin = DataOrigin.DEVICE_RESPONSE
    within_declared_range: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.semantic, TelemetrySemantic):
            raise ValidationError("semantic must be TelemetrySemantic")
        if not isinstance(self.raw, tuple) or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in self.raw
        ):
            raise ValidationError("raw must contain finite protocol numbers")
        if not isinstance(self.tactile, tuple) or any(
            not isinstance(value, TactileMatrix) for value in self.tactile
        ):
            raise ValidationError("tactile must contain TactileMatrix values")
        if not isinstance(self.sensors, tuple) or any(
            not isinstance(value, TactileSensorSample) for value in self.sensors
        ):
            raise ValidationError("sensors must contain TactileSensorSample values")
        if not isinstance(self.fault_statuses, tuple) or any(
            not isinstance(
                value,
                (JointFaultStatus, L20LiteFaultStatus, L6FaultStatus, O6FaultStatus),
            )
            for value in self.fault_statuses
        ):
            raise ValidationError("fault_statuses must contain decoded fault statuses")
        if not isinstance(self.origin, DataOrigin) or self.origin is DataOrigin.NONE:
            raise ValidationError("a payload must have a concrete data origin")
        if not isinstance(self.within_declared_range, bool):
            raise ValidationError("within_declared_range must be bool")
        if self.semantic is not TelemetrySemantic.FORCE and (
            self.tactile or self.sensors
        ):
            raise ValidationError("tactile data is only valid for force payloads")
        if self.semantic is not TelemetrySemantic.FAULTS and self.fault_statuses:
            raise ValidationError(
                "decoded fault statuses are only valid for fault payloads"
            )
        if self.fault_statuses and len(self.fault_statuses) != len(self.raw):
            raise ValidationError("decoded fault statuses must match raw values")
        if not self.raw and not self.tactile and not self.sensors:
            raise ValidationError("a sample payload cannot be empty")


@dataclass(frozen=True, slots=True)
class IdentityResult:
    """Verified identity/profile result produced by an identity transaction."""

    capabilities: DeviceCapabilities
    device_info: DeviceInfo
    fingerprint: Hashable

    def __post_init__(self) -> None:
        if not isinstance(self.capabilities, DeviceCapabilities):
            raise ValidationError("capabilities must be DeviceCapabilities")
        if not self.capabilities.identity.verified:
            raise ValidationError("identity capabilities must be verified")
        if not isinstance(self.device_info, DeviceInfo):
            raise ValidationError("device_info must be DeviceInfo")
        _require_hashable(self.fingerprint, "fingerprint")


@dataclass(frozen=True, slots=True)
class ResponseProgress:
    """Result of offering one routed message to a protocol transaction."""

    matched: bool
    complete: bool = False
    result: IdentityResult | SamplePayload | None = None
    publications: tuple[SamplePayload, ...] = ()
    acknowledged: bool = False

    def __post_init__(self) -> None:
        for name in ("matched", "complete", "acknowledged"):
            if not isinstance(getattr(self, name), bool):
                raise ValidationError(f"{name} must be bool")
        if not isinstance(self.publications, tuple) or any(
            not isinstance(value, SamplePayload) for value in self.publications
        ):
            raise ValidationError("publications must contain SamplePayload values")
        if not self.matched and (
            self.complete
            or self.result is not None
            or self.publications
            or self.acknowledged
        ):
            raise ValidationError(
                "an unmatched response cannot change transaction state"
            )
        if not self.complete and (self.result is not None or self.acknowledged):
            raise ValidationError("result and acknowledgement require completion")


ResponseConsumer = Callable[[object], ResponseProgress]
NextTransactionFactory = Callable[[], "ProtocolTransaction | None"]


@dataclass(frozen=True, slots=True)
class ProtocolTransaction:
    """One serializable request/response transaction built by a model codec."""

    key: Hashable
    correlation_key: Hashable
    request_messages: tuple[object, ...]
    response_timeout_s: float
    estimated_duration_s: float
    response_consumer: ResponseConsumer | None = None
    response_acknowledges_command: bool = False
    dispatch_publications: tuple[SamplePayload, ...] = ()
    request_interval_s: float = 0.0
    late_response_guard_s: float = 0.0
    next_transaction: ProtocolTransaction | None = None
    next_transaction_factory: NextTransactionFactory | None = None
    yield_after_response: bool = False
    firmware_optional: bool = False
    pre_dispatch_delay_s: float = 0.0
    post_dispatch_delay_s: float = 0.0

    def __post_init__(self) -> None:
        _require_hashable(self.key, "transaction key")
        _require_hashable(self.correlation_key, "correlation key")
        if not isinstance(self.request_messages, tuple) or not self.request_messages:
            raise ValidationError("request_messages must be a non-empty tuple")
        for name in (
            "response_timeout_s",
            "estimated_duration_s",
            "request_interval_s",
            "late_response_guard_s",
            "pre_dispatch_delay_s",
            "post_dispatch_delay_s",
        ):
            _finite_non_negative(getattr(self, name), name)
        if self.response_consumer is not None and not callable(self.response_consumer):
            raise ValidationError("response_consumer must be callable or None")
        if self.response_consumer is not None and self.response_timeout_s <= 0:
            raise ValidationError("response transactions need a positive timeout")
        if not isinstance(self.response_acknowledges_command, bool):
            raise ValidationError("response_acknowledges_command must be bool")
        if not isinstance(self.firmware_optional, bool):
            raise ValidationError("firmware_optional must be bool")
        if not isinstance(self.yield_after_response, bool):
            raise ValidationError("yield_after_response must be bool")
        if self.response_acknowledges_command and self.response_consumer is None:
            raise ValidationError("an ACK transaction must await a response")
        if not isinstance(self.dispatch_publications, tuple) or any(
            not isinstance(value, SamplePayload) for value in self.dispatch_publications
        ):
            raise ValidationError(
                "dispatch_publications must contain SamplePayload values"
            )
        if self.next_transaction is not None:
            if not isinstance(self.next_transaction, ProtocolTransaction):
                raise ValidationError(
                    "next_transaction must be ProtocolTransaction or None"
                )
            if self.response_consumer is None:
                raise ValidationError(
                    "a staged transaction must await a response before its next stage"
                )
        if (
            self.next_transaction is not None
            and self.next_transaction_factory is not None
        ):
            raise ValidationError(
                "a transaction cannot define both next_transaction and "
                "next_transaction_factory"
            )
        if self.next_transaction_factory is not None:
            if not callable(self.next_transaction_factory):
                raise ValidationError(
                    "next_transaction_factory must be callable or None"
                )
            if self.response_consumer is None:
                raise ValidationError(
                    "a dynamic staged transaction must await a response before "
                    "its next stage"
                )
        elif self.next_transaction is None and self.yield_after_response:
            raise ValidationError(
                "yield_after_response requires a following transaction stage"
            )

    @property
    def expects_response(self) -> bool:
        """Return whether completion requires a matching protocol response."""

        return self.response_consumer is not None

    @property
    def terminal_expects_response(self) -> bool:
        """Return whether the final response-gated stage expects a reply."""

        # A dynamic stage is intentionally unresolved until the current
        # response has been decoded.  The current stage is response-gated, so
        # returning its value is the only sound answer without invoking user
        # codec state from this metadata property.
        if self.next_transaction_factory is not None:
            return self.expects_response
        transaction = self
        while transaction.next_transaction is not None:
            transaction = transaction.next_transaction
        return transaction.expects_response

    @property
    def has_dynamic_next_transaction(self) -> bool:
        """Return whether the next stage is selected after this response."""

        return self.next_transaction_factory is not None

    def build_next_transaction(self) -> ProtocolTransaction | None:
        """Resolve a dynamic next stage at the response boundary."""

        if self.next_transaction_factory is None:
            return self.next_transaction
        next_transaction = self.next_transaction_factory()
        if next_transaction is not None and not isinstance(
            next_transaction, ProtocolTransaction
        ):
            raise ValidationError(
                "next_transaction_factory must return ProtocolTransaction or None"
            )
        return next_transaction

    @property
    def terminal_acknowledges_command(self) -> bool:
        """Return whether the final stage carries a protocol command ACK."""

        if self.next_transaction_factory is not None:
            return self.response_acknowledges_command
        transaction = self
        while transaction.next_transaction is not None:
            transaction = transaction.next_transaction
        return transaction.response_acknowledges_command

    def consume(self, message: object) -> ResponseProgress:
        """Offer a routed message without giving the runtime codec knowledge."""

        if self.response_consumer is None:
            return ResponseProgress(matched=False)
        progress = self.response_consumer(message)
        if not isinstance(progress, ResponseProgress):
            raise ValidationError("response_consumer must return ResponseProgress")
        return progress


@runtime_checkable
class ProtocolBackend(Protocol):
    """Model-specific transaction factory injected into ``DeviceRuntime``."""

    @property
    def endpoint_id(self) -> Hashable:
        """Return the transport-arbitration endpoint identity."""
        ...

    @property
    def route_keys(self) -> tuple[Hashable, ...]:
        """Return exact receive routes feeding one endpoint inbox."""
        ...

    def identity_transaction(
        self, capabilities: DeviceCapabilities
    ) -> ProtocolTransaction:
        """Build the identity and firmware-profile transaction."""
        ...

    def baseline_semantics(
        self, capabilities: DeviceCapabilities
    ) -> tuple[TelemetrySemantic, ...]:
        """Return non-motion state reads required before a generation is ready."""
        ...

    def command_transaction(
        self,
        semantic: CommandSemantic,
        values: tuple[RawNumber, ...],
        capabilities: DeviceCapabilities,
    ) -> ProtocolTransaction:
        """Encode one already validated public command."""
        ...

    def read_transaction(
        self,
        semantic: TelemetrySemantic,
        capabilities: DeviceCapabilities,
    ) -> ProtocolTransaction:
        """Build one active state read transaction."""
        ...

    def operation_transaction(
        self,
        operation: str,
        capabilities: DeviceCapabilities,
    ) -> ProtocolTransaction:
        """Build one bounded discrete operation transaction."""
        ...

    def decode_report(
        self,
        message: object,
        capabilities: DeviceCapabilities,
    ) -> tuple[SamplePayload, ...]:
        """Decode an unsolicited report, returning an empty tuple if unmatched."""
        ...


__all__ = [
    "IdentityResult",
    "NextTransactionFactory",
    "ProtocolBackend",
    "ProtocolTransaction",
    "ResponseProgress",
    "SamplePayload",
]
