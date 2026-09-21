"""Transaction factory shared by opcode-based classic six-axis profiles."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from linkerbot_transport import CANRouteKey

from ...api.enums import DataOrigin
from ...api.errors import ProtocolError, UnsupportedOperationError
from ...api.readings import L6FaultStatus, RawNumber
from ...capabilities.types import (
    CommandSemantic,
    DeviceCapabilities,
    TelemetrySemantic,
)
from ..base import (
    ProtocolTransaction,
    ResponseProgress,
    SamplePayload,
)
from .classic_can import build_classic_message, decode_u8_vector

IdentityTransactionFactory = Callable[[DeviceCapabilities], ProtocolTransaction]


class ClassicSixAxisBackend:
    """Stateless backend for one six-axis opcode profile and endpoint route."""

    def __init__(
        self,
        *,
        route: CANRouteKey,
        identity_transaction: IdentityTransactionFactory,
        command_opcodes: Mapping[CommandSemantic, int],
        telemetry_opcodes: Mapping[TelemetrySemantic, int],
        baseline: tuple[TelemetrySemantic, ...],
        operation_payloads: Mapping[str, bytes] | None = None,
        config_telemetry: tuple[TelemetrySemantic, ...] = (),
        firmware_optional_reads: tuple[TelemetrySemantic, ...] = (),
        decode_fault_statuses: Callable[[tuple[int, ...]], tuple[L6FaultStatus, ...]]
        | None = None,
    ) -> None:
        self._route = route
        self._identity_transaction = identity_transaction
        self._command_opcodes = dict(command_opcodes)
        self._telemetry_opcodes = dict(telemetry_opcodes)
        self._baseline = baseline
        self._operation_payloads = dict(operation_payloads or {})
        self._config_telemetry = frozenset(config_telemetry)
        self._firmware_optional_reads = frozenset(firmware_optional_reads)
        self._decode_fault_statuses = decode_fault_statuses

    @property
    def endpoint_id(self) -> CANRouteKey:
        """Return the exact endpoint key used by shared TX latest slots."""

        return self._route

    @property
    def route_keys(self) -> tuple[CANRouteKey, ...]:
        """Return the sole standard-frame receive route for this hand."""

        return (self._route,)

    def identity_transaction(
        self,
        capabilities: DeviceCapabilities,
    ) -> ProtocolTransaction:
        """Build a fresh stateful identity consumer for one transaction."""

        return self._identity_transaction(capabilities)

    def baseline_semantics(
        self,
        capabilities: DeviceCapabilities,
    ) -> tuple[TelemetrySemantic, ...]:
        """Return supported safety state read before opening admission."""

        for semantic in self._baseline:
            if capabilities.telemetry_channel(semantic) is None:
                raise ProtocolError(
                    f"baseline semantic {semantic.value} is absent from capabilities"
                )
        return self._baseline

    def command_transaction(
        self,
        semantic: CommandSemantic,
        values: tuple[RawNumber, ...],
        capabilities: DeviceCapabilities,
    ) -> ProtocolTransaction:
        """Encode one six-byte setting and capture verified angle feedback."""

        command = capabilities.command(semantic)
        if command is None:
            raise UnsupportedOperationError(
                f"{capabilities.model} does not support {semantic.value}"
            )
        try:
            opcode = self._command_opcodes[semantic]
        except KeyError as error:
            raise UnsupportedOperationError(
                f"no protocol command for {semantic.value}"
            ) from error
        raw = _u8_vector(values, semantic.value)
        response_consumer = None
        response_timeout_s = 0.0
        late_response_guard_s = 0.0
        if semantic is CommandSemantic.ANGLES and command.returns_current_state:
            telemetry = capabilities.telemetry_channel(TelemetrySemantic.ANGLES)
            if telemetry is None:
                raise ProtocolError("angle command has no angle telemetry")

            def consume(message: object) -> ResponseProgress:
                echoed = decode_u8_vector(
                    message,
                    route=self._route,
                    opcode=opcode,
                    count=6,
                )
                if echoed is None:
                    return ResponseProgress(matched=False)
                return ResponseProgress(
                    matched=True,
                    complete=True,
                    result=SamplePayload(
                        semantic=TelemetrySemantic.ANGLES,
                        raw=echoed,
                        origin=DataOrigin.DEVICE_COMMAND_FEEDBACK,
                        within_declared_range=_within_range(
                            echoed,
                            telemetry.raw_range,
                        ),
                    ),
                )

            response_consumer = consume
            response_timeout_s = 0.05
            late_response_guard_s = 0.005
        return ProtocolTransaction(
            key=("command", semantic),
            correlation_key=("opcode", self._route, opcode),
            request_messages=(
                build_classic_message(self._route, bytes((opcode, *raw))),
            ),
            response_timeout_s=response_timeout_s,
            estimated_duration_s=0.001,
            response_consumer=response_consumer,
            late_response_guard_s=late_response_guard_s,
        )

    def read_transaction(
        self,
        semantic: TelemetrySemantic,
        capabilities: DeviceCapabilities,
    ) -> ProtocolTransaction:
        """Build one active query that publishes only an exact matching reply."""

        telemetry = capabilities.telemetry_channel(semantic)
        if telemetry is None or not telemetry.device_queried:
            raise UnsupportedOperationError(
                f"{semantic.value} has no active protocol query"
            )
        try:
            opcode = self._telemetry_opcodes[semantic]
        except KeyError as error:
            raise UnsupportedOperationError(
                f"no protocol read for {semantic.value}"
            ) from error

        def consume(message: object) -> ResponseProgress:
            raw = decode_u8_vector(
                message,
                route=self._route,
                opcode=opcode,
                count=6,
            )
            if raw is None:
                return ResponseProgress(matched=False)
            return ResponseProgress(
                matched=True,
                complete=True,
                result=SamplePayload(
                    semantic=semantic,
                    raw=raw,
                    origin=self._response_origin(semantic),
                    fault_statuses=self._fault_statuses(semantic, raw),
                    within_declared_range=_within_range(raw, telemetry.raw_range),
                ),
            )

        return ProtocolTransaction(
            key=("read", semantic),
            correlation_key=("opcode", self._route, opcode),
            request_messages=(build_classic_message(self._route, bytes((opcode,))),),
            response_timeout_s=0.05,
            estimated_duration_s=telemetry.estimated_transaction_s,
            response_consumer=consume,
            late_response_guard_s=0.005,
            firmware_optional=semantic in self._firmware_optional_reads,
        )

    def operation_transaction(
        self,
        operation: str,
        capabilities: DeviceCapabilities,
    ) -> ProtocolTransaction:
        """Encode one supported dispatch-only discrete operation."""

        if not getattr(capabilities.operations, operation, False):
            raise UnsupportedOperationError(
                f"{capabilities.model} does not support {operation}"
            )
        try:
            payload = self._operation_payloads[operation]
        except KeyError as error:
            raise UnsupportedOperationError(
                f"no protocol operation for {operation}"
            ) from error
        return ProtocolTransaction(
            key=("operation", operation),
            correlation_key=("operation", self._route, operation),
            request_messages=(build_classic_message(self._route, payload),),
            response_timeout_s=0,
            estimated_duration_s=0.001,
        )

    def decode_report(
        self,
        message: object,
        capabilities: DeviceCapabilities,
    ) -> tuple[SamplePayload, ...]:
        """Decode one complete unsolicited vector while preserving raw values."""

        for semantic, opcode in self._telemetry_opcodes.items():
            telemetry = capabilities.telemetry_channel(semantic)
            if telemetry is None:
                continue
            # Unsolicited traffic may collide with a telemetry opcode without
            # being a response to us. Per the ``decode_report`` contract that is
            # "unmatched", so a length mismatch must not raise here.
            raw = decode_u8_vector(
                message,
                route=self._route,
                opcode=opcode,
                count=6,
                strict=False,
            )
            if raw is None:
                continue
            return (
                SamplePayload(
                    semantic=semantic,
                    raw=raw,
                    origin=(
                        self._response_origin(semantic)
                        if semantic in self._config_telemetry
                        else DataOrigin.DEVICE_REPORT
                    ),
                    fault_statuses=self._fault_statuses(semantic, raw),
                    within_declared_range=_within_range(raw, telemetry.raw_range),
                ),
            )
        return ()

    def _fault_statuses(
        self, semantic: TelemetrySemantic, raw: tuple[int, ...]
    ) -> tuple[L6FaultStatus, ...]:
        if semantic is TelemetrySemantic.FAULTS and self._decode_fault_statuses:
            return self._decode_fault_statuses(raw)
        return ()

    def _response_origin(self, semantic: TelemetrySemantic) -> DataOrigin:
        return (
            DataOrigin.DEVICE_CONFIG_RESPONSE
            if semantic in self._config_telemetry
            else DataOrigin.DEVICE_RESPONSE
        )


def _u8_vector(values: tuple[RawNumber, ...], label: str) -> tuple[int, ...]:
    if len(values) != 6 or any(type(value) is not int for value in values):
        raise ProtocolError(f"{label} must contain six integer protocol bytes")
    raw = tuple(int(value) for value in values)
    if any(not 0 <= value <= 0xFF for value in raw):
        raise ProtocolError(f"{label} protocol bytes must be in 0..255")
    return raw


def _within_range(raw: tuple[int, ...], raw_range: object | None) -> bool:
    if raw_range is None:
        return True
    contains = getattr(raw_range, "contains", None)
    if not callable(contains):
        raise ProtocolError("telemetry raw range has no contains() contract")
    return all(contains(value) for value in raw)


__all__ = ["ClassicSixAxisBackend", "IdentityTransactionFactory"]
