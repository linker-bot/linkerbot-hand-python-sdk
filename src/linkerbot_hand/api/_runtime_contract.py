"""Narrow dependency boundary between the public facade and device runtime."""

from __future__ import annotations

from typing import Protocol

from ..capabilities.types import (
    CommandSemantic,
    DeviceCapabilities,
    TelemetrySemantic,
)
from .commands import CommandHandle
from .config import DeviceConfig, PollingPlan
from .device_info import DeviceInfo
from .health import HealthStatus
from .snapshot import HandSnapshot


class DeviceRuntimeContract(Protocol):
    """Operations owned by the single actor-style device runtime."""

    def open(self) -> None: ...

    def close(self) -> None: ...

    def start_polling(self, plan: PollingPlan) -> None: ...

    def stop_polling(self) -> None: ...

    def submit(
        self,
        semantic: CommandSemantic,
        values: tuple[int | float, ...],
    ) -> CommandHandle: ...

    def read(self, semantic: TelemetrySemantic, timeout: float) -> object: ...

    def read_device_info(self, timeout: float) -> DeviceInfo: ...

    def operate(self, operation: str) -> CommandHandle: ...

    def recover(self, timeout: float) -> None: ...

    def get_capabilities(self) -> DeviceCapabilities: ...

    def get_snapshot(self) -> HandSnapshot: ...

    def get_health(self) -> HealthStatus: ...


class RuntimeFactory(Protocol):
    """Injectable constructor used only by :meth:`HandDevice.open`."""

    def __call__(
        self,
        *,
        transport: object,
        capabilities: DeviceCapabilities,
        config: DeviceConfig,
        initial_snapshot: HandSnapshot,
        initial_health: HealthStatus,
        clock: object | None,
        protocol_backend: object | None,
    ) -> DeviceRuntimeContract: ...


__all__ = ["DeviceRuntimeContract", "RuntimeFactory"]
