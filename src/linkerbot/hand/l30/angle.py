"""L30 protocol-native position control and sensing."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from linkerbot_transport.errors import ValidationError
from linkerbot_transport.relay import DataRelay

from . import protocol
from .client import L30Client
from .joints import L30Angle, validate_raw_command_values


@dataclass(frozen=True, slots=True)
class AngleData:
    """Immutable L30 protocol position data.

    Attributes:
        angles: 17 decoded signed protocol values in J1..J17 order.
        timestamp: Unix timestamp when the data was received or decoded.
    """

    angles: tuple[int, ...]
    timestamp: float


class AngleManager:
    """Manage L30 protocol-native target and measured joint positions."""

    def __init__(self, client: L30Client) -> None:
        """Initialize the angle manager.

        Args:
            client: L30 protocol client used for control, query, and report data.
        """
        self._client = client
        self._relay = DataRelay[AngleData]()
        self._client.add_report_handler(
            protocol.L30_SUBCMD_POSITION, self._on_periodic_report
        )

    def set_angles(self, angles: list[int] | tuple[int, ...]) -> None:
        """Send 17 protocol-native target positions.

        Args:
            angles: 17 protocol integers in J1..J17 order. Each value must be
                within the corresponding :data:`L30_JOINT_SPECS` range.

        Raises:
            ValidationError: If count, type, or a per-joint range is invalid.
        """
        validated = validate_raw_command_values(angles)
        self._send_angles(list(validated))

    def set_percentage_angles(self, angles: L30Angle | Sequence[int | float]) -> None:
        """Convert explicit 0..100 targets and send protocol values."""
        percentages = (
            angles if isinstance(angles, L30Angle) else L30Angle.from_list(angles)
        )
        self.set_angles(percentages.to_raw())

    def get_blocking(self, timeout_ms: float = 100) -> AngleData:
        """Read current joint angles and update the cached snapshot.

        Args:
            timeout_ms: Time to wait for the query response.

        Returns:
            AngleData containing decoded signed protocol values.

        Raises:
            ValidationError: If timeout_ms is not positive.
            TimeoutError: If no matching response is received before the timeout.
            ProtocolError: If the response status or payload shape is invalid.
        """
        if timeout_ms <= 0:
            raise ValidationError("timeout_ms must be positive")
        response = self._client.read(
            parent=protocol.L30_PARENT_QUERY,
            subcmd=protocol.L30_SUBCMD_POSITION,
            timeout_ms=timeout_ms,
        )
        data = AngleData(
            angles=protocol.decode_i16_response(response), timestamp=time.time()
        )
        self._relay.push(data)
        return data

    def get_snapshot(self) -> AngleData | None:
        """Return the latest cached angle data without sending a request.

        Returns:
            Cached AngleData, or None if no angle data has been received yet.
        """
        return self._relay.snapshot()

    def _send_angles(self, values: list[int]) -> None:
        self._client.send_no_ack(
            parent=protocol.L30_PARENT_CONTROL,
            subcmd=protocol.L30_SUBCMD_POSITION,
            payload=protocol.encode_i16_vector(tuple(values)),
            dlc=protocol.L30_VECTOR_DLC,
        )

    def _send_sense_request(self) -> None:
        self.get_blocking(timeout_ms=100)

    def _set_event_sink(self, sink: Callable[[AngleData], None]) -> None:
        self._relay.set_sink(sink)

    def close(self) -> None:
        """Unregister the periodic angle report handler."""
        self._client.remove_report_handler(
            protocol.L30_SUBCMD_POSITION, self._on_periodic_report
        )

    def _on_periodic_report(self, payload: bytes) -> None:
        values = protocol.decode_periodic_i16_report(
            payload, joint_mask=protocol.L30_PERIODIC_ALL_JOINTS_MASK
        )
        self._relay.push(
            AngleData(
                angles=values,
                timestamp=time.time(),
            )
        )
