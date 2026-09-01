"""L30 protocol backend for the unified device runtime."""

from __future__ import annotations

from linkerbot_transport import CANRouteKey

from ...api.enums import HandSide
from ...api.errors import ProtocolError
from ...api.readings import RawNumber
from ...capabilities.types import (
    CommandSemantic,
    DeviceCapabilities,
    TelemetrySemantic,
)
from ..base import ProtocolTransaction, SamplePayload
from .codec import (
    PARENT_CONFIG,
    PARENT_CONTROL,
    PARENT_QUERY,
    PARENT_TACTILE,
    SUB_CALIBRATE,
    SUB_CONFIG_UNLOCK,
    SUB_DEVICE_INFO,
    SUB_DISABLE,
    SUB_ENABLE,
    SUB_PRODUCT_CODE,
)
from .control import command_transaction as build_command_transaction
from .framing import (
    DEFAULT_HOST_ID,
    DEFAULT_NODE_ID,
    response_route,
)
from .identity import l30_identity_transaction
from .operations import operation_transaction
from .telemetry import read_transaction as build_read_transaction

_BASELINE = (TelemetrySemantic.ANGLES, TelemetrySemantic.FAULTS)
_READ_ROUTES = (
    (PARENT_CONFIG, SUB_DEVICE_INFO),
    (PARENT_CONFIG, SUB_PRODUCT_CODE),
    *((PARENT_QUERY, subcommand) for subcommand in range(1, 6)),
    *((PARENT_TACTILE, subcommand) for subcommand in range(1, 6)),
)
_WRITE_ROUTES = (
    (PARENT_CONTROL, SUB_ENABLE),
    (PARENT_CONTROL, SUB_DISABLE),
    (PARENT_CONFIG, SUB_CONFIG_UNLOCK),
    (PARENT_CONFIG, SUB_CALIBRATE),
)


class L30ProtocolBackend:
    """Stateless L30 profile for one configured Node ID and host ID."""

    def __init__(
        self,
        side: HandSide,
        *,
        node_id: int = DEFAULT_NODE_ID,
        host_id: int = DEFAULT_HOST_ID,
    ) -> None:
        if side is not HandSide.NOT_APPLICABLE:
            raise ProtocolError("L30 protocol requires NOT_APPLICABLE side")
        if type(node_id) is not int or not 1 <= node_id <= 31:
            raise ProtocolError("L30 node_id must be within 1..31")
        if type(host_id) is not int or not 0 <= host_id <= 31:
            raise ProtocolError("L30 host_id must be within 0..31")
        if node_id == host_id:
            raise ProtocolError("L30 node_id and host_id must be different")
        self._node_id = node_id
        self._host_id = host_id
        self._endpoint_id = ("l30", self._node_id, self._host_id)
        self._route_keys = tuple(
            response_route(
                parent=parent,
                subcommand=subcommand,
                write=write,
                node_id=self._node_id,
                host_id=self._host_id,
            )
            for write, routes in ((False, _READ_ROUTES), (True, _WRITE_ROUTES))
            for parent, subcommand in routes
        )

    @property
    def endpoint_id(self) -> tuple[str, int, int]:
        """Return the physical L30 endpoint used for TX arbitration."""

        return self._endpoint_id

    @property
    def route_keys(self) -> tuple[CANRouteKey, ...]:
        """Return every exact response route consumed by this profile."""

        return self._route_keys

    def identity_transaction(
        self,
        capabilities: DeviceCapabilities,
    ) -> ProtocolTransaction:
        """Build the response-gated DeviceInfo and product-code read."""

        return l30_identity_transaction(
            capabilities,
            node_id=self._node_id,
            host_id=self._host_id,
        )

    def baseline_semantics(
        self,
        capabilities: DeviceCapabilities,
    ) -> tuple[TelemetrySemantic, ...]:
        """Return the non-motion state required before command admission."""

        for semantic in _BASELINE:
            if capabilities.telemetry_channel(semantic) is None:
                raise ProtocolError(
                    f"L30 baseline semantic {semantic.value} is not declared"
                )
        return _BASELINE

    def command_transaction(
        self,
        semantic: CommandSemantic,
        values: tuple[RawNumber, ...],
        capabilities: DeviceCapabilities,
    ) -> ProtocolTransaction:
        """Encode one dispatch-only raw command and optional command cache."""

        return build_command_transaction(
            semantic,
            values,
            capabilities,
            node_id=self._node_id,
            host_id=self._host_id,
        )

    def read_transaction(
        self,
        semantic: TelemetrySemantic,
        capabilities: DeviceCapabilities,
    ) -> ProtocolTransaction:
        """Build one fresh state query or complete five-finger tactile read."""

        return build_read_transaction(
            semantic,
            capabilities,
            node_id=self._node_id,
            host_id=self._host_id,
        )

    def operation_transaction(
        self,
        operation: str,
        capabilities: DeviceCapabilities,
    ) -> ProtocolTransaction:
        """Build enable, disable, or ACK-gated three-stage calibration."""

        return operation_transaction(
            operation,
            capabilities,
            node_id=self._node_id,
            host_id=self._host_id,
        )

    def decode_report(
        self,
        message: object,
        capabilities: DeviceCapabilities,
    ) -> tuple[SamplePayload, ...]:
        """Ignore unsolicited traffic; SDK polling uses explicit state queries."""

        del message, capabilities
        return ()


__all__ = ["L30ProtocolBackend"]
