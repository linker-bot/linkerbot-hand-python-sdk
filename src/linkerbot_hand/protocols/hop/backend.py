"""Shared HOP endpoint state, identity, baseline and sensor handling."""

from __future__ import annotations

from linkerbot_transport import CANRouteKey

from ...api.enums import HandSide
from ...api.errors import ProtocolError, ValidationError
from ...api.readings import TactileSensorMetadata
from ...capabilities.types import (
    DeviceCapabilities,
    TelemetrySemantic,
)
from ..base import IdentityResult, ProtocolTransaction, SamplePayload
from .framing import HOPAddressing, response_route
from .identity import identity_transaction
from .sensors import tactile_transaction

_BASELINE = (
    TelemetrySemantic.ANGLES,
    TelemetrySemantic.FAULTS,
    TelemetrySemantic.STALL_DETECTION_TIMES,
    TelemetrySemantic.STALL_THRESHOLDS,
    TelemetrySemantic.STALL_HOLD_CURRENTS,
)


class HOPBackend:
    """Model-independent HOP mechanics; subclasses define joint object layout."""

    def __init__(self, side: HandSide, *, request_id: int, brs: bool = False) -> None:
        if side not in {HandSide.LEFT, HandSide.RIGHT}:
            raise ProtocolError("HOP protocol requires a left or right side")
        self._side = side
        if type(request_id) is not int:
            raise ValidationError("HOP request_id must be int")
        self._addressing = HOPAddressing(request_id, request_id | 0x400, brs)
        # Retain the original O30 arbitration tag for compatibility; every
        # HOP model uses this same physical address key.
        self._endpoint_id = (
            "o30",
            self._addressing.request_id,
            self._addressing.response_id,
        )
        self._route_keys = (response_route(self._addressing.response_id),)
        self._sensor_metadata: dict[int, TactileSensorMetadata] = {}
        self._identity_fingerprint: object | None = None

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
            raise ProtocolError("HOP capability side changed after backend creation")
        # Initialization always supplies the unverified declared profile;
        # same-generation get_device_info() supplies the verified profile.
        if not capabilities.identity.verified:
            self._sensor_metadata.clear()
            self._identity_fingerprint = None
        return identity_transaction(
            capabilities, self._addressing, on_identity=self._observe_identity
        )

    def _observe_identity(self, identity: IdentityResult) -> None:
        if identity.fingerprint != self._identity_fingerprint:
            self._sensor_metadata.clear()
        self._identity_fingerprint = identity.fingerprint

    def baseline_semantics(
        self,
        capabilities: DeviceCapabilities,
    ) -> tuple[TelemetrySemantic, ...]:
        """Return motion, fault, and safety configuration required before ready."""

        for semantic in _BASELINE:
            telemetry = capabilities.telemetry_channel(semantic)
            if telemetry is None or not telemetry.device_queried:
                raise ProtocolError(
                    f"HOP baseline semantic {semantic.value} is not queryable"
                )
        return _BASELINE

    def sensor_transaction(
        self, capabilities: DeviceCapabilities
    ) -> ProtocolTransaction:
        """Read descriptor-based force data with endpoint-scoped metadata."""
        return tactile_transaction(
            capabilities, self._addressing, metadata_cache=self._sensor_metadata
        )

    def decode_report(
        self,
        message: object,
        capabilities: DeviceCapabilities,
    ) -> tuple[SamplePayload, ...]:
        """Ignore unsolicited traffic; the profile uses explicit HOP queries."""

        del message, capabilities
        return ()


__all__ = ["HOPBackend"]
