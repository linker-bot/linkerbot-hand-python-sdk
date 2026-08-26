"""Torque target control for L30."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from linkerbot.hand._values import protocol_vector_to_scaled, scaled_vector_to_protocol
from linkerbot_transport.relay import DataRelay

from . import protocol
from .client import L30Client
from .joints import L30_JOINT_COUNT, validate_u16_values

L30_TORQUE_MIN = 60
L30_TORQUE_MAX = 800
L30_CURRENT_LIMIT_MA_PER_PROTOCOL_UNIT = 6.5


def protocol_current_limits_to_milliamps(
    values: list[int] | tuple[int, ...],
) -> tuple[float, ...]:
    """Convert 17 protocol current-limit values to milliamps explicitly."""
    return protocol_vector_to_scaled(
        values,
        count=L30_JOINT_COUNT,
        name="protocol_current_limits",
        minimum=L30_TORQUE_MIN,
        maximum=L30_TORQUE_MAX,
        scale=L30_CURRENT_LIMIT_MA_PER_PROTOCOL_UNIT,
    )


def milliamps_to_protocol_current_limits(
    values: list[float | int] | tuple[float | int, ...],
) -> tuple[int, ...]:
    """Quantize 17 milliamp current limits to protocol values explicitly."""
    return scaled_vector_to_protocol(
        values,
        count=L30_JOINT_COUNT,
        name="milliamps",
        minimum=L30_TORQUE_MIN,
        maximum=L30_TORQUE_MAX,
        scale=L30_CURRENT_LIMIT_MA_PER_PROTOCOL_UNIT,
    )


@dataclass(frozen=True, slots=True)
class TorqueData:
    """Immutable L30 target torque data.

    Attributes:
        torques: 17 protocol current-limit targets in J1..J17 order.
        timestamp: Unix timestamp when the target torque command was sent.
    """

    torques: tuple[int, ...]
    timestamp: float


class TorqueManager:
    """Manager for L30 target torque commands.

    The cached snapshot stores the last successfully encoded protocol
    current-limit target; it is not measured torque feedback.
    """

    def __init__(self, client: L30Client) -> None:
        """Initialize the torque manager.

        Args:
            client: L30 protocol client used to send torque target commands.
        """
        self._client = client
        self._relay = DataRelay[TorqueData]()

    def set_torques(self, torques: list[int] | tuple[int, ...]) -> None:
        """Send per-joint protocol current-limit targets.

        Args:
            torques: 17 protocol integers in J1..J17 order. Each value must be
                between 60 and 800; one unit represents 6.5 mA.

        Raises:
            ValidationError: If the count, type, or torque range is invalid.
        """
        values = validate_u16_values(
            torques, name="torques", minimum=L30_TORQUE_MIN, maximum=L30_TORQUE_MAX
        )
        self._client.send_no_ack(
            parent=protocol.L30_PARENT_CONTROL,
            subcmd=protocol.L30_SUBCMD_TORQUE,
            payload=protocol.encode_u16_vector(
                values, minimum=L30_TORQUE_MIN, maximum=L30_TORQUE_MAX
            ),
            dlc=protocol.L30_VECTOR_DLC,
        )
        self._relay.push(TorqueData(torques=values, timestamp=time.time()))

    def set_all(self, torque: int) -> None:
        """Send the same torque target to all 17 joints.

        Args:
            torque: Torque target applied to every joint, between 60 and 800.

        Raises:
            ValidationError: If torque is outside the documented range.
        """
        self.set_torques([torque] * L30_JOINT_COUNT)

    def get_snapshot(self) -> TorqueData | None:
        """Return the latest commanded torque target.

        Returns:
            Cached TorqueData, or None if no torque target has been sent yet.
        """
        return self._relay.snapshot()

    def _set_event_sink(self, sink: Callable[[TorqueData], None]) -> None:
        self._relay.set_sink(sink)
