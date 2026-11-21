"""Six-axis HOP backend, using installed V36 positions without hole writes."""

from __future__ import annotations

from ...api.enums import DataOrigin, HandSide
from ...api.errors import ProtocolError, UnsupportedOperationError
from ...api.readings import RawNumber
from ...capabilities.types import CommandSemantic, DeviceCapabilities, TelemetrySemantic
from ..base import ProtocolTransaction, ResponseProgress, SamplePayload
from .backend import HOPBackend
from .framing import (
    HOPAddressing,
    build_read_message,
    build_write_message,
)
from .identity import identity_transaction
from .objects import decode_fault_statuses
from .response import HOPRangeAssembler

# The order here is public API order, independent of logical wire position.
POSITIONS = (10, 5, 11, 12, 13, 14)
_READS = {
    TelemetrySemantic.ANGLES: (0x01, False),
    TelemetrySemantic.MEASURED_SPEEDS: (0x02, False),
    TelemetrySemantic.CONFIGURED_SPEEDS: (0x02, True),
    TelemetrySemantic.CONFIGURED_ACCELERATIONS: (0x03, True),
    TelemetrySemantic.CURRENTS: (0x04, False),
    TelemetrySemantic.CURRENT_LIMITS: (0x04, True),
    TelemetrySemantic.VOLTAGES: (0x05, False),
    TelemetrySemantic.TORQUES: (0x06, False),
    TelemetrySemantic.TORQUES_LIMITS: (0x06, True),
    TelemetrySemantic.TEMPERATURES: (0x07, False),
    TelemetrySemantic.MOTION_TIMES: (0x08, True),
    TelemetrySemantic.STALL_DETECTION_TIMES: (0x09, True),
    TelemetrySemantic.STALL_THRESHOLDS: (0x0A, True),
    TelemetrySemantic.STALL_HOLD_CURRENTS: (0x0B, True),
    TelemetrySemantic.FAULTS: (0x0C, False),
    TelemetrySemantic.POSITION_KP: (0x21, True),
    TelemetrySemantic.POSITION_KD: (0x23, True),
}
_COMMANDS = {
    CommandSemantic.ANGLES: (0x01, False),
    CommandSemantic.SPEEDS: (0x02, True),
    CommandSemantic.ACCELERATIONS: (0x03, True),
    CommandSemantic.CURRENT_LIMITS: (0x04, True),
    CommandSemantic.TORQUES: (0x06, False),
    CommandSemantic.TORQUES_LIMITS: (0x06, True),
    CommandSemantic.MOTION_TIMES: (0x08, True),
    CommandSemantic.STALL_DETECTION_TIMES: (0x09, True),
    CommandSemantic.STALL_THRESHOLDS: (0x0A, True),
    CommandSemantic.STALL_HOLD_CURRENTS: (0x0B, True),
    CommandSemantic.POSITION_KP: (0x21, True),
    CommandSemantic.POSITION_KD: (0x23, True),
}


def vector_transaction(
    main_index: int,
    rts: bool,
    addressing: HOPAddressing,
    capabilities: DeviceCapabilities,
    *,
    semantic: TelemetrySemantic | None = None,
    values: tuple[RawNumber, ...] | None = None,
) -> ProtocolTransaction:
    """Publish/ACK only after both ranges; writes are not mechanically atomic."""
    width = 2 if main_index in (0x21, 0x23) else 1
    received: dict[int, int] = {}
    spans = ((5, 1), (10, 5))

    def stage(index: int) -> ProtocolTransaction:
        start, count = spans[index]
        assembler = HOPRangeAssembler(
            main_index,
            sub_index=start * width,
            length=count * width,
            rts=rts,
            response_id=addressing.response_id,
        )
        if values is None:
            message = build_read_message(
                main_index,
                sub_index=start * width,
                length=count * width,
                rts=rts,
                request_id=addressing.request_id,
                brs=addressing.brs,
            )
        else:
            payload = b"".join(
                int(values[POSITIONS.index(pos)]).to_bytes(width, "little")
                for pos in range(start, start + count)
            )
            message = build_write_message(
                main_index,
                sub_index=start * width,
                payload=payload,
                rts=rts,
                request_id=addressing.request_id,
                brs=addressing.brs,
            )

        def consume(message: object) -> ResponseProgress:
            matched, complete = assembler.consume(message)
            if complete is None:
                return ResponseProgress(matched=matched)
            received.update(
                (
                    start + n,
                    int.from_bytes(complete[n * width : (n + 1) * width], "little"),
                )
                for n in range(count)
            )
            result = None
            if index == len(spans) - 1 and semantic is not None:
                raw = tuple(received[pos] for pos in POSITIONS)
                telemetry = capabilities.telemetry_channel(semantic)
                result = SamplePayload(
                    semantic=semantic,
                    raw=raw,
                    origin=DataOrigin.DEVICE_COMMAND_FEEDBACK
                    if values is not None
                    else (
                        DataOrigin.DEVICE_CONFIG_RESPONSE
                        if rts
                        else DataOrigin.DEVICE_RESPONSE
                    ),
                    within_declared_range=(
                        telemetry is None
                        or telemetry.raw_range is None
                        or all(telemetry.raw_range.contains(v) for v in raw)
                    ),
                    fault_statuses=decode_fault_statuses(raw)
                    if semantic is TelemetrySemantic.FAULTS
                    else (),
                )
            return ResponseProgress(
                matched=True,
                complete=True,
                result=result,
                acknowledged=values is not None,
            )

        return ProtocolTransaction(
            key=("hop_vector", main_index, rts),
            correlation_key=("hop", addressing.response_id),
            request_messages=(message,),
            response_timeout_s=0.1,
            estimated_duration_s=0.004,
            response_consumer=consume,
            response_acknowledges_command=values is not None,
            late_response_guard_s=0.01,
            next_transaction_factory=(lambda: stage(index + 1)) if index == 0 else None,
        )

    return stage(0)


class HOPSixAxisBackend(HOPBackend):
    """Reuse shared HOP identity/sensors and override model-specific objects."""

    def __init__(
        self,
        model: str,
        side: HandSide,
        *,
        request_id: int | None = None,
        brs: bool = False,
    ) -> None:
        super().__init__(
            side, request_id=1 if request_id is None else request_id, brs=brs
        )
        self._model = model

    def identity_transaction(
        self, capabilities: DeviceCapabilities
    ) -> ProtocolTransaction:
        if capabilities.model != self._model or capabilities.side is not self._side:
            raise ProtocolError("HOP capability identity changed")
        if not capabilities.identity.verified:
            self._sensor_metadata.clear()
            self._identity_fingerprint = None
        return identity_transaction(
            capabilities, self._addressing, on_identity=self._observe_identity
        )

    def command_transaction(
        self,
        semantic: CommandSemantic,
        values: tuple[RawNumber, ...],
        capabilities: DeviceCapabilities,
    ) -> ProtocolTransaction:
        command = capabilities.command(semantic)
        if command is None or semantic not in _COMMANDS:
            raise UnsupportedOperationError(
                f"{self._model} HOP does not support {semantic.value}"
            )
        if len(values) != 6 or any(
            type(v) is not int or not command.range_for(n).contains(v)
            for n, v in enumerate(values)
        ):
            raise ProtocolError(
                "HOP six-axis commands require six raw integers in 0..255"
            )
        mi, rts = _COMMANDS[semantic]
        return vector_transaction(
            mi,
            rts,
            self._addressing,
            capabilities,
            values=values,
            semantic=TelemetrySemantic.ANGLES
            if semantic is CommandSemantic.ANGLES
            else None,
        )

    def read_transaction(
        self, semantic: TelemetrySemantic, capabilities: DeviceCapabilities
    ) -> ProtocolTransaction:
        if semantic is TelemetrySemantic.FORCE:
            return self.sensor_transaction(capabilities)
        if capabilities.telemetry_channel(semantic) is None or semantic not in _READS:
            raise UnsupportedOperationError(
                f"{self._model} HOP does not support {semantic.value}"
            )
        mi, rts = _READS[semantic]
        return vector_transaction(
            mi, rts, self._addressing, capabilities, semantic=semantic
        )

    def operation_transaction(
        self, operation: str, capabilities: DeviceCapabilities
    ) -> ProtocolTransaction:
        if operation != "save_parameters":
            raise UnsupportedOperationError(
                f"{self._model} HOP does not support {operation}"
            )
        from .management import save_transaction

        return save_transaction(self._addressing)

    def can_configuration_transaction(self) -> ProtocolTransaction:
        from .management import configuration_transaction

        return configuration_transaction(self._addressing)

    def can_configuration_write(
        self, sub_index: int, payload: bytes
    ) -> ProtocolTransaction:
        from .management import configuration_write

        return configuration_write(self._addressing, sub_index, payload)


__all__ = ["POSITIONS", "HOPSixAxisBackend", "vector_transaction"]
