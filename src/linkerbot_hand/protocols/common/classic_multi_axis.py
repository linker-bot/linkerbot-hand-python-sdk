"""Multi-frame unsigned-byte objects used by classic CAN hand profiles."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from linkerbot_transport import CANRouteKey

from ...api.enums import DataOrigin
from ...api.errors import ProtocolError, UnsupportedOperationError
from ...api.readings import (
    JointFaultStatus,
    L20FaultStatus,
    L20LiteFaultStatus,
    RawNumber,
)
from ...capabilities.types import (
    CommandCapability,
    CommandSemantic,
    DeviceCapabilities,
    NumericRange,
    TelemetrySemantic,
)
from ..base import ProtocolTransaction, ResponseProgress, SamplePayload
from .classic_can import build_classic_message
from .classic_layout import ClassicObjectAssembler, ClassicObjectLayout

IdentityTransactionFactory = Callable[[DeviceCapabilities], ProtocolTransaction]


class ClassicMultiAxisBackend:
    """Stateless backend for classic CAN objects split across several frames."""

    def __init__(
        self,
        *,
        route: CANRouteKey,
        identity_transaction: IdentityTransactionFactory,
        command_layouts: Mapping[CommandSemantic, ClassicObjectLayout],
        telemetry_layouts: Mapping[TelemetrySemantic, ClassicObjectLayout],
        baseline: tuple[TelemetrySemantic, ...],
        operation_payloads: Mapping[str, tuple[bytes, ...]] | None = None,
        config_telemetry: tuple[TelemetrySemantic, ...] = (),
        firmware_optional_reads: tuple[TelemetrySemantic, ...] = (),
        request_interval_s: float = 0.0005,
        decode_fault_statuses: Callable[
            [tuple[int, ...]],
            tuple[JointFaultStatus | L20LiteFaultStatus | L20FaultStatus, ...],
        ]
        | None = None,
    ) -> None:
        self._route = route
        self._identity_transaction = identity_transaction
        self._command_layouts = dict(command_layouts)
        self._telemetry_layouts = dict(telemetry_layouts)
        self._baseline = baseline
        self._operation_payloads = dict(operation_payloads or {})
        self._config_telemetry = frozenset(config_telemetry)
        self._firmware_optional_reads = frozenset(firmware_optional_reads)
        self._request_interval_s = request_interval_s
        self._decode_fault_statuses = decode_fault_statuses

    @property
    def endpoint_id(self) -> CANRouteKey:
        """Return the endpoint key used by shared transport arbitration."""

        return self._route

    @property
    def route_keys(self) -> tuple[CANRouteKey, ...]:
        """Return the sole standard-frame receive route."""

        return (self._route,)

    def identity_transaction(
        self, capabilities: DeviceCapabilities
    ) -> ProtocolTransaction:
        """Build a fresh model identity transaction."""

        return self._identity_transaction(capabilities)

    def baseline_semantics(
        self, capabilities: DeviceCapabilities
    ) -> tuple[TelemetrySemantic, ...]:
        """Return supported state reads required before command admission."""

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
        """Encode an exact vector and atomically assemble angle feedback."""

        command = capabilities.command(semantic)
        if command is None:
            raise UnsupportedOperationError(
                f"{capabilities.model} does not support {semantic.value}"
            )
        try:
            layout = self._command_layouts[semantic]
        except KeyError as error:
            raise UnsupportedOperationError(
                f"no protocol command for {semantic.value}"
            ) from error
        raw = _validated_u8(values, command, semantic.value)
        response_consumer = None
        response_timeout_s = 0.0
        late_response_guard_s = 0.0
        if semantic is CommandSemantic.ANGLES and command.returns_current_state:
            telemetry = capabilities.telemetry_channel(TelemetrySemantic.ANGLES)
            if telemetry is None:
                raise ProtocolError("angle command has no angle telemetry")
            assembler = ClassicObjectAssembler(route=self._route, layout=layout)

            def consume(message: object) -> ResponseProgress:
                matched, echoed = assembler.offer(message)
                if not matched:
                    return ResponseProgress(matched=False)
                if echoed is None:
                    return ResponseProgress(matched=True)
                return ResponseProgress(
                    matched=True,
                    complete=True,
                    result=SamplePayload(
                        semantic=TelemetrySemantic.ANGLES,
                        raw=echoed,
                        origin=DataOrigin.DEVICE_COMMAND_FEEDBACK,
                        within_declared_range=_within_declared_range(
                            echoed,
                            TelemetrySemantic.ANGLES,
                            telemetry.raw_range,
                            capabilities,
                        ),
                    ),
                )

            response_consumer = consume
            response_timeout_s = 0.1
            late_response_guard_s = 0.005
        return ProtocolTransaction(
            key=("command", semantic),
            correlation_key=("classic_object", self._route, layout.opcodes),
            request_messages=layout.encode(raw, self._route),
            response_timeout_s=response_timeout_s,
            estimated_duration_s=_batch_duration(
                len(layout.frames), self._request_interval_s
            ),
            response_consumer=response_consumer,
            request_interval_s=self._request_interval_s,
            late_response_guard_s=late_response_guard_s,
        )

    def read_transaction(
        self,
        semantic: TelemetrySemantic,
        capabilities: DeviceCapabilities,
    ) -> ProtocolTransaction:
        """Query all fragments and publish one atomically assembled sample."""

        telemetry = capabilities.telemetry_channel(semantic)
        if telemetry is None or not telemetry.device_queried:
            raise UnsupportedOperationError(
                f"{semantic.value} has no active protocol query"
            )
        try:
            layout = self._telemetry_layouts[semantic]
        except KeyError as error:
            raise UnsupportedOperationError(
                f"no protocol read for {semantic.value}"
            ) from error
        assembler = ClassicObjectAssembler(route=self._route, layout=layout)

        def consume(message: object) -> ResponseProgress:
            matched, raw = assembler.offer(message)
            if not matched:
                return ResponseProgress(matched=False)
            if raw is None:
                return ResponseProgress(matched=True)
            return ResponseProgress(
                matched=True,
                complete=True,
                result=SamplePayload(
                    semantic=semantic,
                    raw=raw,
                    origin=self._response_origin(semantic),
                    fault_statuses=(
                        self._decode_fault_statuses(raw)
                        if semantic is TelemetrySemantic.FAULTS
                        and self._decode_fault_statuses is not None
                        else ()
                    ),
                    within_declared_range=_within_declared_range(
                        raw, semantic, telemetry.raw_range, capabilities
                    ),
                ),
            )

        return ProtocolTransaction(
            key=("read", semantic),
            correlation_key=("classic_object", self._route, layout.opcodes),
            request_messages=layout.query(self._route),
            response_timeout_s=0.1,
            estimated_duration_s=telemetry.estimated_transaction_s,
            response_consumer=consume,
            request_interval_s=self._request_interval_s,
            late_response_guard_s=0.005,
            firmware_optional=semantic in self._firmware_optional_reads,
        )

    def operation_transaction(
        self,
        operation: str,
        capabilities: DeviceCapabilities,
    ) -> ProtocolTransaction:
        """Encode one supported bounded discrete operation."""

        if not getattr(capabilities.operations, operation, False):
            raise UnsupportedOperationError(
                f"{capabilities.model} does not support {operation}"
            )
        try:
            payloads = self._operation_payloads[operation]
        except KeyError as error:
            raise UnsupportedOperationError(
                f"no protocol operation for {operation}"
            ) from error
        return ProtocolTransaction(
            key=("operation", operation),
            correlation_key=("operation", self._route, operation),
            request_messages=tuple(
                build_classic_message(self._route, value) for value in payloads
            ),
            response_timeout_s=0,
            estimated_duration_s=_batch_duration(
                len(payloads), self._request_interval_s
            ),
            request_interval_s=self._request_interval_s,
        )

    def decode_report(
        self,
        message: object,
        capabilities: DeviceCapabilities,
    ) -> tuple[SamplePayload, ...]:
        """Ignore partial reports; active transactions own multi-frame assembly."""

        return ()

    def _response_origin(self, semantic: TelemetrySemantic) -> DataOrigin:
        return (
            DataOrigin.DEVICE_CONFIG_RESPONSE
            if semantic in self._config_telemetry
            else DataOrigin.DEVICE_RESPONSE
        )


def _validated_u8(
    values: tuple[RawNumber, ...], command: CommandCapability, label: str
) -> tuple[int, ...]:
    if len(values) != len(command.channels) or any(
        type(value) is not int for value in values
    ):
        raise ProtocolError(
            f"{label} must contain {len(command.channels)} integer protocol bytes"
        )
    raw = tuple(int(value) for value in values)
    if any(
        not 0 <= value <= 0xFF or not command.range_for(index).contains(value)
        for index, value in enumerate(raw)
    ):
        raise ProtocolError(f"{label} values are outside the declared u8 ranges")
    return raw


def _within_declared_range(
    raw: tuple[int, ...],
    semantic: TelemetrySemantic,
    raw_range: NumericRange | None,
    capabilities: DeviceCapabilities,
) -> bool:
    if raw_range is not None:
        return all(raw_range.contains(value) for value in raw)
    if semantic is TelemetrySemantic.ANGLES:
        return len(raw) == len(capabilities.joints) and all(
            joint.raw_angle_range.contains(value)
            for joint, value in zip(capabilities.joints, raw, strict=True)
        )
    return True


def _batch_duration(frame_count: int, interval_s: float) -> float:
    return max(0.001, max(0, frame_count - 1) * interval_s)


__all__ = [
    "ClassicMultiAxisBackend",
]
