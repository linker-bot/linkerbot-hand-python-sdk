"""O30 protocol backend for the unified device runtime."""

from __future__ import annotations

from linkerbot_transport import CANRouteKey

from ...api.enums import HandSide
from ...api.errors import ProtocolError, UnsupportedOperationError
from ...api.readings import RawNumber, TactileSensorMetadata
from ...capabilities.types import (
    CommandSemantic,
    DeviceCapabilities,
    TelemetrySemantic,
)
from ..base import ProtocolTransaction, SamplePayload
from .control import command_transaction
from .framing import addressing_for_side, response_route
from .identity import o30_identity_transaction
from .sensors import tactile_transaction
from .telemetry import read_transaction

_BASELINE = (
    TelemetrySemantic.ANGLES,
    TelemetrySemantic.FAULTS,
    TelemetrySemantic.STALL_DETECTION_TIMES,
    TelemetrySemantic.STALL_THRESHOLDS,
    TelemetrySemantic.STALL_HOLD_CURRENTS,
)


class O30ProtocolBackend:
    """O30 HOP profile; CAN ID pair is fixed per side (right 0x001, left 0x002)."""

    def __init__(self, side: HandSide) -> None:
        if side not in {HandSide.LEFT, HandSide.RIGHT}:
            raise ProtocolError("O30 protocol requires a left or right side")
        self._side = side
        self._addressing = addressing_for_side(side)
        self._endpoint_id = (
            "o30",
            self._addressing.request_id,
            self._addressing.response_id,
        )
        self._route_keys = (response_route(self._addressing.response_id),)
        self._sensor_metadata: dict[int, TactileSensorMetadata] = {}

    @property
    def endpoint_id(self) -> tuple[str, int, int]:
        """Return the physical HOP endpoint used for TX arbitration."""

        return self._endpoint_id

    @property
    def route_keys(self) -> tuple[CANRouteKey, ...]:
        """Return the exact standard response-ID route."""

        return self._route_keys

    def identity_transaction(
        self,
        capabilities: DeviceCapabilities,
    ) -> ProtocolTransaction:
        """Build the response-gated field-wise identity read."""

        if capabilities.side is not self._side:
            raise ProtocolError("O30 capability side changed after backend creation")
        # Identity runs once per connection generation. Force the next tactile
        # read to re-probe metadata after reconnect or firmware replacement.
        self._sensor_metadata.clear()
        return o30_identity_transaction(capabilities, self._addressing)

    def baseline_semantics(
        self,
        capabilities: DeviceCapabilities,
    ) -> tuple[TelemetrySemantic, ...]:
        """Return motion, fault, and safety configuration required before ready."""

        for semantic in _BASELINE:
            telemetry = capabilities.telemetry_channel(semantic)
            if telemetry is None or not telemetry.device_queried:
                raise ProtocolError(
                    f"O30 baseline semantic {semantic.value} is not queryable"
                )
        return _BASELINE

    def command_transaction(
        self,
        semantic: CommandSemantic,
        values: tuple[RawNumber, ...],
        capabilities: DeviceCapabilities,
    ) -> ProtocolTransaction:
        """Build one V25 write completed only by its HOP response."""

        return command_transaction(semantic, values, capabilities, self._addressing)

    def read_transaction(
        self,
        semantic: TelemetrySemantic,
        capabilities: DeviceCapabilities,
    ) -> ProtocolTransaction:
        """Build one V25 actual-state or set-point query."""

        if semantic is TelemetrySemantic.FORCE:
            return tactile_transaction(
                capabilities,
                self._addressing,
                metadata_cache=self._sensor_metadata,
            )
        return read_transaction(semantic, capabilities, self._addressing)

    def operation_transaction(
        self,
        operation: str,
        capabilities: DeviceCapabilities,
    ) -> ProtocolTransaction:
        """Reject operations not represented by a verified O30 object."""

        del capabilities
        raise UnsupportedOperationError(f"O30 does not support {operation}")

    def decode_report(
        self,
        message: object,
        capabilities: DeviceCapabilities,
    ) -> tuple[SamplePayload, ...]:
        """Ignore unsolicited traffic; the profile uses explicit HOP queries."""

        del message, capabilities
        return ()


__all__ = ["O30ProtocolBackend"]
