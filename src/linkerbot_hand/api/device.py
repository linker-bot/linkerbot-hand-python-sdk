"""The single public facade used for every Linkerbot hand model."""

from __future__ import annotations

from collections.abc import Iterable

from ..capabilities.types import CommandSemantic, TelemetrySemantic
from ._device_core import _DeviceCore
from .commands import CommandHandle
from .device_info import DeviceInfo
from .errors import ValidationError
from .readings import (
    AngleSample,
    ConfiguredAccelerationSample,
    ConfiguredSpeedSample,
    CurrentLimitSample,
    CurrentSample,
    EffortLimitSample,
    FaultSample,
    ForceSample,
    MeasuredSpeedSample,
    MotionTimeSample,
    RawNumber,
    TemperatureSample,
    TorqueSample,
    VoltageSample,
)
from .snapshot import HandSnapshot
from .stall import (
    LimitCompensationSample,
    StallDetectionTimeSample,
    StallHoldCurrentSample,
    StallThresholdSample,
)


class HandDevice(_DeviceCore):
    """Unified non-blocking control and typed state access for one hand.

    Construction only validates configuration and creates immutable in-memory
    state. It does not open a transport, start a thread, query a device, or
    submit motion. Call :meth:`open` explicitly before any device operation.
    """

    def open(self) -> HandDevice:
        """Open and verify the configured device; repeated calls are no-ops."""

        super().open()
        return self

    def wait_for_snapshot(
        self,
        previous: HandSnapshot,
        timeout: float,
        *,
        wake_on_command_feedback: bool = True,
    ) -> HandSnapshot:
        """Wait for cached state to change, returning the cache again on timeout.

        This method never sends a device request. It is intended for a dedicated
        consumer thread that wants change-driven state delivery. Command and ROS
        callbacks should continue to use the non-blocking :meth:`get_snapshot`.
        Set ``wake_on_command_feedback=False`` when command feedback is retained
        in the cache but must not drive this consumer's publication loop.
        """

        if not isinstance(wake_on_command_feedback, bool):
            raise ValidationError("wake_on_command_feedback must be bool")
        return self._wait_for_snapshot(
            previous,
            timeout,
            wake_on_command_feedback=wake_on_command_feedback,
        )

    def set_angles(self, values: Iterable[RawNumber]) -> CommandHandle:
        """Submit exact protocol angle values without direction conversion."""

        return self._submit_raw(CommandSemantic.ANGLES, values, operation="set_angles")

    def set_percent_angles(self, values: Iterable[RawNumber]) -> CommandHandle:
        """Submit strict ``0..100`` angles using fixed capability mappings."""

        return self._submit_percent(
            CommandSemantic.ANGLES,
            values,
            operation="set_percent_angles",
        )

    def get_angles(self, timeout: float) -> AngleSample:
        """Actively read one fresh raw/percent/physical angle sample."""

        return self._read(
            TelemetrySemantic.ANGLES,
            timeout,
            AngleSample,
            operation="get_angles",
        )

    def set_speeds(self, values: Iterable[RawNumber]) -> CommandHandle:
        """Submit exact configured-speed protocol values."""

        return self._submit_raw(CommandSemantic.SPEEDS, values, operation="set_speeds")

    def set_percent_speeds(self, values: Iterable[RawNumber]) -> CommandHandle:
        """Submit ``0..100`` speed values only when a mapping is declared."""

        return self._submit_percent(
            CommandSemantic.SPEEDS,
            values,
            operation="set_percent_speeds",
        )

    def get_speeds(self, timeout: float) -> MeasuredSpeedSample:
        """Actively read device-measured speed protocol values."""

        return self._read(
            TelemetrySemantic.MEASURED_SPEEDS,
            timeout,
            MeasuredSpeedSample,
            operation="get_speeds",
        )

    def get_configured_speeds(self, timeout: float) -> ConfiguredSpeedSample:
        """Read device configuration or the explicit command-cache sample."""

        return self._read(
            TelemetrySemantic.CONFIGURED_SPEEDS,
            timeout,
            ConfiguredSpeedSample,
            operation="get_configured_speeds",
        )

    def set_accelerations(self, values: Iterable[RawNumber]) -> CommandHandle:
        """Submit exact configured-acceleration protocol values."""

        return self._submit_raw(
            CommandSemantic.ACCELERATIONS,
            values,
            operation="set_accelerations",
        )

    def get_configured_accelerations(
        self,
        timeout: float,
    ) -> ConfiguredAccelerationSample:
        """Actively read configured-acceleration protocol values."""

        return self._read(
            TelemetrySemantic.CONFIGURED_ACCELERATIONS,
            timeout,
            ConfiguredAccelerationSample,
            operation="get_configured_accelerations",
        )

    def set_current_limits(self, values: Iterable[RawNumber]) -> CommandHandle:
        """Submit exact current-limit values without physical conversion."""

        return self._submit_raw(
            CommandSemantic.CURRENT_LIMITS,
            values,
            operation="set_current_limits",
        )

    def get_current_limits(self, timeout: float) -> CurrentLimitSample:
        """Actively read current-limit protocol values."""

        return self._read(
            TelemetrySemantic.CURRENT_LIMITS,
            timeout,
            CurrentLimitSample,
            operation="get_current_limits",
        )

    def set_effort_limits(self, values: Iterable[RawNumber]) -> CommandHandle:
        """Submit physically unqualified effort-limit protocol values."""

        return self._submit_raw(
            CommandSemantic.EFFORT_LIMITS,
            values,
            operation="set_effort_limits",
        )

    def get_effort_limits(self, timeout: float) -> EffortLimitSample:
        """Actively read physically unqualified effort-limit values."""

        return self._read(
            TelemetrySemantic.EFFORT_LIMITS,
            timeout,
            EffortLimitSample,
            operation="get_effort_limits",
        )

    def set_torques(self, values: Iterable[RawNumber]) -> CommandHandle:
        """Submit protocol values only when true torque is declared."""

        return self._submit_raw(
            CommandSemantic.TORQUES,
            values,
            operation="set_torques",
        )

    def get_torques(self, timeout: float) -> TorqueSample:
        """Actively read fields declared to represent true torque."""

        return self._read(
            TelemetrySemantic.TORQUES,
            timeout,
            TorqueSample,
            operation="get_torques",
        )

    def get_currents(self, timeout: float) -> CurrentSample:
        """Actively read current protocol values without unit conversion."""

        return self._read(
            TelemetrySemantic.CURRENTS,
            timeout,
            CurrentSample,
            operation="get_currents",
        )

    def get_temperatures(self, timeout: float) -> TemperatureSample:
        """Actively read temperature protocol values without conversion."""

        return self._read(
            TelemetrySemantic.TEMPERATURES,
            timeout,
            TemperatureSample,
            operation="get_temperatures",
        )

    def get_force(self, timeout: float) -> ForceSample:
        """Actively read one complete force or tactile transaction."""

        return self._read(
            TelemetrySemantic.FORCE,
            timeout,
            ForceSample,
            operation="get_force",
        )

    def get_faults(self, timeout: float) -> FaultSample:
        """Actively read device fault codes or bitmasks."""

        return self._read(
            TelemetrySemantic.FAULTS,
            timeout,
            FaultSample,
            operation="get_faults",
        )

    def get_voltages(self, timeout: float) -> VoltageSample:
        """Actively read voltage protocol values without conversion."""

        return self._read(
            TelemetrySemantic.VOLTAGES,
            timeout,
            VoltageSample,
            operation="get_voltages",
        )

    def set_motion_times(self, values: Iterable[RawNumber]) -> CommandHandle:
        """Submit exact configured motion-time protocol values."""

        return self._submit_raw(
            CommandSemantic.MOTION_TIMES,
            values,
            operation="set_motion_times",
        )

    def get_motion_times(self, timeout: float) -> MotionTimeSample:
        """Actively read configured motion-time protocol values."""

        return self._read(
            TelemetrySemantic.MOTION_TIMES,
            timeout,
            MotionTimeSample,
            operation="get_motion_times",
        )

    def get_device_info(self, timeout: float) -> DeviceInfo:
        """Actively refresh, verify, cache, and return device identity."""

        return self._read_device_info(timeout)

    def set_stall_detection_times(
        self,
        values: Iterable[RawNumber],
    ) -> CommandHandle:
        """Submit raw stall-detection-time configuration values."""

        return self._submit_raw(
            CommandSemantic.STALL_DETECTION_TIMES,
            values,
            operation="set_stall_detection_times",
        )

    def get_stall_detection_times(
        self,
        timeout: float,
    ) -> StallDetectionTimeSample:
        """Actively read raw stall-detection-time configuration."""

        return self._read(
            TelemetrySemantic.STALL_DETECTION_TIMES,
            timeout,
            StallDetectionTimeSample,
            operation="get_stall_detection_times",
        )

    def set_stall_thresholds(
        self,
        values: Iterable[RawNumber],
    ) -> CommandHandle:
        """Submit raw stall-threshold configuration values."""

        return self._submit_raw(
            CommandSemantic.STALL_THRESHOLDS,
            values,
            operation="set_stall_thresholds",
        )

    def get_stall_thresholds(self, timeout: float) -> StallThresholdSample:
        """Actively read raw stall-threshold configuration."""

        return self._read(
            TelemetrySemantic.STALL_THRESHOLDS,
            timeout,
            StallThresholdSample,
            operation="get_stall_thresholds",
        )

    def set_stall_hold_currents(
        self,
        values: Iterable[RawNumber],
    ) -> CommandHandle:
        """Submit raw current-maintained-after-stall values."""

        return self._submit_raw(
            CommandSemantic.STALL_HOLD_CURRENTS,
            values,
            operation="set_stall_hold_currents",
        )

    def get_stall_hold_currents(self, timeout: float) -> StallHoldCurrentSample:
        """Actively read raw current-maintained-after-stall values."""

        return self._read(
            TelemetrySemantic.STALL_HOLD_CURRENTS,
            timeout,
            StallHoldCurrentSample,
            operation="get_stall_hold_currents",
        )

    def set_limit_compensations(
        self,
        values: Iterable[RawNumber],
    ) -> CommandHandle:
        """Submit raw per-joint limit compensation with response validation."""

        return self._submit_raw(
            CommandSemantic.LIMIT_COMPENSATIONS,
            values,
            operation="set_limit_compensations",
        )

    def get_limit_compensations(self, timeout: float) -> LimitCompensationSample:
        """Actively read raw per-joint limit compensation values."""

        return self._read(
            TelemetrySemantic.LIMIT_COMPENSATIONS,
            timeout,
            LimitCompensationSample,
            operation="get_limit_compensations",
        )

    def clear_faults(self) -> CommandHandle:
        """Submit one bounded discrete clear-fault operation."""

        return self._operate("clear_faults")

    def enable(self) -> CommandHandle:
        """Submit one bounded discrete device-enable operation."""

        return self._operate("enable")

    def disable(self) -> CommandHandle:
        """Submit one bounded discrete device-disable operation."""

        return self._operate("disable")

    def calibrate(self) -> CommandHandle:
        """Submit one bounded discrete device-calibration operation."""

        return self._operate("calibrate")

    def save_parameters(self) -> CommandHandle:
        """Persist supported device configuration in non-volatile storage."""

        return self._operate("save_parameters")


__all__ = ["HandDevice"]
