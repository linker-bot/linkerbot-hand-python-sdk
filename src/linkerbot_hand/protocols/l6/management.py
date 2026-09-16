"""L6-specific limit-compensation write over the shared config transaction."""

from __future__ import annotations

from ...api.readings import RawNumber
from ...capabilities.types import (
    CommandSemantic,
    DeviceCapabilities,
    TelemetrySemantic,
)
from ..base import ProtocolTransaction
from ..common.classic_management import configuration_transaction

_LIMIT_COMPENSATION_OPCODE = 0x38


def limit_compensation_transaction(
    values: tuple[RawNumber, ...],
    capabilities: DeviceCapabilities,
) -> ProtocolTransaction:
    """Write six raw compensation bytes and require the documented exact echo."""

    return configuration_transaction(
        CommandSemantic.LIMIT_COMPENSATIONS,
        TelemetrySemantic.LIMIT_COMPENSATIONS,
        _LIMIT_COMPENSATION_OPCODE,
        values,
        capabilities,
    )


__all__ = ["limit_compensation_transaction"]
