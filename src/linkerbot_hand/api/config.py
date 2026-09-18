"""Validated lifecycle and SDK-owned polling configuration."""

from __future__ import annotations

import math
from dataclasses import dataclass, fields

from .enums import AngleFeedbackPolicy
from .errors import ValidationError


def _positive_number(name: str, value: object) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValidationError(f"{name} must be finite and positive")


@dataclass(frozen=True, slots=True)
class PollingPlan:
    """Requested feedback rates; zero disables a channel completely.

    ``angle_feedback`` controls whether the angle rate is fulfilled by active
    queries, verified angle-command feedback, or an adaptive combination. It
    never causes the SDK to repeat a motion target on the user's behalf.
    """

    angles_hz: float = 0.0
    speeds_hz: float = 0.0
    force_hz: float = 0.0
    temperature_hz: float = 0.0
    current_hz: float = 0.0
    faults_hz: float = 0.0
    torques_hz: float = 0.0
    voltage_hz: float = 0.0
    angle_feedback: AngleFeedbackPolicy = AngleFeedbackPolicy.ADAPTIVE

    def __post_init__(self) -> None:
        for item in fields(self):
            if not item.name.endswith("_hz"):
                continue
            value = getattr(self, item.name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValidationError(f"{item.name} must be finite and non-negative")
        if not isinstance(self.angle_feedback, AngleFeedbackPolicy):
            raise ValidationError("angle_feedback must be AngleFeedbackPolicy")

    def enabled_rates(self) -> tuple[tuple[str, float], ...]:
        """Return enabled channels in stable scheduler order."""

        return tuple(
            (item.name.removesuffix("_hz"), float(getattr(self, item.name)))
            for item in fields(self)
            if item.name.endswith("_hz") and getattr(self, item.name) > 0
        )


@dataclass(frozen=True, slots=True)
class DeviceConfig:
    """Runtime limits, optional O30 endpoint and recovery policy for one hand."""

    command_rate_hz: float = 250.0
    discrete_queue_capacity: int = 16
    mailbox_capacity: int = 256
    route_queue_capacity: int = 256
    identity_timeout_s: float = 0.2
    transaction_timeout_s: float = 0.1
    shutdown_timeout_s: float = 2.0
    auto_reconnect: bool = False
    reconnect_initial_delay_s: float = 0.1
    reconnect_max_delay_s: float = 2.0
    o30_request_id: int | None = None

    def __post_init__(self) -> None:
        if self.o30_request_id is not None and (
            type(self.o30_request_id) is not int
            or not 0 <= self.o30_request_id <= 0x3FE
        ):
            raise ValidationError("o30_request_id must be within 0x000..0x3FE or None")
        for name in (
            "discrete_queue_capacity",
            "mailbox_capacity",
            "route_queue_capacity",
        ):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValidationError(f"{name} must be a positive int")
        for name in (
            "command_rate_hz",
            "identity_timeout_s",
            "transaction_timeout_s",
            "shutdown_timeout_s",
            "reconnect_initial_delay_s",
            "reconnect_max_delay_s",
        ):
            _positive_number(name, getattr(self, name))
        if not isinstance(self.auto_reconnect, bool):
            raise ValidationError("auto_reconnect must be bool")
        if self.reconnect_initial_delay_s > self.reconnect_max_delay_s:
            raise ValidationError(
                "reconnect_initial_delay_s must not exceed reconnect_max_delay_s"
            )


__all__ = ["DeviceConfig", "PollingPlan"]
