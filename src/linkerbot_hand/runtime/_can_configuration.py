"""Private actor request keys for optional device-side CAN management."""

from dataclasses import dataclass
from typing import Protocol

from ..protocols.base import ProtocolTransaction

CAN_CONFIGURATION_KEY = ("can_configuration",)


@dataclass(frozen=True, slots=True)
class CANConfigurationWrite:
    sub_index: int
    payload: bytes
    deadline_s: float


class CANConfigurationBackend(Protocol):
    def can_configuration_transaction(self) -> ProtocolTransaction: ...
    def can_configuration_write(
        self, sub_index: int, payload: bytes
    ) -> ProtocolTransaction: ...
