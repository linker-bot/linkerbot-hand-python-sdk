"""Generic adapter from classic model classes to the unified backend contract."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any

from ...backend import BackendReading, DispatchResult
from ...commands.model import HandCommand
from ...contracts import CommandKind, DeviceIdentity, HandSide, SensorType
from ...errors import DeviceNotReadyError, TransportError, ValidationError
from ...models import get_model_capabilities
from .adapters import LegacyModelAdapter, get_legacy_model_adapter
from .dispatcher import DispatchClaimRejected, ScopedImmediateDispatcher
from .normalization import (
    as_tuple,
    extract_payload,
    normalize_sensor_value,
    normalize_tactile,
    poll_manager_name,
    protocol_angle_values,
    protocol_vector_values,
    raise_mapped,
)


class LegacyHandBackend:
    """Run one old model class behind the new serialized session contract."""

    def __init__(
        self,
        model: str,
        *,
        side: HandSide,
        options: dict[str, Any],
    ) -> None:
        """Configure a legacy backend without constructing the old driver.

        Args:
            model: Canonical supported model name.
            side: Requested hand side.
            options: Legacy transport and constructor options.
        """
        self._model = model
        self._side = side
        self._options = dict(options)
        self._capabilities = get_model_capabilities(model, side=side)
        self._adapter: LegacyModelAdapter = get_legacy_model_adapter(
            self._capabilities.model
        )
        self._hand: Any | None = None

    def open(self) -> DeviceIdentity:
        """Construct the legacy driver and verify connected device identity.

        Returns:
            Identity read from the connected device.

        Raises:
            HandSDKError: If import, construction, connection, or identity
                verification fails.
        """
        if self._hand is not None:
            identity = self._adapter.read_identity(self._hand)
            self._capabilities = self._capabilities.with_identity(identity)
            return identity
        hand_class = getattr(
            importlib.import_module(self._adapter.module_name),
            self._adapter.class_name,
        )
        options = self._adapter.prepare_options(self._side, self._options)
        dispatcher = options.get("dispatcher")
        if dispatcher is not None and not isinstance(
            dispatcher, ScopedImmediateDispatcher
        ):
            options["dispatcher"] = ScopedImmediateDispatcher(dispatcher)
        try:
            self._hand = hand_class(**options)
            identity = self._adapter.read_identity(self._hand)
            self._capabilities = self._capabilities.with_identity(identity)
            return identity
        except BaseException as exc:
            try:
                self.close()
            except BaseException:
                pass
            raise_mapped(exc)

    def close(self) -> None:
        """Close and discard the legacy hand instance idempotently.

        Raises:
            HandSDKError: If the legacy driver's close operation fails.
        """
        hand = self._hand
        if hand is None:
            return
        try:
            hand.close()
        except BaseException as exc:
            raise_mapped(exc)
        if self._hand is hand:
            self._hand = None

    def dispatch(
        self,
        command: HandCommand,
        *,
        claim_dispatch: Callable[[], bool],
        mark_dispatched: Callable[[], None],
    ) -> DispatchResult:
        """Dispatch one unified command through a legacy manager.

        The scoped dispatcher claims latest-wins commands immediately before
        the first transport frame and records dispatch only after ``send_now``
        succeeds.

        Args:
            command: Validated unified command.
            claim_dispatch: Atomic pre-send scheduler claim.
            mark_dispatched: Callback invoked after transport send success.

        Returns:
            Explicit ACK status when supported by the model protocol.

        Raises:
            HandSDKError: If the driver operation or transport send fails.
        """
        hand = self._require_hand()
        kind = command.kind
        values = list(command.values)

        def require_claim() -> bool:
            if not claim_dispatch():
                raise DispatchClaimRejected
            return True

        try:
            with self._immediate_dispatch(require_claim, mark_dispatched):
                if kind is CommandKind.ANGLES:
                    hand.angle.set_angles(values)
                elif kind is CommandKind.SPEEDS:
                    hand.speed.set_speeds(values)
                elif kind is CommandKind.TORQUES:
                    hand.torque.set_torques(values)
                elif kind is CommandKind.CLEAR_FAULTS:
                    clear = getattr(hand.fault, "clear_faults", None)
                    if clear is None:
                        clear = hand.fault.clear
                    clear()
                elif kind is CommandKind.ENABLE:
                    hand.control.enable()
                elif kind is CommandKind.DISABLE:
                    hand.control.disable()
                elif kind is CommandKind.CALIBRATE:
                    hand.calibration.calibrate_zero(confirm=True)
                else:  # pragma: no cover - core validation owns this set
                    raise ValidationError(f"unknown command {kind!r}")
        except DispatchClaimRejected:
            return DispatchResult()
        except BaseException as exc:
            raise_mapped(exc)
        return DispatchResult(
            acknowledged=(
                True if self._capabilities.features.supports_ack(kind) else None
            )
        )

    def poll(self, sensor: SensorType) -> BackendReading:
        """Run one blocking legacy sensor query and normalize its response.

        Args:
            sensor: Supported sensor channel to query.

        Returns:
            Unified reading containing decoded protocol-native values.

        Raises:
            HandSDKError: If polling, payload extraction, or normalization fails.
        """
        hand = self._require_hand()
        try:
            manager = getattr(hand, poll_manager_name(sensor))
            data = manager.get_blocking()
            if sensor is SensorType.FORCE:
                return BackendReading(sensor=sensor, values=normalize_tactile(data))
            payload = extract_payload(data, sensor)
            if sensor is SensorType.ANGLES:
                values = protocol_angle_values(
                    payload,
                    model=self._model,
                )
                return BackendReading(sensor=sensor, values=values)
            if sensor in (
                SensorType.SPEEDS,
                SensorType.TORQUES,
                SensorType.ACCELERATION,
                SensorType.CURRENT,
            ):
                values = protocol_vector_values(
                    payload, sensor=sensor, model=self._model
                )
                return BackendReading(sensor=sensor, values=values)
            values = tuple(normalize_sensor_value(value) for value in as_tuple(payload))
            return BackendReading(sensor=sensor, values=values)
        except BaseException as exc:
            raise_mapped(exc)

    def _immediate_dispatch(
        self,
        claim_dispatch: Callable[[], bool],
        mark_dispatched: Callable[[], None],
    ) -> Any:
        dispatcher = getattr(self._require_hand(), "_dispatcher", None)
        scope = getattr(dispatcher, "immediate_dispatch", None)
        if not callable(scope):
            raise TransportError(
                "legacy dispatcher does not support synchronous command dispatch"
            )
        return scope(claim_dispatch, mark_dispatched)

    def _require_hand(self) -> Any:
        if self._hand is None:
            raise DeviceNotReadyError("legacy hand backend is not open")
        return self._hand


__all__ = ["LegacyHandBackend"]
