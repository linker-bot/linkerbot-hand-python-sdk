"""Lifecycle and routing core for the single public HandDevice facade."""

from __future__ import annotations

import math
import threading
from collections.abc import Iterable
from importlib import import_module
from typing import NoReturn, TypeVar, cast

from ..capabilities.types import (
    CommandSemantic,
    DeviceCapabilities,
    TelemetrySemantic,
)
from ..conversion.values import percent_to_raw_values, validate_command_values
from ._initial_state import make_initial_health, make_initial_snapshot
from ._runtime_contract import DeviceRuntimeContract, RuntimeFactory
from .commands import CommandHandle
from .config import DeviceConfig, PollingPlan
from .device_info import DeviceInfo
from .errors import (
    ClosedError,
    LifecycleError,
    UnsupportedOperationError,
    ValidationError,
)
from .health import HealthStatus
from .readings import RawNumber
from .snapshot import HandSnapshot

_SampleT = TypeVar("_SampleT")

_POLL_CHANNELS = {
    "angles": TelemetrySemantic.ANGLES,
    "speeds": TelemetrySemantic.MEASURED_SPEEDS,
    "force": TelemetrySemantic.FORCE,
    "temperature": TelemetrySemantic.TEMPERATURES,
    "current": TelemetrySemantic.CURRENTS,
    "faults": TelemetrySemantic.FAULTS,
    "torques": TelemetrySemantic.TORQUES,
    "voltage": TelemetrySemantic.VOLTAGES,
}


def _default_runtime_factory(
    *,
    transport: object,
    capabilities: DeviceCapabilities,
    config: DeviceConfig,
    initial_snapshot: HandSnapshot,
    initial_health: HealthStatus,
    clock: object | None,
    protocol_backend: object | None,
) -> DeviceRuntimeContract:
    """Import the concrete runtime only when the user explicitly opens."""

    try:
        module = import_module("linkerbot_hand.runtime.device_runtime")
        factory = cast(RuntimeFactory, module.DeviceRuntime)
    except (ImportError, AttributeError) as error:
        raise LifecycleError(
            "the Hand SDK 1.0 device runtime is unavailable",
            operation="open",
            model=capabilities.model,
            cause=error,
        ) from error
    return factory(
        transport=transport,
        capabilities=capabilities,
        config=config,
        initial_snapshot=initial_snapshot,
        initial_health=initial_health,
        clock=clock,
        protocol_backend=protocol_backend,
    )


class _DeviceCore:
    """Private mechanics shared by the public, typed facade methods."""

    def __init__(
        self,
        *,
        model: str,
        transport: object,
        side: str | None = None,
        config: DeviceConfig | None = None,
        clock: object | None = None,
        protocol_backend: object | None = None,
        runtime_factory: RuntimeFactory | None = None,
    ) -> None:
        # Delayed to avoid the transitional capability package importing
        # ``api.enums`` while ``api`` itself is still being initialized.
        from ..capabilities.registry import get_declared_capabilities

        if transport is None:
            raise ValidationError("transport must not be None")
        if config is not None and not isinstance(config, DeviceConfig):
            raise ValidationError("config must be DeviceConfig or None")
        if runtime_factory is not None and not callable(runtime_factory):
            raise ValidationError("runtime_factory must be callable or None")

        capabilities = get_declared_capabilities(model, side=side)
        self._transport = transport
        self._config = DeviceConfig() if config is None else config
        self._clock = clock
        self._protocol_backend = protocol_backend
        self._runtime_factory = (
            _default_runtime_factory if runtime_factory is None else runtime_factory
        )
        self._declared_capabilities = capabilities
        self._initial_snapshot = make_initial_snapshot(capabilities)
        self._initial_health = make_initial_health(capabilities)
        self._runtime: DeviceRuntimeContract | None = None
        self._transition_lock = threading.Lock()

    def open(self) -> _DeviceCore:
        """Open and verify the configured device; repeated calls are no-ops."""

        with self._transition_lock:
            created = self._runtime is None
            runtime = self._runtime
            if runtime is None:
                runtime = self._runtime_factory(
                    transport=self._transport,
                    capabilities=self._declared_capabilities,
                    config=self._config,
                    initial_snapshot=self._initial_snapshot,
                    initial_health=self._initial_health,
                    clock=self._clock,
                    protocol_backend=self._protocol_backend,
                )
                if runtime is None:
                    raise LifecycleError(
                        "runtime factory returned no runtime",
                        operation="open",
                        model=self._declared_capabilities.model,
                    )
                self._runtime = runtime
            try:
                runtime.open()
            except BaseException as open_error:
                try:
                    runtime.close()
                except BaseException as close_error:
                    raise LifecycleError(
                        "device open failed and runtime cleanup did not complete",
                        operation="open",
                        model=self._declared_capabilities.model,
                        cause=open_error,
                    ) from close_error
                if created:
                    self._runtime = None
                raise
            return self

    def close(self) -> None:
        """Stop accepting work and close the runtime with a bounded shutdown."""

        with self._transition_lock:
            runtime = self._runtime
            if runtime is not None:
                runtime.close()

    def start_polling(self, plan: PollingPlan) -> None:
        """Start or atomically replace the SDK-owned polling plan."""

        if not isinstance(plan, PollingPlan):
            raise ValidationError("plan must be PollingPlan")
        self._validate_polling_plan(plan)
        self._require_runtime("start_polling").start_polling(plan)

    def stop_polling(self) -> None:
        """Stop periodic queries; repeated and closed calls are no-ops."""

        runtime = self._runtime
        if runtime is not None:
            runtime.stop_polling()

    def get_capabilities(self) -> DeviceCapabilities:
        """Return declared or verified immutable capabilities without I/O."""

        runtime = self._runtime
        if runtime is None:
            return self._declared_capabilities
        return runtime.get_capabilities()

    def get_snapshot(self) -> HandSnapshot:
        """Return the current immutable cache reference without device I/O."""

        runtime = self._runtime
        if runtime is None:
            return self._initial_snapshot
        return runtime.get_snapshot()

    def get_health(self) -> HealthStatus:
        """Return cached lifecycle and scheduler health without device I/O."""

        runtime = self._runtime
        if runtime is None:
            return self._initial_health
        return runtime.get_health()

    def recover(self, timeout: float) -> None:
        """Run explicit identity-verified recovery without replaying old targets."""

        timeout_s = _coerce_timeout(timeout)
        self._require_runtime("recover").recover(timeout_s)

    def _submit_raw(
        self,
        semantic: CommandSemantic,
        values: Iterable[RawNumber],
        *,
        operation: str,
    ) -> CommandHandle:
        capabilities = self.get_capabilities()
        command = capabilities.command(semantic)
        if command is None:
            self._raise_unsupported(operation)
        normalized = validate_command_values(values, command, label=semantic.value)
        return self._require_runtime(operation).submit(semantic, normalized)

    def _submit_percent(
        self,
        semantic: CommandSemantic,
        values: Iterable[RawNumber],
        *,
        operation: str,
    ) -> CommandHandle:
        capabilities = self.get_capabilities()
        command = capabilities.command(semantic)
        if command is None:
            self._raise_unsupported(operation)
        normalized = percent_to_raw_values(
            values,
            command,
            operation=operation,
            model=capabilities.model,
        )
        return self._require_runtime(operation).submit(semantic, normalized)

    def _read(
        self,
        semantic: TelemetrySemantic,
        timeout: float,
        expected_type: type[_SampleT],
        *,
        operation: str,
    ) -> _SampleT:
        timeout_s = _coerce_timeout(timeout)
        if self.get_capabilities().telemetry_channel(semantic) is None:
            self._raise_unsupported(operation)
        result = self._require_runtime(operation).read(semantic, timeout_s)
        if not isinstance(result, expected_type):
            raise LifecycleError(
                f"runtime returned {type(result).__name__} from {operation}",
                operation=operation,
                model=self._declared_capabilities.model,
            )
        return result

    def _read_device_info(self, timeout: float) -> DeviceInfo:
        operation = "get_device_info"
        timeout_s = _coerce_timeout(timeout)
        if not self.get_capabilities().operations.read_device_info:
            self._raise_unsupported(operation)
        return self._require_runtime(operation).read_device_info(timeout_s)

    def _operate(self, operation: str) -> CommandHandle:
        capabilities = self.get_capabilities()
        if not getattr(capabilities.operations, operation):
            self._raise_unsupported(operation)
        return self._require_runtime(operation).operate(operation)

    def _validate_polling_plan(self, plan: PollingPlan) -> None:
        capabilities = self.get_capabilities()
        for channel, _rate in plan.enabled_rates():
            semantic = _POLL_CHANNELS[channel]
            telemetry = capabilities.telemetry_channel(semantic)
            if telemetry is None or not telemetry.device_queried:
                raise UnsupportedOperationError(
                    f"{capabilities.model} cannot poll {channel}",
                    operation="start_polling",
                    model=capabilities.model,
                )

    def _require_runtime(self, operation: str) -> DeviceRuntimeContract:
        runtime = self._runtime
        if runtime is None:
            raise ClosedError(
                f"{operation} requires an open device",
                operation=operation,
                model=self._declared_capabilities.model,
            )
        return runtime

    def _raise_unsupported(self, operation: str) -> NoReturn:
        raise UnsupportedOperationError(
            f"{self._declared_capabilities.model} does not support {operation}",
            operation=operation,
            model=self._declared_capabilities.model,
        )


def _coerce_timeout(timeout: float) -> float:
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise ValidationError("timeout must be finite and positive")
    return float(timeout)


__all__: list[str] = []
