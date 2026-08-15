"""Polling and lifecycle configuration contracts."""

from dataclasses import dataclass

from ..errors import ValidationError
from ._validation import is_finite_number
from .enums import SensorType


@dataclass(frozen=True, slots=True)
class PollingPlan:
    """Requested SDK-owned polling rates in hertz.

    A rate of zero omits the corresponding sensor query. The SDK rejects
    enabled channels that the selected device does not support when polling is
    started. Use :meth:`HandSession.stop_polling` to stop the polling lifecycle.

    Attributes:
        angles_hz: Joint-angle polling frequency.
        force_hz: Tactile or force-sensor polling frequency.
        temperature_hz: Temperature polling frequency.
        current_hz: Joint-current polling frequency.
        faults_hz: Device-fault polling frequency.
        speeds_hz: Measured joint-speed polling frequency.
        torques_hz: Measured joint-torque polling frequency.
        voltage_hz: Supply-voltage polling frequency.
        acceleration_hz: Joint-acceleration polling frequency.
        motion_time_hz: Configured motion-time polling frequency.

    Raises:
        ValidationError: If any rate is non-numeric, negative, or non-finite.
    """

    angles_hz: float = 0.0
    force_hz: float = 0.0
    temperature_hz: float = 0.0
    current_hz: float = 0.0
    faults_hz: float = 0.0
    speeds_hz: float = 0.0
    torques_hz: float = 0.0
    voltage_hz: float = 0.0
    acceleration_hz: float = 0.0
    motion_time_hz: float = 0.0

    def __post_init__(self) -> None:
        """Validate all configured rates."""
        for name, rate in self._named_rates():
            if isinstance(rate, bool) or not isinstance(rate, (int, float)):
                raise ValidationError(f"{name} must be numeric")
            if not is_finite_number(rate) or rate < 0:
                raise ValidationError(f"{name} must be finite and non-negative")

    def rates(self) -> dict[SensorType, float]:
        """Return every sensor and its configured rate.

        Returns:
            A new mapping that includes disabled sensors with a zero rate.
        """
        return {
            SensorType.ANGLES: float(self.angles_hz),
            SensorType.FORCE: float(self.force_hz),
            SensorType.TEMPERATURE: float(self.temperature_hz),
            SensorType.CURRENT: float(self.current_hz),
            SensorType.FAULTS: float(self.faults_hz),
            SensorType.SPEEDS: float(self.speeds_hz),
            SensorType.TORQUES: float(self.torques_hz),
            SensorType.VOLTAGE: float(self.voltage_hz),
            SensorType.ACCELERATION: float(self.acceleration_hz),
            SensorType.MOTION_TIME: float(self.motion_time_hz),
        }

    def enabled_rates(self) -> dict[SensorType, float]:
        """Return only sensors whose configured rate is greater than zero."""
        return {sensor: rate for sensor, rate in self.rates().items() if rate > 0}

    def _named_rates(self) -> tuple[tuple[str, float], ...]:
        return (
            ("angles_hz", self.angles_hz),
            ("force_hz", self.force_hz),
            ("temperature_hz", self.temperature_hz),
            ("current_hz", self.current_hz),
            ("faults_hz", self.faults_hz),
            ("speeds_hz", self.speeds_hz),
            ("torques_hz", self.torques_hz),
            ("voltage_hz", self.voltage_hz),
            ("acceleration_hz", self.acceleration_hz),
            ("motion_time_hz", self.motion_time_hz),
        )


@dataclass(frozen=True, slots=True)
class SessionConfig:
    """Connection lifecycle and scheduler limits for a hand session.

    Attributes:
        command_rate_hz: Maximum rate at which continuous commands are sent.
        discrete_queue_size: Capacity of the bounded discrete-command FIFO.
        join_timeout_s: Maximum worker-join time during lifecycle operations.
        auto_reconnect: Whether transport failures schedule reconnection.
        reconnect_initial_delay_s: Delay before the first automatic retry.
        reconnect_max_delay_s: Upper bound for exponential retry delay.
    """

    command_rate_hz: float = 100.0
    discrete_queue_size: int = 16
    join_timeout_s: float = 2.0
    auto_reconnect: bool = False
    reconnect_initial_delay_s: float = 0.1
    reconnect_max_delay_s: float = 2.0

    def __post_init__(self) -> None:
        """Validate lifecycle durations and queue capacity.

        Raises:
            ValidationError: If a duration is invalid, the maximum reconnect
                delay is smaller than the initial delay, the command rate is
                invalid, or the queue capacity is not a positive integer.
        """
        if not isinstance(self.auto_reconnect, bool):
            raise ValidationError("auto_reconnect must be bool")
        for name in (
            "command_rate_hz",
            "join_timeout_s",
            "reconnect_initial_delay_s",
            "reconnect_max_delay_s",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValidationError(f"{name} must be numeric")
            if not is_finite_number(value) or value <= 0:
                raise ValidationError(f"{name} must be finite and positive")
        if isinstance(self.discrete_queue_size, bool) or not isinstance(
            self.discrete_queue_size, int
        ):
            raise ValidationError("discrete_queue_size must be int")
        if self.discrete_queue_size <= 0:
            raise ValidationError("discrete_queue_size must be positive")
        if self.reconnect_initial_delay_s > self.reconnect_max_delay_s:
            raise ValidationError(
                "reconnect_initial_delay_s must not exceed reconnect_max_delay_s"
            )


__all__ = ["PollingPlan", "SessionConfig"]
