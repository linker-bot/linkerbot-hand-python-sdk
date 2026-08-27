"""Complete five-finger tactile transaction assembly for classic CAN."""

from __future__ import annotations

from dataclasses import dataclass

from linkerbot_transport import CANRouteKey

from ...api.enums import DataOrigin
from ...api.errors import ProtocolError, UnsupportedOperationError, ValidationError
from ...api.readings import TactileMatrix
from ...capabilities.types import DeviceCapabilities, TelemetrySemantic
from ..base import ProtocolTransaction, ResponseProgress, SamplePayload
from .classic_can import build_classic_message, matching_data

_FINGER_OPCODES = (0xB1, 0xB2, 0xB3, 0xB4, 0xB5)


@dataclass(frozen=True, slots=True)
class _TactileProgress:
    matched: bool
    matrices: tuple[TactileMatrix, ...] | None = None


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

    def offer(self, message: object) -> _TactileProgress:
        """Consume one row and return matrices only after atomic completion."""

        data = matching_data(message, self._route)
        if data is None or not data or data[0] not in self._frames:
            return _TactileProgress(matched=False)
        expected_length = self._columns + 2
        if len(data) != expected_length:
            raise ProtocolError(
                f"tactile response must contain {expected_length} bytes"
            )
        row_index = _row_index(data[1], self._rows)
        payload = data[2:]
        finger_frames = self._frames[data[0]]
        previous = finger_frames.get(row_index)
        if previous is not None and previous != payload:
            raise ProtocolError("tactile row conflicts with a duplicate")
        finger_frames[row_index] = payload
        if not all(len(rows) == self._rows for rows in self._frames.values()):
            return _TactileProgress(matched=True)
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
        return _TactileProgress(matched=True, matrices=matrices)


def tactile_transaction(
    capabilities: DeviceCapabilities,
    *,
    route: CANRouteKey,
    selector: int,
    rows: int,
    columns: int,
    response_timeout_s: float,
    late_response_guard_s: float,
) -> ProtocolTransaction:
    """Build one pipelined request that publishes all five matrices atomically."""

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
    assembler = TactileAssembler(
        route=route,
        finger_names=telemetry.channels,
        rows=rows,
        columns=columns,
    )

    def consume(message: object) -> ResponseProgress:
        progress = assembler.offer(message)
        if not progress.matched:
            return ResponseProgress(matched=False)
        if progress.matrices is None:
            return ResponseProgress(matched=True)
        return ResponseProgress(
            matched=True,
            complete=True,
            result=SamplePayload(
                semantic=TelemetrySemantic.FORCE,
                tactile=progress.matrices,
                origin=DataOrigin.DEVICE_RESPONSE,
            ),
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
        request_interval_s=0,
        late_response_guard_s=late_response_guard_s,
    )


def _row_index(marker: int, row_count: int) -> int:
    if marker < row_count:
        return marker
    if marker & 0x0F == 0 and marker >> 4 < row_count:
        return marker >> 4
    raise ProtocolError("tactile row marker is outside the declared matrix")


__all__ = ["TactileAssembler", "tactile_transaction"]
