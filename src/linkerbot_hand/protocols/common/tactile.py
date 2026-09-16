"""Complete five-finger tactile transaction assembly for classic CAN."""

from __future__ import annotations

from dataclasses import dataclass

from linkerbot_transport import CANRouteKey

from ...api.enums import DataOrigin
from ...api.errors import ProtocolError, UnsupportedOperationError, ValidationError
from ...api.readings import TactileMatrix
from ...capabilities.types import (
    DeviceCapabilities,
    TelemetryCapability,
    TelemetrySemantic,
)
from ..base import ProtocolTransaction, ResponseProgress, SamplePayload
from .classic_can import build_classic_message, matching_data

_FINGER_OPCODES = (0xB1, 0xB2, 0xB3, 0xB4, 0xB5)


@dataclass(frozen=True, slots=True)
class _TactileProgress:
    matched: bool
    finger_complete: bool = False
    matrices: tuple[TactileMatrix, ...] | None = None


_TACTILE_UNMATCHED = _TactileProgress(matched=False)
_TACTILE_MATCHED = _TactileProgress(matched=True)
_TACTILE_FINGER_COMPLETE = _TactileProgress(
    matched=True,
    finger_complete=True,
)
_RESPONSE_MATCHED = ResponseProgress(matched=True)
_RESPONSE_COMPLETE = ResponseProgress(matched=True, complete=True)


class TactileAssembler:
    """Assemble all rows for all five fingers before exposing one sample."""

    def __init__(
        self,
        *,
        route: CANRouteKey,
        finger_names: tuple[str, ...],
        rows: int,
        columns: int,
    ) -> None:
        if len(finger_names) != len(_FINGER_OPCODES):
            raise ValidationError("classic tactile profile must declare five fingers")
        if type(rows) is not int or rows <= 0 or rows > 16:
            raise ValidationError("tactile rows must be in 1..16")
        if type(columns) is not int or not 1 <= columns <= 6:
            raise ValidationError("classic tactile columns must be in 1..6")
        self._route = route
        self._finger_names = finger_names
        self._rows = rows
        self._columns = columns
        self._frames: dict[int, dict[int, bytes]] = {
            opcode: {} for opcode in _FINGER_OPCODES
        }

    def offer(
        self,
        message: object,
        *,
        expected_opcode: int | None = None,
    ) -> _TactileProgress:
        """Consume one row and return matrices only after atomic completion."""

        data = matching_data(message, self._route)
        if (
            data is None
            or not data
            or data[0] not in self._frames
            or (expected_opcode is not None and data[0] != expected_opcode)
        ):
            return _TACTILE_UNMATCHED
        expected_length = self._columns + 2
        if len(data) != expected_length:
            # A hand whose matrix is not the layout this SDK assembles lands
            # here, so name the layout rather than only the byte count.
            raise ProtocolError(
                f"tactile response must contain {expected_length} bytes for the "
                f"{self._rows} by {self._columns} layout this model assembles; "
                f"got {len(data)}"
            )
        row_index = _row_index(data[1], self._rows)
        payload = data[2:]
        finger_frames = self._frames[data[0]]
        previous = finger_frames.get(row_index)
        if previous is not None and previous != payload:
            raise ProtocolError("tactile row conflicts with a duplicate")
        finger_frames[row_index] = payload
        finger_complete = len(finger_frames) == self._rows
        if not all(len(rows) == self._rows for rows in self._frames.values()):
            return _TACTILE_FINGER_COMPLETE if finger_complete else _TACTILE_MATCHED
        matrices = tuple(
            TactileMatrix(
                name=name,
                rows=self._rows,
                columns=self._columns,
                raw=tuple(
                    tuple(self._frames[opcode][row]) for row in range(self._rows)
                ),
            )
            for name, opcode in zip(
                self._finger_names,
                _FINGER_OPCODES,
                strict=True,
            )
        )
        return _TactileProgress(
            matched=True,
            finger_complete=True,
            matrices=matrices,
        )


def tactile_transaction(
    capabilities: DeviceCapabilities,
    *,
    route: CANRouteKey,
    selector: int,
    rows: int,
    columns: int,
    request_interval_s: float,
    response_timeout_s: float,
    late_response_guard_s: float,
) -> ProtocolTransaction:
    """Build one pipelined request that publishes all five matrices atomically."""

    telemetry, assembler = _tactile_context(
        capabilities,
        route=route,
        rows=rows,
        columns=columns,
    )

    def consume(message: object) -> ResponseProgress:
        progress = assembler.offer(message)
        if not progress.matched:
            return ResponseProgress(matched=False)
        if progress.matrices is None:
            return _RESPONSE_MATCHED
        return ResponseProgress(
            matched=True,
            complete=True,
            result=_force_payload(progress.matrices),
        )

    return ProtocolTransaction(
        key=("read", TelemetrySemantic.FORCE),
        correlation_key=("tactile", route),
        request_messages=tuple(
            build_classic_message(route, bytes((opcode, selector)))
            for opcode in _FINGER_OPCODES
        ),
        response_timeout_s=response_timeout_s,
        estimated_duration_s=telemetry.estimated_transaction_s,
        response_consumer=consume,
        request_interval_s=request_interval_s,
        late_response_guard_s=late_response_guard_s,
    )


def response_gated_tactile_transaction(
    capabilities: DeviceCapabilities,
    *,
    route: CANRouteKey,
    selector: int,
    rows: int,
    columns: int,
    response_timeout_s: float,
    late_response_guard_s: float,
) -> ProtocolTransaction:
    """Build five stages that query the next finger after the prior response."""

    telemetry, assembler = _tactile_context(
        capabilities,
        route=route,
        rows=rows,
        columns=columns,
    )
    next_transaction: ProtocolTransaction | None = None
    for opcode in reversed(_FINGER_OPCODES):
        final_stage = next_transaction is None

        def consume(
            message: object,
            *,
            expected_opcode: int = opcode,
            publishes_result: bool = final_stage,
        ) -> ResponseProgress:
            progress = assembler.offer(message, expected_opcode=expected_opcode)
            if not progress.matched:
                return ResponseProgress(matched=False)
            if not progress.finger_complete:
                return _RESPONSE_MATCHED
            if not publishes_result:
                return _RESPONSE_COMPLETE
            if progress.matrices is None:
                raise ProtocolError("final tactile stage completed without all fingers")
            return ResponseProgress(
                matched=True,
                complete=True,
                result=_force_payload(progress.matrices),
            )

        next_transaction = ProtocolTransaction(
            key=("read", TelemetrySemantic.FORCE),
            correlation_key=("tactile", route, opcode),
            request_messages=(build_classic_message(route, bytes((opcode, selector))),),
            response_timeout_s=response_timeout_s,
            estimated_duration_s=telemetry.estimated_transaction_s
            / len(_FINGER_OPCODES),
            response_consumer=consume,
            late_response_guard_s=late_response_guard_s,
            next_transaction=next_transaction,
            yield_after_response=not final_stage,
        )
    if next_transaction is None:  # pragma: no cover - fixed non-empty opcode set
        raise ProtocolError("classic tactile profile has no finger opcodes")
    return next_transaction


def _tactile_context(
    capabilities: DeviceCapabilities,
    *,
    route: CANRouteKey,
    rows: int,
    columns: int,
) -> tuple[TelemetryCapability, TactileAssembler]:
    telemetry = capabilities.telemetry_channel(TelemetrySemantic.FORCE)
    if telemetry is None or not telemetry.device_queried:
        raise UnsupportedOperationError(
            f"{capabilities.model} profile does not support tactile queries"
        )
    expected_shape = (len(_FINGER_OPCODES), rows, columns)
    if telemetry.shape != expected_shape:
        raise ProtocolError(
            f"tactile capability shape must be {expected_shape}, got {telemetry.shape}"
        )
    return telemetry, TactileAssembler(
        route=route,
        finger_names=telemetry.channels,
        rows=rows,
        columns=columns,
    )


def _force_payload(matrices: tuple[TactileMatrix, ...]) -> SamplePayload:
    return SamplePayload(
        semantic=TelemetrySemantic.FORCE,
        tactile=matrices,
        origin=DataOrigin.DEVICE_RESPONSE,
    )


def _row_index(marker: int, row_count: int) -> int:
    if marker < row_count:
        return marker
    if marker & 0x0F == 0 and marker >> 4 < row_count:
        return marker >> 4
    raise ProtocolError("tactile row marker is outside the declared matrix")


__all__ = [
    "TactileAssembler",
    "response_gated_tactile_transaction",
    "tactile_transaction",
]
